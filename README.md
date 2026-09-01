# Minecraft Server Status

A Flask API and dashboard that checks any Minecraft server — **Java Edition**
(Server List Ping over TCP) and **Bedrock Edition** (RakNet unconnected ping over
UDP) — with SRV records, IPv4/IPv6, MOTD formatting, server icons, player samples
and latency history.

```
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python app.py            # http://localhost:5000
```

## Dashboard

- Java, Bedrock or auto-detect (tries Java, falls back to Bedrock).
- Server icon, MOTD rendered with its real Minecraft colors and styles.
- Stat tiles: players online with a capacity meter, latency with a sparkline,
  version, and availability across the checks made in this browser.
- **Latency** and **players online** charts built from repeat checks, with a
  crosshair tooltip, keyboard readout (focus a chart, then arrow keys), a range
  filter, optional auto-refresh, and a table view of the same numbers.
  Downtime shows as a gap in the line plus a red tick on the baseline.
- Deep links (`/?server=play.hypixel.net&edition=java`), recent servers, copyable
  address, light/dark theme, and `/` to jump to the search box.

History is stored per server in `localStorage` only — nothing is persisted
server-side.

## API

### `GET /api/status`

| Parameter | Default | Notes |
|---|---|---|
| `server` | required | `host`, `host:port`, `[ipv6]`, `[ipv6]:port` |
| `edition` | `auto` | `auto`, `java`, `bedrock` |
| `timeout` | `5` | seconds, clamped to 1–15 |
| `refresh` | `0` | `1` bypasses the response cache |

```bash
curl 'localhost:5000/api/status?server=play.hypixel.net'
```

```jsonc
{
  "online": true,
  "edition": "java",
  "hostname": "play.hypixel.net",
  "ip": "172.65.197.160",
  "port": 25565,
  "version": "Requires MC 1.8 / 1.21",
  "protocol": 47,
  "players": { "online": 25900, "max": 200000, "sample": [] },
  "motd": {
    "tokens": [{ "text": "Hypixel Network ", "color": "green" }],
    "clean": "Hypixel Network [1.8/26.2]"
  },
  "icon": "data:image/png;base64,…",
  "srv_record": null,
  "latency_ms": 214.3,
  "mods": null,
  "cached": false,
  "checked_at": 1788303932.42,
  "query_time_ms": 757.5
}
```

`motd.tokens` are flattened chat components: `text` plus optional `color`
(a Minecraft color name or `#rrggbb`) and `bold` / `italic` / `underlined` /
`strikethrough` / `obfuscated`. `motd.clean` is the same text without formatting.

**Status codes.** `400` invalid address or parameter, `403` blocked address,
`429` rate limited. A server that is simply unreachable is a valid answer and
returns `200` with `online: false`, an `error` message and an `error_code` of
`dns_failure`, `timeout`, `refused`, `unreachable` or `protocol_error`.

### `GET /api/health`

Liveness plus the active cache and rate-limit settings.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `PORT` | `5000` | HTTP port |
| `MCSTATUS_ALLOW_PRIVATE` | `0` | Allow private/loopback targets. Off by default so a public deployment can't be used to probe the internal network; set to `1` to check LAN servers. |
| `MCSTATUS_CACHE_TTL` | `8` | Seconds to reuse a status response. `0` disables. |
| `MCSTATUS_RATE_LIMIT` | `60` | Requests per window per client IP. `0` disables. |
| `MCSTATUS_RATE_WINDOW` | `60` | Rate-limit window, in seconds. |

## Layout

```
app.py                 Flask routes, TTL cache, rate limiting
mcping.py              DNS/SRV, Java SLP, Bedrock RakNet, MOTD parsing
templates/index.html   Dashboard markup
static/css/app.css     Design tokens and layout (light + dark)
static/js/app.js       Dashboard logic and the SVG charts (no dependencies)
```
