# Fritz LTE Monitor

macOS menu-bar app for AVM Fritzbox LTE routers (e.g. 6890 LTE). Polls signal metrics every 60 seconds via TR-064 and shows a live dashboard at **http://127.0.0.1:5433**.

![macOS 13+](https://img.shields.io/badge/macOS-13%2B-blue) ![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue)

---

## Features

- **Menu-bar indicator** 🟢/🟡/🔴 with current RSRP value
- **Live dashboard** with cards for RSRP, RSRQ, RSSI and cell load
- **Cell load** read directly from the Fritzbox (`Utilization` field), with RSRQ-based fallback
- **Carrier Aggregation** — secondary cell RSRP/RSRQ shown separately
- **Signal history** chart (6h / 24h / 3d / 7d)
- **Cell distance** for primary and secondary cell
- All data stored locally in SQLite — no cloud, no internet required
- Optional native macOS app build via py2app

---

## Dashboard

| Metric | Description | Good | Ok | Weak |
|--------|-------------|------|----|------|
| **RSRP** | Received signal strength (dBm) | ≥ −80 | ≥ −95 | < −95 |
| **RSRQ** | Signal quality — drops under load (dB) | ≥ −9 | ≥ −12 | < −12 |
| **Cell load** | Channel utilization % (direct or estimated) | < 40 % | < 65 % | ≥ 65 % |
| **RSSI** | Total received power incl. noise (dBm) | ≥ −80 | — | — |

Menu-bar icon meanings:
- 🟢 RSRP ≥ −80 dBm
- 🟡 RSRP −80 to −95 dBm
- 🔴 RSRP < −95 dBm
- ⚪ No connection / error

---

## Setup

### 1. Install dependencies

```bash
pip3 install rumps fritzconnection
```

### 2. Run

```bash
python3 fritz_monitor.py
```

The menu-bar icon appears in the top-right corner (⚪ on first start).

### 3. Configure Fritzbox access

Click the icon and set your credentials:

| Menu item | What to enter |
|-----------|---------------|
| **Set username…** | Your Fritzbox username (required if user management is enabled) |
| **Set password…** | Your Fritzbox password |
| **Change address…** | IP or hostname — default `fritz.box` works for most setups |

> **Username required?** Open the Fritzbox web UI → System → Fritzbox Users. If you see individual accounts listed (instead of a single password), you must set a username in the app. Without it, every poll attempt will fail with 401 and eventually trigger the Fritzbox's brute-force lockout.

After ~60 seconds the icon switches to 🟢/🟡/🔴 with the current RSRP value.

### 4. Open dashboard

Icon → **Open dashboard** or directly: http://127.0.0.1:5433

---

## Download

Pre-built `.app` bundles are available on the [Releases](../../releases) page.

> **Note:** The app is not signed with an Apple Developer certificate. On first launch macOS will block it — right-click the app → **Open** → **Open** to bypass Gatekeeper. This is only needed once.

---

## Build as native macOS app (optional)

```bash
pip install py2app
python3 create_icon.py
rm -rf build dist
python3 setup.py py2app
```

The finished app will be at `dist/Fritz LTE Monitor.app`.

---

## Autostart with launchd

```bash
# 1. Copy the plist
cp at.littleprinz.fritz-monitor.plist ~/Library/LaunchAgents/

# 2. Adjust paths (your username, path to python3, path to script)
nano ~/Library/LaunchAgents/at.littleprinz.fritz-monitor.plist

# 3. Load
launchctl load ~/Library/LaunchAgents/at.littleprinz.fritz-monitor.plist
```

To disable:
```bash
launchctl unload ~/Library/LaunchAgents/at.littleprinz.fritz-monitor.plist
```

---

## Data & files

All runtime files are stored in `~/.fritz_monitor/`:

| File | Contents |
|------|----------|
| `config.json` | Fritzbox address, username, password, poll interval |
| `signals.db` | SQLite database with complete signal history |
| `stdout.log` / `stderr.log` | Logs (launchd mode only) |

The database can be opened directly with [DB Browser for SQLite](https://sqlitebrowser.org).

---

## Troubleshooting

**401 Unauthorized on startup / works only after browser login:**

Your Fritzbox has user management enabled and requires a username. Set it via icon → **Set username…**. Without a username the app sends anonymous TR-064 requests that the Fritzbox rejects — repeated failures trigger the built-in brute-force lockout (BlockTime). The app now detects this and shows a clear error message instead of retrying blindly.

**Icon stays ⚪ / no data:**

Test Fritzbox connectivity:
```bash
python3 -c "
from fritzconnection import FritzConnection
fc = FritzConnection('fritz.box', user='YOUR_USER', password='YOUR_PW')
print(fc.call_action('X_AVM-DE_WANMobileConnection:1', 'GetInfoEx'))
"
```

**Cell load shows "from RSRQ" instead of "direct":**

The Fritzbox reports utilization in the `<Utilization>` XML tag. The `/api/debug` endpoint (http://127.0.0.1:5433/api/debug) shows the raw XML — if the tag name differs in your firmware version, adjust it in `_parse_cell()`.

**Debug endpoint:**

http://127.0.0.1:5433/api/debug — shows the last 5 raw entries from the database.

---

## Requirements

- macOS 13+
- Python 3.9+
- Fritzbox with LTE modem (tested with 6890 LTE)
- TR-064 enabled: **Home Network → Network → Home network sharing** (usually already active)

---

## License

MIT
