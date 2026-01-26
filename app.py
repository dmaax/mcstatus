"""
Minecraft Server Status API with Built-in Frontend
A Flask app to check Minecraft server status with SRV and IPv6 support
"""

from flask import Flask, jsonify, request, render_template_string
import socket
import struct
import json
import os
import dns.resolver
from typing import Optional, Dict, Any
import time

app = Flask(__name__)

# HTML Frontend Template
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MC Status Checker</title>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #1a4d2e 0%, #2d5016 50%, #1a3a1a 100%);
            min-height: 100vh;
            padding: 20px;
            color: #fff;
        }

        .container {
            max-width: 800px;
            margin: 0 auto;
        }

        .header {
            text-align: center;
            margin-bottom: 40px;
            animation: fadeIn 0.5s ease;
        }

        .header h1 {
            font-size: 3em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.5);
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 15px;
        }

        .header p {
            color: #a8e6a1;
            font-size: 1.1em;
        }

        .icon {
            width: 60px;
            height: 60px;
            background: #4ade80;
            border-radius: 8px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            box-shadow: 0 4px 6px rgba(0,0,0,0.3);
        }

        .search-box {
            background: rgba(0,0,0,0.3);
            backdrop-filter: blur(10px);
            border-radius: 12px;
            padding: 30px;
            margin-bottom: 30px;
            border: 1px solid rgba(74, 222, 128, 0.3);
            animation: slideUp 0.5s ease;
        }

        .input-group {
            display: flex;
            gap: 10px;
            margin-bottom: 10px;
        }

        input {
            flex: 1;
            padding: 15px 20px;
            font-size: 16px;
            border: 2px solid rgba(74, 222, 128, 0.5);
            background: rgba(0,0,0,0.4);
            color: #fff;
            border-radius: 8px;
            outline: none;
            transition: all 0.3s;
        }

        input:focus {
            border-color: #4ade80;
            background: rgba(0,0,0,0.6);
        }

        input::placeholder {
            color: #86efac;
        }

        button {
            padding: 15px 30px;
            font-size: 16px;
            font-weight: bold;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.3s;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .btn-check {
            background: #4ade80;
            color: #1a4d2e;
        }

        .btn-check:hover:not(:disabled) {
            background: #22c55e;
            transform: translateY(-2px);
            box-shadow: 0 6px 12px rgba(74, 222, 128, 0.4);
        }

        .btn-check:disabled {
            background: #6b7280;
            cursor: not-allowed;
        }

        .status-card {
            background: rgba(0,0,0,0.3);
            backdrop-filter: blur(10px);
            border-radius: 12px;
            padding: 30px;
            border: 1px solid rgba(74, 222, 128, 0.3);
            animation: slideUp 0.5s ease;
        }

        .status-header {
            display: flex;
            align-items: center;
            gap: 15px;
            margin-bottom: 25px;
            padding-bottom: 20px;
            border-bottom: 2px solid rgba(74, 222, 128, 0.2);
        }

        .status-icon {
            width: 50px;
            height: 50px;
            border-radius: 50%;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
        }

        .status-icon.online {
            background: #22c55e;
        }

        .status-icon.offline {
            background: #ef4444;
        }

        .status-info h2 {
            font-size: 1.8em;
            margin-bottom: 5px;
        }

        .status-info p {
            color: #a8e6a1;
            font-size: 0.9em;
        }

        .info-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }

        .info-item {
            background: rgba(0,0,0,0.3);
            padding: 15px;
            border-radius: 8px;
            border: 1px solid rgba(74, 222, 128, 0.2);
        }

        .info-label {
            color: #86efac;
            font-size: 0.85em;
            margin-bottom: 5px;
        }

        .info-value {
            font-size: 1.1em;
            font-weight: bold;
            font-family: 'Courier New', monospace;
        }

        .players-box {
            background: rgba(0,0,0,0.3);
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            border: 1px solid rgba(74, 222, 128, 0.2);
        }

        .players-count {
            font-size: 2em;
            font-weight: bold;
            color: #4ade80;
            margin-top: 10px;
        }

        .motd-box {
            background: rgba(0,0,0,0.3);
            padding: 20px;
            border-radius: 8px;
            border: 1px solid rgba(74, 222, 128, 0.2);
        }

        .error-box {
            background: rgba(239, 68, 68, 0.2);
            border: 1px solid #ef4444;
            padding: 20px;
            border-radius: 8px;
            color: #fca5a5;
            animation: slideUp 0.5s ease;
        }

        .loading {
            text-align: center;
            padding: 40px;
            animation: pulse 1.5s ease-in-out infinite;
        }

        .spinner {
            width: 50px;
            height: 50px;
            border: 4px solid rgba(74, 222, 128, 0.3);
            border-top-color: #4ade80;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin: 0 auto 20px;
        }

        @keyframes spin {
            to { transform: rotate(360deg); }
        }

        @keyframes fadeIn {
            from { opacity: 0; }
            to { opacity: 1; }
        }

        @keyframes slideUp {
            from {
                opacity: 0;
                transform: translateY(20px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }

        .api-info {
            text-align: center;
            margin-top: 30px;
            padding: 20px;
            background: rgba(0,0,0,0.2);
            border-radius: 8px;
            border: 1px solid rgba(74, 222, 128, 0.2);
        }

        .api-info code {
            background: rgba(0,0,0,0.4);
            padding: 5px 10px;
            border-radius: 4px;
            color: #4ade80;
            font-family: 'Courier New', monospace;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>
                <span class="icon">⛏️</span>
                MC Status Checker
            </h1>
            <p>Check any Minecraft server with SRV & IPv6 support</p>
        </div>

        <div class="search-box">
            <div class="input-group">
                <input
                    type="text"
                    id="serverInput"
                    placeholder="Enter server address (e.g., play.hypixel.net)"
                    value="play.hypixel.net"
                >
                <button class="btn-check" id="checkBtn">
                    <span>🔍</span>
                    Check Status
                </button>
            </div>
        </div>

        <div id="results"></div>

        <div class="api-info">
            <p style="color: #a8e6a1; margin-bottom: 10px;">API Endpoint:</p>
            <code>GET /api/status?server=hypixel.net</code>
        </div>
    </div>

    <script>
        const serverInput = document.getElementById('serverInput');
        const checkBtn = document.getElementById('checkBtn');
        const results = document.getElementById('results');

        async function checkServer() {
            const server = serverInput.value.trim();
            if (!server) return;

            checkBtn.disabled = true;
            results.innerHTML = `
                <div class="loading">
                    <div class="spinner"></div>
                    <p>Checking server status...</p>
                </div>
            `;

            try {
                const response = await fetch(`/api/status?server=${encodeURIComponent(server)}`);
                const data = await response.json();

                if (data.error) {
                    showError(data.error);
                } else {
                    showStatus(data);
                }
            } catch (error) {
                showError(`Failed to connect to API: ${error.message}`);
            } finally {
                checkBtn.disabled = false;
            }
        }

        function showStatus(data) {
            const statusClass = data.online ? 'online' : 'offline';
            const statusIcon = data.online ? '✓' : '✕';
            const statusText = data.online ? 'Online' : 'Offline';

            let html = `
                <div class="status-card">
                    <div class="status-header">
                        <div class="status-icon ${statusClass}">${statusIcon}</div>
                        <div class="status-info">
                            <h2>Server ${statusText}</h2>
                            <p>${escapeHtml(data.hostname)}</p>
                        </div>
                    </div>

                    <div class="info-grid">
                        <div class="info-item">
                            <div class="info-label">IP Address</div>
                            <div class="info-value">${escapeHtml(data.ip || 'N/A')}</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">Port</div>
                            <div class="info-value">${data.port}</div>
                        </div>
            `;

            if (data.online) {
                html += `
                        <div class="info-item">
                            <div class="info-label">Version</div>
                            <div class="info-value">${escapeHtml(data.version || 'N/A')}</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">Protocol</div>
                            <div class="info-value">${data.protocol || 'N/A'}</div>
                        </div>
                        <div class="info-item">
                            <div class="info-label">Latency</div>
                            <div class="info-value">${data.latency_ms ? data.latency_ms + 'ms' : 'N/A'}</div>
                        </div>
                `;

                if (data.srv_record) {
                    html += `
                        <div class="info-item">
                            <div class="info-label">SRV Record</div>
                            <div class="info-value" style="font-size: 0.85em;">${escapeHtml(data.srv_record)}</div>
                        </div>
                    `;
                }

                html += `</div>`;

                if (data.players && (data.players.online !== null || data.players.max !== null)) {
                    html += `
                        <div class="players-box">
                            <div class="info-label">👥 Players Online</div>
                            <div class="players-count">${data.players.online || 0} / ${data.players.max || '?'}</div>
                        </div>
                    `;
                }

                if (data.motd) {
                    html += `
                        <div class="motd-box">
                            <div class="info-label">📝 Message of the Day</div>
                            <div style="margin-top: 10px; line-height: 1.5;">${escapeHtml(data.motd)}</div>
                        </div>
                    `;
                }
            } else {
                html += `</div>`;
            }

            html += `</div>`;
            results.innerHTML = html;
        }

        function showError(message) {
            results.innerHTML = `
                <div class="error-box">
                    <strong>❌ Error</strong>
                    <p style="margin-top: 10px;">${escapeHtml(message)}</p>
                </div>
            `;
        }

        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        checkBtn.addEventListener('click', checkServer);
        serverInput.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') checkServer();
        });
    </script>
</body>
</html>
"""

class MinecraftServerStatus:
    def __init__(self, host: str, port: int = 25565):
        self.host = host
        self.port = port
        self.srv_record = None
        self.resolved_ip = None

    def resolve_srv(self) -> tuple[str, int]:
        """Resolve SRV record for Minecraft server"""
        try:
            srv_domain = f"_minecraft._tcp.{self.host}"
            answers = dns.resolver.resolve(srv_domain, 'SRV')
            if answers:
                srv = answers[0]
                self.srv_record = f"{srv.target} (port {srv.port}, priority {srv.priority})"
                return str(srv.target).rstrip('.'), srv.port
        except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer, Exception):
            pass
        return self.host, self.port

    def resolve_address(self, hostname: str) -> Optional[str]:
        """Resolve hostname to IP (supports both IPv4 and IPv6)"""
        try:
            # Try IPv4 first
            try:
                info = socket.getaddrinfo(hostname, None, socket.AF_INET)
                if info:
                    self.resolved_ip = info[0][4][0]
                    return self.resolved_ip
            except socket.gaierror:
                pass

            # Try IPv6
            try:
                info = socket.getaddrinfo(hostname, None, socket.AF_INET6)
                if info:
                    self.resolved_ip = info[0][4][0]
                    return self.resolved_ip
            except socket.gaierror:
                pass

        except Exception as e:
            print(f"Resolution error: {e}")
        return None

    def pack_varint(self, value: int) -> bytes:
        """Pack integer as varint"""
        result = b''
        while True:
            temp = value & 0x7F
            value >>= 7
            if value != 0:
                result += struct.pack('B', temp | 0x80)
            else:
                result += struct.pack('B', temp)
                break
        return result

    def unpack_varint(self, sock: socket.socket) -> int:
        """Unpack varint from socket"""
        result = 0
        for i in range(5):
            data = sock.recv(1)
            if not data:
                raise ConnectionError("Connection closed")
            byte = struct.unpack('B', data)[0]
            result |= (byte & 0x7F) << (7 * i)
            if not byte & 0x80:
                break
        return result

    def pack_data(self, data: bytes) -> bytes:
        """Pack data with length prefix"""
        return self.pack_varint(len(data)) + data

    def pack_string(self, text: str) -> bytes:
        """Pack string with length prefix"""
        encoded = text.encode('utf-8')
        return self.pack_varint(len(encoded)) + encoded

    def get_status(self) -> Dict[str, Any]:
        """Get server status using Server List Ping protocol"""
        result = {
            "online": False,
            "hostname": self.host,
            "ip": None,
            "port": self.port,
            "version": None,
            "protocol": None,
            "players": {
                "online": None,
                "max": None
            },
            "motd": None,
            "srv_record": None,
            "latency_ms": None
        }

        # Resolve SRV record
        resolved_host, resolved_port = self.resolve_srv()
        result["srv_record"] = self.srv_record
        result["port"] = resolved_port

        # Resolve IP address
        ip = self.resolve_address(resolved_host)
        if not ip:
            return result

        result["ip"] = ip

        try:
            # Determine socket family based on IP version
            if ':' in ip:
                sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
                addr = (ip, resolved_port, 0, 0)
            else:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                addr = (ip, resolved_port)

            sock.settimeout(5)

            # Measure latency
            start_time = time.time()
            sock.connect(addr)

            # Send handshake packet
            handshake = b'\x00'  # Packet ID
            handshake += self.pack_varint(47)  # Protocol version (1.8+)
            handshake += self.pack_string(resolved_host)
            handshake += struct.pack('>H', resolved_port)
            handshake += self.pack_varint(1)  # Next state: status

            sock.sendall(self.pack_data(handshake))

            # Send status request
            sock.sendall(self.pack_data(b'\x00'))

            # Read response
            length = self.unpack_varint(sock)
            packet_id = self.unpack_varint(sock)

            if packet_id == 0:
                json_length = self.unpack_varint(sock)
                json_data = b''
                while len(json_data) < json_length:
                    chunk = sock.recv(json_length - len(json_data))
                    if not chunk:
                        break
                    json_data += chunk

                # Send ping
                ping_time = int(time.time() * 1000)
                ping_packet = b'\x01' + struct.pack('>Q', ping_time)
                sock.sendall(self.pack_data(ping_packet))

                # Receive pong
                self.unpack_varint(sock)  # Length
                pong_id = self.unpack_varint(sock)

                end_time = time.time()
                latency = int((end_time - start_time) * 1000)

                # Parse JSON response
                status_data = json.loads(json_data.decode('utf-8'))

                result["online"] = True
                result["latency_ms"] = latency

                if 'version' in status_data:
                    result["version"] = status_data['version'].get('name')
                    result["protocol"] = status_data['version'].get('protocol')

                if 'players' in status_data:
                    result["players"]["online"] = status_data['players'].get('online')
                    result["players"]["max"] = status_data['players'].get('max')

                if 'description' in status_data:
                    desc = status_data['description']
                    if isinstance(desc, dict):
                        result["motd"] = desc.get('text', '')
                    else:
                        result["motd"] = str(desc)

            sock.close()

        except Exception as e:
            print(f"Error checking server: {e}")

        return result

@app.route('/')
def index():
    """Serve the frontend"""
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/status', methods=['GET'])
def check_status():
    """API endpoint to check server status"""
    server = request.args.get('server')

    if not server:
        return jsonify({"error": "Missing 'server' parameter"}), 400

    # Parse host and port
    if ':' in server and not server.startswith('['):
        parts = server.rsplit(':', 1)
        try:
            host = parts[0]
            port = int(parts[1])
        except (ValueError, IndexError):
            host = server
            port = 25565
    else:
        host = server.strip('[]')
        port = 25565

    checker = MinecraftServerStatus(host, port)
    status = checker.get_status()

    return jsonify(status)

if __name__ == '__main__':
    print("=" * 60)
    print("🎮 Minecraft Server Status Checker")
    print("=" * 60)
    print("Frontend: http://localhost:5000")
    print("API: http://localhost:5000/api/status?server=<hostname>")
    print("=" * 60)
    port = int(os.getenv("PORT", 5000))
    app.run(host='0.0.0.0', port=port)

