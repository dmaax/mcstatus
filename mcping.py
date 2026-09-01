"""
Minecraft server pinging: Java Edition (Server List Ping over TCP) and
Bedrock Edition (RakNet unconnected ping over UDP).

Handles SRV resolution, IPv4/IPv6, MOTD component parsing and legacy color
codes. Every failure surfaces as a PingError carrying a stable machine code.
"""

from __future__ import annotations

import ipaddress
import json
import re
import socket
import struct
import time
from typing import Any, Dict, List, Optional, Tuple

import dns.resolver

DEFAULT_JAVA_PORT = 25565
DEFAULT_BEDROCK_PORT = 19132
DEFAULT_TIMEOUT = 5.0
HANDSHAKE_PROTOCOL = 47  # 1.8+; servers ignore this for status requests

# RakNet "offline message data ID" magic
RAKNET_MAGIC = b"\x00\xff\xff\x00\xfe\xfe\xfe\xfe\xfd\xfd\xfd\xfd\x12\x34\x56\x78"

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)(?!-)[A-Za-z0-9-_]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-_]{1,63}(?<!-))*\.?$"
)

# Legacy section-sign formatting codes -> the colors Minecraft renders them as.
LEGACY_COLORS = {
    "0": "black", "1": "dark_blue", "2": "dark_green", "3": "dark_aqua",
    "4": "dark_red", "5": "dark_purple", "6": "gold", "7": "gray",
    "8": "dark_gray", "9": "blue", "a": "green", "b": "aqua",
    "c": "red", "d": "light_purple", "e": "yellow", "f": "white",
}
LEGACY_STYLES = {
    "k": "obfuscated", "l": "bold", "m": "strikethrough",
    "n": "underlined", "o": "italic",
}


class PingError(Exception):
    """A ping that failed for a reason worth showing the user."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


# --------------------------------------------------------------------------
# Address handling
# --------------------------------------------------------------------------

def split_host_port(server: str) -> Tuple[str, Optional[int]]:
    """Split "host", "host:port", "[::1]" or "[::1]:port" into parts."""
    server = server.strip()
    if not server:
        raise PingError("invalid_host", "No server address given.")

    if server.startswith("["):
        end = server.find("]")
        if end == -1:
            raise PingError("invalid_host", "Unbalanced brackets in IPv6 address.")
        host = server[1:end]
        rest = server[end + 1:]
        if rest.startswith(":"):
            return host, _parse_port(rest[1:])
        if rest:
            raise PingError("invalid_host", "Unexpected characters after IPv6 address.")
        return host, None

    if server.count(":") == 1:
        host, _, port = server.partition(":")
        return host, _parse_port(port)

    # Several colons and no brackets: a bare IPv6 literal.
    return server, None


def _parse_port(raw: str) -> int:
    try:
        port = int(raw)
    except ValueError:
        raise PingError("invalid_host", f"'{raw}' is not a valid port.")
    if not 1 <= port <= 65535:
        raise PingError("invalid_host", "Port must be between 1 and 65535.")
    return port


def validate_host(host: str) -> None:
    if len(host) > 253:
        raise PingError("invalid_host", "Hostname is too long.")
    try:
        ipaddress.ip_address(host)
        return
    except ValueError:
        pass
    if not _HOSTNAME_RE.match(host):
        raise PingError("invalid_host", f"'{host}' is not a valid hostname.")


def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_multicast or addr.is_reserved or addr.is_unspecified
    )


def resolve_srv(host: str, timeout: float = 3.0) -> Optional[Dict[str, Any]]:
    """Look up _minecraft._tcp.<host>. Returns None when there is no record."""
    try:
        resolver = dns.resolver.Resolver()
        resolver.lifetime = timeout
        answers = resolver.resolve(f"_minecraft._tcp.{host}", "SRV")
    except Exception:
        return None
    records = sorted(answers, key=lambda r: (r.priority, -r.weight))
    if not records:
        return None
    srv = records[0]
    return {
        "target": str(srv.target).rstrip("."),
        "port": srv.port,
        "priority": srv.priority,
        "weight": srv.weight,
    }


def resolve_addresses(host: str, port: int, udp: bool = False) -> List[Tuple[int, str]]:
    """Resolve to [(family, ip), ...], IPv4 first, deduplicated."""
    kind = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_UNSPEC, kind)
    except socket.gaierror:
        raise PingError("dns_failure", f"Could not resolve '{host}'.")
    seen, out = set(), []
    for family, _, _, _, sockaddr in infos:
        ip = sockaddr[0]
        # ::ffff:1.2.3.4 is the same host as 1.2.3.4 — don't spend an attempt twice.
        if ip.lower().startswith("::ffff:") and ip[7:] in seen:
            continue
        if ip in seen:
            continue
        seen.add(ip)
        out.append((family, ip))
    out.sort(key=lambda item: item[0] != socket.AF_INET)
    if not out:
        raise PingError("dns_failure", f"Could not resolve '{host}'.")
    return out


# --------------------------------------------------------------------------
# Java Edition — Server List Ping
# --------------------------------------------------------------------------

def _pack_varint(value: int) -> bytes:
    out = b""
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out += struct.pack("B", byte | 0x80)
        else:
            return out + struct.pack("B", byte)


def _pack_string(text: str) -> bytes:
    encoded = text.encode("utf-8")
    return _pack_varint(len(encoded)) + encoded


class _SocketReader:
    """Buffered reader so varints don't cost one syscall per byte."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buf = bytearray()

    def read(self, count: int) -> bytes:
        while len(self.buf) < count:
            chunk = self.sock.recv(max(4096, count - len(self.buf)))
            if not chunk:
                raise PingError("protocol_error", "Server closed the connection early.")
            self.buf += chunk
        out = bytes(self.buf[:count])
        del self.buf[:count]
        return out

    def read_varint(self) -> int:
        result = 0
        for i in range(5):
            byte = self.read(1)[0]
            result |= (byte & 0x7F) << (7 * i)
            if not byte & 0x80:
                return result
        raise PingError("protocol_error", "Malformed varint in server response.")


