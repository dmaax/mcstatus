"""
Minecraft Server Status — a small Flask API with a built-in dashboard.

Java Edition (SRV + IPv4/IPv6) and Bedrock Edition, with MOTD formatting,
server icons, player samples and latency measured on the ping/pong round trip.
"""

from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from typing import Any, Dict, Optional, Tuple

from flask import Flask, jsonify, render_template, request

from mcping import DEFAULT_TIMEOUT, PingError, get_status

app = Flask(__name__)

ALLOW_PRIVATE = os.getenv("MCSTATUS_ALLOW_PRIVATE", "0").lower() in ("1", "true", "yes")
CACHE_TTL = float(os.getenv("MCSTATUS_CACHE_TTL", "8"))
RATE_LIMIT = int(os.getenv("MCSTATUS_RATE_LIMIT", "60"))       # requests…
RATE_WINDOW = float(os.getenv("MCSTATUS_RATE_WINDOW", "60"))   # …per this many seconds

# Errors that mean "we reached a verdict", as opposed to "the request was bad".
HTTP_STATUS_FOR_ERROR = {
    "invalid_host": 400,
    "blocked_host": 403,
    "dns_failure": 200,
    "timeout": 200,
    "refused": 200,
    "unreachable": 200,
    "protocol_error": 200,
}


class _TTLCache:
    def __init__(self, ttl: float, max_entries: int = 512):
        self.ttl = ttl
        self.max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: Dict[str, Tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        if self.ttl <= 0:
            return None
        with self._lock:
            entry = self._entries.get(key)
            if not entry:
                return None
            stored_at, value = entry
            if time.time() - stored_at > self.ttl:
                self._entries.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        if self.ttl <= 0:
            return
        with self._lock:
            if len(self._entries) >= self.max_entries:
                oldest = min(self._entries, key=lambda k: self._entries[k][0])
                self._entries.pop(oldest, None)
            self._entries[key] = (time.time(), value)


class _RateLimiter:
    def __init__(self, limit: int, window: float):
        self.limit = limit
        self.window = window
        self._lock = threading.Lock()
        self._hits: Dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        if self.limit <= 0:
            return True
        now = time.time()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True


cache = _TTLCache(CACHE_TTL)
limiter = _RateLimiter(RATE_LIMIT, RATE_WINDOW)


def client_key() -> str:
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",")[0].strip() or request.remote_addr or "unknown")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/status")
def api_status():
    server = (request.args.get("server") or "").strip()
    if not server:
        return jsonify({"error": "Missing 'server' parameter.",
                        "error_code": "missing_parameter"}), 400
    if len(server) > 300:
        return jsonify({"error": "Server address is too long.",
                        "error_code": "invalid_host"}), 400

    edition = (request.args.get("edition") or "auto").lower()
    if edition not in ("auto", "java", "bedrock"):
        return jsonify({"error": "edition must be auto, java or bedrock.",
                        "error_code": "invalid_parameter"}), 400

    try:
        timeout = float(request.args.get("timeout", DEFAULT_TIMEOUT))
    except ValueError:
        return jsonify({"error": "timeout must be a number.",
                        "error_code": "invalid_parameter"}), 400

    if not limiter.allow(client_key()):
        return jsonify({"error": "Too many requests. Slow down a little.",
                        "error_code": "rate_limited"}), 429

    fresh = request.args.get("refresh", "").lower() in ("1", "true", "yes")
    cache_key = f"{edition}|{server.lower()}"
    started = time.monotonic()

    payload = None if fresh else cache.get(cache_key)
    cached = payload is not None

    if payload is None:
        try:
            payload = get_status(server, edition=edition, timeout=timeout,
                                 allow_private=ALLOW_PRIVATE)
        except PingError as exc:
            status_code = HTTP_STATUS_FOR_ERROR.get(exc.code, 200)
            body = {
                "online": False,
                "hostname": server,
                "edition": edition,
                "error": exc.message,
                "error_code": exc.code,
                "checked_at": time.time(),
            }
            if status_code == 200:
                cache.set(cache_key, body)
            return jsonify({**body, "cached": False,
                            "query_time_ms": round((time.monotonic() - started) * 1000, 1)}), status_code
        payload["checked_at"] = time.time()
        cache.set(cache_key, payload)

    return jsonify({
        **payload,
        "cached": cached,
        "query_time_ms": round((time.monotonic() - started) * 1000, 1),
    })


@app.route("/api/health")
def api_health():
    return jsonify({"status": "ok", "cache_ttl": CACHE_TTL,
                    "rate_limit": {"limit": RATE_LIMIT, "window_seconds": RATE_WINDOW}})


@app.errorhandler(404)
def not_found(_error):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Unknown endpoint.", "error_code": "not_found"}), 404
    return render_template("index.html"), 404


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print("=" * 62)
    print("  Minecraft Server Status")
    print("=" * 62)
    print(f"  Dashboard : http://localhost:{port}/")
    print(f"  API       : http://localhost:{port}/api/status?server=<host>")
    print(f"  Private hosts allowed: {ALLOW_PRIVATE}")
    print("=" * 62)
    app.run(host="0.0.0.0", port=port)