def ping_java(host: str, port: int, ip: str, family: int, timeout: float) -> Dict[str, Any]:
    """Handshake + status request + ping. Returns the raw status JSON and RTT."""
    addr = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
    sock = socket.socket(family, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        connect_started = time.monotonic()
        sock.connect(addr)
        connect_ms = (time.monotonic() - connect_started) * 1000

        handshake = (
            b"\x00"
            + _pack_varint(HANDSHAKE_PROTOCOL)
            + _pack_string(host)
            + struct.pack(">H", port)
            + _pack_varint(1)
        )
        sock.sendall(_pack_varint(len(handshake)) + handshake)
        sock.sendall(_pack_varint(1) + b"\x00")  # status request

        reader = _SocketReader(sock)
        reader.read_varint()  # packet length
        if reader.read_varint() != 0:
            raise PingError("protocol_error", "Unexpected packet from server.")
        payload = reader.read(reader.read_varint())

        # Ping/pong measures round-trip time on the established connection.
        token = int(time.time() * 1000) & 0x7FFFFFFFFFFFFFFF
        ping_packet = b"\x01" + struct.pack(">Q", token)
        ping_started = time.monotonic()
        sock.sendall(_pack_varint(len(ping_packet)) + ping_packet)
        try:
            reader.read_varint()
            reader.read_varint()
            reader.read(8)
            latency_ms = (time.monotonic() - ping_started) * 1000
        except (PingError, OSError):
            latency_ms = connect_ms  # some proxies skip pong; fall back

        try:
            data = json.loads(payload.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            raise PingError("protocol_error", "Server sent a malformed status response.")
        if not isinstance(data, dict):
            raise PingError("protocol_error", "Server sent an unexpected status response.")

        return {
            "raw": data,
            "latency_ms": round(latency_ms, 1),
            "connect_ms": round(connect_ms, 1),
        }
    except socket.timeout:
        raise PingError("timeout", f"'{host}' did not answer within {timeout:.0f}s.")
    except ConnectionRefusedError:
        raise PingError("refused", f"Connection refused on port {port}.")
    except OSError as exc:
        raise PingError("unreachable", f"Could not reach {ip}:{port} ({exc.strerror or exc}).")
    finally:
        sock.close()


# --------------------------------------------------------------------------
# Bedrock Edition — RakNet unconnected ping
# --------------------------------------------------------------------------

def ping_bedrock(host: str, port: int, ip: str, family: int, timeout: float) -> Dict[str, Any]:
    addr = (ip, port, 0, 0) if family == socket.AF_INET6 else (ip, port)
    sock = socket.socket(family, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        packet = (
            b"\x01"
            + struct.pack(">Q", int(time.monotonic() * 1000) & 0xFFFFFFFFFFFFFFFF)
            + RAKNET_MAGIC
            + struct.pack(">Q", 0x0000000012345678)
        )
        started = time.monotonic()
        sock.sendto(packet, addr)
        data, _ = sock.recvfrom(4096)
        latency_ms = (time.monotonic() - started) * 1000

        if not data or data[0] != 0x1C:
            raise PingError("protocol_error", "Not a Bedrock server response.")
        # 1 id + 8 ping time + 8 server GUID + 16 magic, then a length-prefixed string
        offset = 1 + 8 + 8 + 16
        if len(data) < offset + 2:
            raise PingError("protocol_error", "Truncated Bedrock response.")
        (str_len,) = struct.unpack(">H", data[offset:offset + 2])
        text = data[offset + 2:offset + 2 + str_len].decode("utf-8", errors="replace")
        return {"fields": text.split(";"), "latency_ms": round(latency_ms, 1)}
    except socket.timeout:
        raise PingError("timeout", f"'{host}' did not answer on UDP within {timeout:.0f}s.")
    except OSError as exc:
        raise PingError("unreachable", f"Could not reach {ip}:{port} ({exc.strerror or exc}).")
    finally:
        sock.close()


# --------------------------------------------------------------------------
# MOTD parsing
# --------------------------------------------------------------------------

def _split_legacy(text: str, base: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Turn a string containing § codes into styled tokens."""
    tokens: List[Dict[str, Any]] = []
    state = dict(base)
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf:
            tokens.append({**state, "text": buf})
            buf = ""

    i = 0
    while i < len(text):
        char = text[i]
        if char == "§" and i + 1 < len(text):
            code = text[i + 1].lower()
            if code in LEGACY_COLORS:
                flush()
                state = {**base, "color": LEGACY_COLORS[code]}
            elif code in LEGACY_STYLES:
                flush()
                state = {**state, LEGACY_STYLES[code]: True}
            elif code == "r":
                flush()
                state = dict(base)
            else:
                buf += text[i:i + 2]
                i += 2
                continue
            i += 2
            continue
        buf += char
        i += 1
    flush()
    return tokens


def parse_description(desc: Any, inherited: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """Flatten a chat component (or legacy string) into styled text tokens."""
    base: Dict[str, Any] = dict(inherited or {})
    if desc is None:
        return []
    if isinstance(desc, str):
        return _split_legacy(desc, base)
    if isinstance(desc, list):
        tokens: List[Dict[str, Any]] = []
        for item in desc:
            tokens.extend(parse_description(item, base))
        return tokens
    if not isinstance(desc, dict):
        return _split_legacy(str(desc), base)

    for key in ("bold", "italic", "underlined", "strikethrough", "obfuscated"):
        if key in desc:
            base[key] = bool(desc[key])
    if isinstance(desc.get("color"), str):
        base["color"] = desc["color"]

    tokens = []
    text = desc.get("text")
    if not isinstance(text, str):
        text = desc.get("translate") if isinstance(desc.get("translate"), str) else ""
    if text:
        tokens.extend(_split_legacy(text, base))
    for child in desc.get("extra", []) or []:
        tokens.extend(parse_description(child, base))
    return tokens


def tokens_to_text(tokens: List[Dict[str, Any]]) -> str:
    return "".join(token.get("text", "") for token in tokens)


# --------------------------------------------------------------------------
# High-level status
# --------------------------------------------------------------------------

def _blank_result(host: str, port: int, edition: str) -> Dict[str, Any]:
    return {
        "online": False,
        "edition": edition,
        "hostname": host,
        "ip": None,
        "port": port,
        "version": None,
        "protocol": None,
        "software": None,
        "players": {"online": None, "max": None, "sample": []},
        "motd": {"tokens": [], "clean": None},
        "icon": None,
        "srv_record": None,
        "latency_ms": None,
        "mods": None,
        "gamemode": None,
        "map": None,
        "error": None,
        "error_code": None,
    }


def _remaining(deadline: Optional[float], timeout: float) -> Optional[float]:
    """Per-attempt timeout, clipped to whatever is left of the overall budget."""
    if deadline is None:
        return timeout
    left = deadline - time.monotonic()
    if left < 0.5:
        return None
    return min(timeout, left)


def _java_status(host: str, port: int, explicit_port: bool, timeout: float,
                 allow_private: bool, deadline: Optional[float] = None) -> Dict[str, Any]:
    result = _blank_result(host, port, "java")

    target_host, target_port = host, port
    if not explicit_port:
        srv = resolve_srv(host, timeout=min(timeout, 3.0))
        if srv:
            result["srv_record"] = srv
            target_host, target_port = srv["target"], srv["port"]
    result["port"] = target_port

    addresses = resolve_addresses(target_host, target_port)
    if not allow_private and not any(is_public_ip(ip) for _, ip in addresses):
        raise PingError("blocked_host", "Private and loopback addresses are not allowed.")

    last_error: Optional[PingError] = None
    for family, ip in addresses[:3]:
        if not allow_private and not is_public_ip(ip):
            continue
        attempt_timeout = _remaining(deadline, timeout)
        if attempt_timeout is None:
            break
        try:
            ping = ping_java(target_host, target_port, ip, family, attempt_timeout)
        except PingError as exc:
            last_error = exc
            continue
        result["ip"] = ip
        result["online"] = True
        result["latency_ms"] = ping["latency_ms"]
        _apply_java_payload(result, ping["raw"])
        return result

    raise last_error or PingError("unreachable", f"Could not reach '{target_host}'.")


def _apply_java_payload(result: Dict[str, Any], data: Dict[str, Any]) -> None:
    version = data.get("version") or {}
    if isinstance(version, dict):
        result["version"] = version.get("name")
        result["protocol"] = version.get("protocol")

    players = data.get("players") or {}
    if isinstance(players, dict):
        result["players"]["online"] = players.get("online")
        result["players"]["max"] = players.get("max")
        sample = players.get("sample")
        if isinstance(sample, list):
            result["players"]["sample"] = [
                {"name": entry.get("name"), "id": entry.get("id")}
                for entry in sample[:24]
                if isinstance(entry, dict) and entry.get("name")
            ]

    tokens = parse_description(data.get("description"))
    result["motd"] = {"tokens": tokens, "clean": tokens_to_text(tokens) or None}

    icon = data.get("favicon")
    if isinstance(icon, str) and icon.startswith("data:image/png;base64,") and len(icon) < 200_000:
        result["icon"] = icon

    mod_info = data.get("modinfo")
    forge = data.get("forgeData")
    if isinstance(mod_info, dict) and isinstance(mod_info.get("modList"), list):
        result["mods"] = {
            "type": mod_info.get("type") or "FML",
            "count": len(mod_info["modList"]),
        }
    elif isinstance(forge, dict) and isinstance(forge.get("mods"), list):
        result["mods"] = {"type": "Forge", "count": len(forge["mods"])}


def _bedrock_status(host: str, port: int, timeout: float,
                    allow_private: bool, deadline: Optional[float] = None) -> Dict[str, Any]:
    result = _blank_result(host, port, "bedrock")
    addresses = resolve_addresses(host, port, udp=True)
    if not allow_private and not any(is_public_ip(ip) for _, ip in addresses):
        raise PingError("blocked_host", "Private and loopback addresses are not allowed.")

    last_error: Optional[PingError] = None
    for family, ip in addresses[:4]:
        if not allow_private and not is_public_ip(ip):
            continue
        attempt_timeout = _remaining(deadline, min(timeout, 2.5))
        if attempt_timeout is None:
            break
        try:
            ping = ping_bedrock(host, port, ip, family, attempt_timeout)
        except PingError as exc:
            last_error = exc
            continue
        fields = ping["fields"]

        def field(index: int) -> Optional[str]:
            value = fields[index] if len(fields) > index else None
            return value or None

        result["ip"] = ip
        result["online"] = True
        result["latency_ms"] = ping["latency_ms"]
        result["software"] = field(0)
        result["version"] = field(3)
        result["protocol"] = int(field(2)) if (field(2) or "").isdigit() else None
        result["players"]["online"] = int(field(4)) if (field(4) or "").isdigit() else None
        result["players"]["max"] = int(field(5)) if (field(5) or "").isdigit() else None
        result["map"] = field(7)
        result["gamemode"] = field(8)

        motd_line = field(1) or ""
        second_line = field(7) if (field(0) or "").startswith("MCPE") else None
        text = motd_line if not second_line else f"{motd_line}\n{second_line}"
        tokens = parse_description(text)
        result["motd"] = {"tokens": tokens, "clean": tokens_to_text(tokens) or None}
        return result

    raise last_error or PingError("unreachable", f"Could not reach '{host}'.")


def get_status(server: str, edition: str = "auto", timeout: float = DEFAULT_TIMEOUT,
               allow_private: bool = False) -> Dict[str, Any]:
    """Ping a server. `edition` is "java", "bedrock" or "auto" (Java, then Bedrock)."""
    host, port = split_host_port(server)
    host = host.strip().rstrip(".")
    validate_host(host)
    explicit_port = port is not None
    timeout = max(1.0, min(timeout, 15.0))

    if edition == "bedrock":
        return _bedrock_status(host, port or DEFAULT_BEDROCK_PORT, timeout, allow_private,
                               time.monotonic() + timeout * 1.5)

    # Trying several A/AAAA records must not multiply the wait: one budget covers
    # every attempt, and in auto mode Bedrock gets a short slice of its own.
    java_port = port or DEFAULT_JAVA_PORT
    try:
        return _java_status(host, java_port, explicit_port, timeout, allow_private,
                            time.monotonic() + timeout * 1.5)
    except PingError as java_error:
        if edition != "auto" or java_error.code in ("invalid_host", "blocked_host", "dns_failure"):
            raise
        bedrock_timeout = min(timeout, 3.5)
        try:
            return _bedrock_status(host, port or DEFAULT_BEDROCK_PORT, bedrock_timeout,
                                   allow_private, time.monotonic() + bedrock_timeout * 1.2)
        except PingError:
            raise java_error
