#!/usr/bin/env python3
"""
Fritz LTE Monitor
Menu-Bar-App für macOS – loggt LTE-Signalqualität der Fritzbox 6890 LTE
und stellt ein lokales Web-Dashboard unter http://127.0.0.1:5433 bereit.
"""

import threading
import sqlite3
import time
import os
import json
import webbrowser
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import urllib.request
import urllib.parse as urlparse_mod

import AppKit
from Foundation import NSOperationQueue

try:
    import rumps
except ImportError:
    raise SystemExit("Fehlt: pip install rumps")

try:
    from fritzconnection import FritzConnection
except ImportError:
    raise SystemExit("Fehlt: pip install fritzconnection")


# ── Pfade & Konstanten ────────────────────────────────────────────────────────

CONFIG_DIR    = os.path.expanduser("~/.fritz_monitor")
CONFIG_FILE   = os.path.join(CONFIG_DIR, "config.json")
DB_FILE       = os.path.join(CONFIG_DIR, "signals.db")
WEB_PORT      = 5433
POLL_INTERVAL = 60  # Sekunden

DEFAULT_CONFIG = {
    "fritz_address":  "fritz.box",
    "fritz_username": "",
    "fritz_password": "",
    "poll_interval":  60,
}

LTE_SERVICE = "X_AVM-DE_WANMobileConnection1"


# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
        for k, v in DEFAULT_CONFIG.items():
            cfg.setdefault(k, v)
        return cfg
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


# ── Datenbank ─────────────────────────────────────────────────────────────────

def init_db():
    os.makedirs(CONFIG_DIR, exist_ok=True)
    con = sqlite3.connect(DB_FILE)
    con.execute("""
        CREATE TABLE IF NOT EXISTS signal (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            ts         TEXT NOT NULL,
            rsrp       REAL,
            rsrq       REAL,
            rssi       REAL,
            rsrp2      REAL,
            rsrq2      REAL,
            rssi2      REAL,
            distance   INTEGER,
            distance2  INTEGER,
            nutzung    INTEGER,
            nutzung2   INTEGER,
            band       TEXT,
            cell_id    TEXT,
            provider   TEXT,
            standard   TEXT,
            raw_json   TEXT
        )
    """)
    # Neue Spalten für bestehende DBs nachrüsten
    for col, typedef in [("rsrp2","REAL"), ("rsrq2","REAL"), ("rssi","REAL"), ("rssi2","REAL"),
                         ("distance","INTEGER"), ("distance2","INTEGER"),
                         ("nutzung","INTEGER"), ("nutzung2","INTEGER")]:
        try:
            con.execute(f"ALTER TABLE signal ADD COLUMN {col} {typedef}")
        except sqlite3.OperationalError:
            pass
    con.commit()
    con.close()


def insert_record(data: dict):
    con = sqlite3.connect(DB_FILE)
    con.execute("""
        INSERT INTO signal
            (ts, rsrp, rsrq, rssi, rsrp2, rsrq2, rssi2, distance, distance2,
             nutzung, nutzung2, band, cell_id, provider, standard, raw_json)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        datetime.now().isoformat(timespec="seconds"),
        data.get("rsrp"),
        data.get("rsrq"),
        data.get("rssi"),
        data.get("rsrp2"),
        data.get("rsrq2"),
        data.get("rssi2"),
        data.get("distance"),
        data.get("distance2"),
        data.get("nutzung"),
        data.get("nutzung2"),
        data.get("band"),
        data.get("cell_id"),
        data.get("provider"),
        data.get("standard"),
        json.dumps(data.get("raw", {}), default=str),
    ))
    con.commit()
    con.close()


def query_latest() -> dict:
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM signal ORDER BY ts DESC LIMIT 1").fetchone()
    con.close()
    return dict(row) if row else {}


def query_history(hours: int = 24) -> list:
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT ts, rsrp, rsrq, rssi, rsrp2, rsrq2, distance, distance2, nutzung, nutzung2, band, cell_id, provider
        FROM signal
        WHERE ts >= datetime('now', ?)
        ORDER BY ts ASC
    """, (f"-{hours} hours",)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def query_all_raw() -> list:
    """Für Debug: alle gespeicherten Rohfelder."""
    con = sqlite3.connect(DB_FILE)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT raw_json FROM signal ORDER BY ts DESC LIMIT 5").fetchall()
    con.close()
    return [json.loads(r["raw_json"] or "{}") for r in rows]


# ── Fritzbox Datenabruf ───────────────────────────────────────────────────────

def _fritz_ensure_session(address: str, username: str, password: str) -> tuple[bool, int]:
    """Stellt eine aktive Fritz!Box-Web-Session sicher (SID-Login).
    Gibt (success, block_time_sekunden) zurück.
    Benötigt Benutzername wenn die Fritz!Box Benutzerverwaltung aktiviert hat."""
    base = f"http://{address}"
    try:
        with urllib.request.urlopen(f"{base}/login_sid.lua?version=2", timeout=10) as resp:
            xml = resp.read().decode()
        root = ET.fromstring(xml)
        sid = root.findtext("SID", "0000000000000000")
        if sid != "0000000000000000":
            return True, 0  # Session noch aktiv

        block_time = int(root.findtext("BlockTime", "0") or "0")
        if block_time > 0:
            return False, block_time  # Gesperrt – nicht nochmal versuchen

        challenge = root.findtext("Challenge", "")

        # Fritz!OS >= 7.24: PBKDF2 (Challenge beginnt mit "2$")
        if challenge.startswith("2$"):
            parts = challenge.split("$")
            iter1, salt1 = int(parts[1]), bytes.fromhex(parts[2])
            iter2, salt2 = int(parts[3]), bytes.fromhex(parts[4])
            h1 = hashlib.pbkdf2_hmac("sha256", password.encode(), salt1, iter1)
            h2 = hashlib.pbkdf2_hmac("sha256", h1, salt2, iter2)
            response = f"{challenge}${h2.hex()}"
        else:
            # Ältere Fritz!OS: MD5-Challenge
            to_hash = f"{challenge}-{password}"
            response = f"{challenge}-{hashlib.md5(to_hash.encode('utf-16-le')).hexdigest()}"

        data = urlparse_mod.urlencode({"username": username, "response": response}).encode()
        req = urllib.request.Request(f"{base}/login_sid.lua?version=2", data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            xml = resp.read().decode()
        root = ET.fromstring(xml)
        sid = root.findtext("SID", "0000000000000000")
        new_block = int(root.findtext("BlockTime", "0") or "0")
        return sid != "0000000000000000", new_block
    except Exception:
        return False, 0


def _parse_cell(cell_el) -> dict:
    def _f(tag):
        v = cell_el.findtext(tag)
        try:
            val = float(v)
            return val if val != 0.0 else None  # 0 ist kein gültiger RSRP/RSRQ-Wert
        except (TypeError, ValueError):
            return None
    def _i(tag):
        v = cell_el.findtext(tag)
        try: return int(v)
        except (TypeError, ValueError): return None
    rssi_raw = _i("Rssi")
    return {
        "rsrp":     _f("RSRP"),
        "rsrq":     _f("Rsrq"),
        "rssi":     -rssi_raw if rssi_raw is not None else None,  # AVM liefert positiven Absolutwert
        "distance": _i("Distance"),
        "provider": cell_el.findtext("Provider") or "",
        "cell_id":  cell_el.findtext("Cellid") or "",
        "nutzung":  _i("Utilization"),  # Fritz!Box-Zellenauslastung 0–100 %
    }


def fetch_lte_data(address: str, username: str, password: str) -> dict:
    ok, block = _fritz_ensure_session(address, username, password)
    if not ok:
        if block > 0:
            raise ConnectionError(
                f"Fritz!Box hat Login gesperrt ({block}s). "
                f"Zu viele Fehlversuche – bitte {block}s warten."
            )
        if not username:
            raise ConnectionError(
                "Login fehlgeschlagen. Deine Fritz!Box hat Benutzerverwaltung aktiviert "
                "– bitte Benutzernamen unter 'Benutzername setzen…' eintragen."
            )
        raise ConnectionError("Fritz!Box-Login fehlgeschlagen (falsches Passwort?).")
    fc = FritzConnection(address=address, user=username, password=password, timeout=10)
    raw = fc.call_action(LTE_SERVICE, "GetInfoEx")

    primary, secondary = {}, {}
    cell_xml = raw.get("NewCellList", "")
    if cell_xml:
        try:
            root = ET.fromstring(cell_xml)
            for cell in root.findall("Cell"):
                connected = cell.findtext("Connected", "")
                if "primary" in connected:
                    primary = _parse_cell(cell)
                elif "secondary" in connected:
                    secondary = _parse_cell(cell)
        except ET.ParseError:
            pass

    if not primary:
        raise ConnectionError("Keine Zelldaten erhalten – Box erreichbar, aber keine aktive LTE-Zelle?")

    return {
        "raw":       raw,
        "rsrp":      primary.get("rsrp"),
        "rsrq":      primary.get("rsrq"),
        "distance":  primary.get("distance"),
        "rssi":      primary.get("rssi"),
        "rsrp2":     secondary.get("rsrp"),
        "rsrq2":     secondary.get("rsrq"),
        "rssi2":     secondary.get("rssi"),
        "distance2": secondary.get("distance"),
        "nutzung":   primary.get("nutzung"),
        "nutzung2":  secondary.get("nutzung"),
        "provider":  primary.get("provider"),
        "cell_id":   primary.get("cell_id"),
        "standard":  raw.get("NewCurrentAccessTechnology", "LTE"),
        "band":      raw.get("NewCurrentAccessTechnology", "LTE"),
    }


# ── Signalqualität ────────────────────────────────────────────────────────────

def signal_emoji(rsrp) -> str:
    """Ampel-Emoji nach RSRP-Wert (dBm)."""
    if rsrp is None:
        return "⚪"
    if rsrp >= -80:
        return "🟢"
    if rsrp >= -95:
        return "🟡"
    return "🔴"


# ── Web-Dashboard (eingebettet) ───────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Fritz LTE Monitor</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue", sans-serif;
  background: #0d0d11;
  color: #d0d0e0;
  padding: 28px 24px;
  min-height: 100vh;
}
header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 24px;
}
h1 { font-size: 1rem; font-weight: 400; color: #6060a0; letter-spacing: 0.06em; text-transform: uppercase; }
#last-update { font-size: 0.7rem; color: #404060; }

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 10px;
  margin-bottom: 10px;
}
.cards-sm {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
  gap: 10px;
  margin-bottom: 24px;
}
.card {
  background: #16161e;
  border-radius: 10px;
  padding: 16px 14px;
  border: 1px solid #1e1e2e;
}
.card-label {
  font-size: 0.65rem;
  color: #555570;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: 8px;
  display: flex;
  align-items: center;
  gap: 6px;
}
.card-label .hint {
  font-size: 0.6rem;
  color: #3a3a55;
  text-transform: none;
  letter-spacing: 0;
  font-style: italic;
}
.card-value {
  font-size: 1.8rem;
  font-weight: 300;
  line-height: 1;
  color: #c0c0d8;
}
.card-value.sm { font-size: 1.2rem; padding-top: 4px; }
.card-sub {
  font-size: 0.68rem;
  color: #44445a;
  margin-top: 6px;
}
.good  { color: #4ade80; }
.ok    { color: #facc15; }
.weak  { color: #f87171; }
.na    { color: #555570; }

/* Netzlast-Balken */
.load-bar-wrap {
  background: #1e1e2a;
  border-radius: 4px;
  height: 6px;
  margin-top: 10px;
  overflow: hidden;
}
.load-bar {
  height: 100%;
  border-radius: 4px;
  transition: width 0.5s, background 0.5s;
}

.section {
  background: #16161e;
  border-radius: 10px;
  padding: 18px;
  margin-bottom: 16px;
  border: 1px solid #1e1e2e;
}
.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.section-title {
  font-size: 0.65rem;
  color: #555570;
  text-transform: uppercase;
  letter-spacing: 0.1em;
}
.chart-hint {
  font-size: 11px;
  color: #444460;
  margin-top: 10px;
  line-height: 1.6;
}
select {
  background: #1e1e2a;
  color: #888;
  border: 1px solid #2a2a3a;
  border-radius: 6px;
  padding: 5px 8px;
  font-size: 0.75rem;
  cursor: pointer;
}
canvas { display: block; }
.secondary-label {
  font-size: 0.6rem;
  color: #3a3a55;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-top: 4px;
}
</style>
</head>
<body>

<header>
  <h1>Fritz LTE Monitor</h1>
  <span id="last-update">–</span>
</header>

<!-- Hauptkarten -->
<div class="cards">
  <div class="card">
    <div class="card-label">RSRP <span class="hint">Empfangsstärke</span></div>
    <div class="card-value na" id="v-rsrp">–</div>
    <div class="card-sub" id="v-rsrp-sub">dBm · ≥ −80 gut · ≥ −95 ok</div>
  </div>
  <div class="card">
    <div class="card-label">RSRQ <span class="hint">Signalqualität</span></div>
    <div class="card-value na" id="v-rsrq">–</div>
    <div class="card-sub" id="v-rsrq-sub">dB · ≥ −9 gut · sinkt bei Netzlast</div>
  </div>
  <div class="card">
    <div class="card-label">Netzlast <span class="hint" id="v-load-hint">aus RSRQ</span></div>
    <div class="card-value na" id="v-load">–</div>
    <div class="card-sub" id="v-load-source">% geschätzte Zellenauslastung</div>
    <div class="load-bar-wrap"><div class="load-bar" id="load-bar" style="width:0%"></div></div>
  </div>
  <div class="card">
    <div class="card-label">RSSI <span class="hint">Gesamtpegel</span></div>
    <div class="card-value na" id="v-rssi">–</div>
    <div class="card-sub">dBm · steigt bei Interferenz/Last</div>
  </div>
</div>

<!-- Detailkarten -->
<div class="cards-sm">
  <div class="card">
    <div class="card-label">Entfernung</div>
    <div class="card-value sm na" id="v-dist">–</div>
    <div class="secondary-label" id="v-dist2"></div>
  </div>
  <div class="card">
    <div class="card-label">2. Zelle (RSRP / RSRQ)</div>
    <div class="card-value sm na" id="v-rsrp2">–</div>
    <div class="secondary-label" id="v-rsrq2"></div>
  </div>
  <div class="card">
    <div class="card-label">Technologie</div>
    <div class="card-value sm na" id="v-band">–</div>
    <div class="card-sub" id="v-provider">–</div>
  </div>
</div>

<!-- Signalverlauf -->
<div class="section">
  <div class="section-header">
    <span class="section-title">Signalverlauf</span>
    <select id="range" onchange="loadChart()">
      <option value="6">6 Stunden</option>
      <option value="24" selected>24 Stunden</option>
      <option value="72">3 Tage</option>
      <option value="168">7 Tage</option>
    </select>
  </div>
  <canvas id="chart" height="160"></canvas>
  <p class="chart-hint">RSRP (blau) misst die Empfangsstärke der Zelle – höhere Werte (weniger negativ) sind besser. RSRQ (gelb) zeigt die Signalqualität; sinkende Werte deuten auf erhöhte Netzlast oder Interferenzen hin. Die gestrichelte Linie zeigt RSRP der 2. Zelle (Carrier Aggregation), falls verbunden.</p>
</div>

<!-- Netzlastverlauf -->
<div class="section">
  <div class="section-header">
    <span class="section-title">Netzlast-Verlauf (%)</span>
  </div>
  <canvas id="chart-load" height="100"></canvas>
  <p class="chart-hint">Zellenauslastung in % – wenn möglich direkt aus der Fritzbox (Feld "Nutzung"), sonst aus RSRQ geschätzt. Grün &lt; 40 % · Gelb &lt; 65 % · Rot ≥ 65 %. Hohe Auslastung bedeutet, dass viele andere Nutzer dieselbe Zelle verwenden und dein Datendurchsatz sinkt.</p>
</div>

<script>
let chart, chartLoad;

function rsrpClass(v) {
  if (v == null) return 'na';
  if (v >= -80) return 'good';
  if (v >= -95) return 'ok';
  return 'weak';
}
function rsrqClass(v) {
  if (v == null) return 'na';
  if (v >= -9)  return 'good';
  if (v >= -12) return 'ok';
  return 'weak';
}
function loadClass(pct) {
  if (pct == null) return 'na';
  if (pct < 40) return 'good';
  if (pct < 65) return 'ok';
  return 'weak';
}
// RSRQ → Netzlast %:  -3 dB = 0 %, -19.5 dB = 100 %
function netzlast(rsrq) {
  if (rsrq == null) return null;
  return Math.max(0, Math.min(100, (rsrq + 3) / -16.5 * 100));
}
function loadColor(pct) {
  if (pct == null) return '#555570';
  if (pct < 40) return '#4ade80';
  if (pct < 65) return '#facc15';
  return '#f87171';
}
function fmt(val, dec=1, suffix='') {
  return val != null ? parseFloat(val).toFixed(dec) + suffix : '–';
}
function tsLabel(ts, hours) {
  const d = new Date(ts);
  if (hours > 48) return (d.getMonth()+1)+'/'+d.getDate()+' '+
    String(d.getHours()).padStart(2,'0')+':00';
  return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');
}

const chartOpts = {
  responsive: true, animation: false,
  interaction: { mode: 'index', intersect: false },
  plugins: {
    legend: { labels: { color: '#555570', font: { size: 11 } } },
    tooltip: {
      backgroundColor: '#1e1e2a', titleColor: '#aaa',
      bodyColor: '#ccc', borderColor: '#333', borderWidth: 1,
    }
  },
  scales: {
    x: { ticks: { color: '#444460', maxRotation: 0, maxTicksLimit: 10 }, grid: { color: '#1a1a22' } },
    y: { ticks: { color: '#444460' }, grid: { color: '#1a1a22' } }
  }
};

async function loadLatest() {
  try {
    const d = await fetch('/api/latest').then(r => r.json());

    // RSRP
    document.getElementById('v-rsrp').textContent = fmt(d.rsrp, 1);
    document.getElementById('v-rsrp').className = 'card-value ' + rsrpClass(d.rsrp);

    // RSRQ
    document.getElementById('v-rsrq').textContent = fmt(d.rsrq, 1);
    document.getElementById('v-rsrq').className = 'card-value ' + rsrqClass(d.rsrq);

    // Netzlast – Fritz!Box-Direktwert bevorzugt, sonst RSRQ-Schätzung
    const pct    = d.nutzung != null ? d.nutzung : netzlast(d.rsrq);
    const direct = d.nutzung != null;
    const lc     = loadClass(pct);
    document.getElementById('v-load').textContent = pct != null ? Math.round(pct) + ' %' : '–';
    document.getElementById('v-load').className = 'card-value ' + lc;
    document.getElementById('load-bar').style.width = (pct || 0) + '%';
    document.getElementById('load-bar').style.background = loadColor(pct);
    document.getElementById('v-load-hint').textContent = direct ? 'direkt' : 'aus RSRQ';
    document.getElementById('v-load-source').textContent = direct
      ? '% Zellenauslastung (Fritzbox)'
      : '% geschätzte Zellenauslastung';

    // RSSI
    document.getElementById('v-rssi').textContent = fmt(d.rssi, 0);
    document.getElementById('v-rssi').className = 'card-value ' + rsrpClass(d.rssi);

    // Entfernung
    const dist  = d.distance  != null ? d.distance  + ' m (primär)'   : '–';
    const dist2 = d.distance2 != null ? d.distance2 + ' m (sekundär)' : '';
    document.getElementById('v-dist').textContent  = dist;
    document.getElementById('v-dist2').textContent = dist2;

    // 2. Zelle
    document.getElementById('v-rsrp2').textContent = fmt(d.rsrp2, 1);
    document.getElementById('v-rsrp2').className   = 'card-value sm ' + rsrpClass(d.rsrp2);
    document.getElementById('v-rsrq2').textContent = d.rsrq2 != null ? 'RSRQ ' + fmt(d.rsrq2,1) + ' dB' : '';

    // Band / Provider
    document.getElementById('v-band').textContent     = d.band || '–';
    document.getElementById('v-provider').textContent = d.provider || '';

    if (d.ts) document.getElementById('last-update').textContent =
      'Letzte Messung: ' + d.ts.replace('T', ' ');
  } catch(e) {
    document.getElementById('last-update').textContent = 'Verbindungsfehler';
  }
}

async function loadChart() {
  const hours = document.getElementById('range').value;
  const rows  = await fetch('/api/history?hours=' + hours).then(r => r.json());
  const labels = rows.map(r => tsLabel(r.ts, hours));

  // Signalverlauf
  const ds1 = [
    { label: 'RSRP primär (dBm)',    data: rows.map(r => r.rsrp),  borderColor: '#818cf8', backgroundColor: 'rgba(129,140,248,0.06)', tension: 0.3, pointRadius: 1.5, borderWidth: 1.5 },
    { label: 'RSRP sekundär (dBm)',  data: rows.map(r => r.rsrp2), borderColor: '#a78bfa', backgroundColor: 'rgba(167,139,250,0.04)', tension: 0.3, pointRadius: 1, borderWidth: 1, borderDash: [4,4] },
    { label: 'RSRQ primär (dB)',     data: rows.map(r => r.rsrq),  borderColor: '#fbbf24', backgroundColor: 'rgba(251,191,36,0.06)',  tension: 0.3, pointRadius: 1.5, borderWidth: 1.5 },
  ];
  if (chart) {
    chart.data.labels = labels; chart.data.datasets = ds1; chart.update('none');
  } else {
    chart = new Chart(document.getElementById('chart'), { type: 'line', data: { labels, datasets: ds1 }, options: chartOpts });
  }

  // Netzlastverlauf – Fritz!Box-Direktwert bevorzugt, sonst RSRQ-Schätzung
  const loadData = rows.map(r => r.nutzung != null ? r.nutzung : netzlast(r.rsrq));
  const loadColors = loadData.map(v => loadColor(v));
  const ds2 = [{
    label: 'Netzlast (%)',
    data: loadData,
    borderColor: '#fb923c',
    backgroundColor: 'rgba(251,146,60,0.08)',
    tension: 0.3, pointRadius: 1.5, borderWidth: 1.5,
    pointBackgroundColor: loadColors,
  }];
  const loadOpts = JSON.parse(JSON.stringify(chartOpts));
  loadOpts.scales.y.min = 0; loadOpts.scales.y.max = 100;
  loadOpts.scales.y.ticks.callback = v => v + '%';
  if (chartLoad) {
    chartLoad.data.labels = labels; chartLoad.data.datasets = ds2; chartLoad.update('none');
  } else {
    chartLoad = new Chart(document.getElementById('chart-load'), { type: 'line', data: { labels, datasets: ds2 }, options: loadOpts });
  }
}

loadLatest();
loadChart();
setInterval(() => { loadLatest(); loadChart(); }, 61000);
</script>
</body>
</html>
"""


# ── HTTP Handler ──────────────────────────────────────────────────────────────

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Kein Logging im Terminal

    def send_json(self, obj):
        body = json.dumps(obj, ensure_ascii=False, default=str).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urlparse(self.path)
        path   = parsed.path

        if path == "/":
            body = DASHBOARD_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/latest":
            self.send_json(query_latest())

        elif path == "/api/history":
            qs    = parse_qs(parsed.query)
            hours = int(qs.get("hours", ["24"])[0])
            self.send_json(query_history(hours))

        elif path == "/api/debug":
            self.send_json(query_all_raw())

        else:
            self.send_response(404)
            self.end_headers()


# ── Poller Thread ─────────────────────────────────────────────────────────────

class Poller(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app

    def _ui(self, fn, *args):
        """UI-Aufruf sicher auf den Main-Thread dispatchen."""
        NSOperationQueue.mainQueue().addOperationWithBlock_(lambda: fn(*args))

    def run(self):
        time.sleep(3)
        while True:
            cfg      = load_config()
            pw       = cfg.get("fritz_password", "")
            username = cfg.get("fritz_username", "")
            if pw:
                try:
                    data = fetch_lte_data(cfg["fritz_address"], username, pw)
                    insert_record(data)
                    self._ui(self.app.refresh, data, None)
                except Exception as e:
                    self._ui(self.app.refresh, None, str(e))
            else:
                self._ui(self.app.refresh, None, "Passwort nicht gesetzt")
            time.sleep(cfg.get("poll_interval", POLL_INTERVAL))


# ── Menu-Bar App ──────────────────────────────────────────────────────────────

class FritzMonitor(rumps.App):
    def __init__(self):
        super().__init__("⚪ LTE", quit_button=None)

        self._status    = rumps.MenuItem("Starte…")
        self._rsrp      = rumps.MenuItem("")
        self._rsrq      = rumps.MenuItem("")
        self._dist      = rumps.MenuItem("")
        self._band      = rumps.MenuItem("")
        self._open      = rumps.MenuItem("Dashboard öffnen",   callback=self._open_dashboard)
        self._set_user  = rumps.MenuItem("Benutzername setzen…", callback=self._set_username)
        self._set_pw    = rumps.MenuItem("Passwort setzen…",   callback=self._set_password)
        self._set_addr  = rumps.MenuItem("Adresse ändern…",    callback=self._set_address)
        self._quit      = rumps.MenuItem("Beenden",            callback=rumps.quit_application)

        self.menu = [
            self._status,
            None,  # Trennlinie
            self._rsrp,
            self._rsrq,
            self._dist,
            self._band,
            None,
            self._open,
            self._set_user,
            self._set_pw,
            self._set_addr,
            None,
            self._quit,
        ]

        init_db()

        # Web-Server
        server = HTTPServer(("127.0.0.1", WEB_PORT), DashboardHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()

        # Poller
        Poller(self).start()

    # Wird aus dem Poller-Thread aufgerufen
    def refresh(self, data, error):
        if error:
            self.title         = "⚪ LTE"
            self._status.title = f"Fehler: {error}"
            return

        rsrp  = data.get("rsrp")
        rsrq  = data.get("rsrq")
        dist  = data.get("distance")
        dist2 = data.get("distance2")
        prov  = data.get("provider") or ""
        std   = data.get("standard") or "LTE"

        emoji      = signal_emoji(rsrp)
        rsrp_str   = f"{rsrp:.0f}" if rsrp is not None else "?"
        self.title = f"{emoji} {rsrp_str}"

        now = datetime.now().strftime("%H:%M")
        self._status.title = f"Aktualisiert {now}  –  {prov}"
        self._rsrp.title   = f"RSRP   {rsrp:.1f} dBm" if rsrp is not None else "RSRP   –"
        self._rsrq.title   = f"RSRQ   {rsrq:.1f} dB"  if rsrq is not None else "RSRQ   –"

        if dist is not None and dist2 is not None:
            self._dist.title = f"Entf.  {dist} m  /  {dist2} m  (pri/sek)"
        elif dist is not None:
            self._dist.title = f"Entf.  {dist} m"
        else:
            self._dist.title = "Entf.  –"

        self._band.title = std

    def _open_dashboard(self, _):
        webbrowser.open(f"http://127.0.0.1:{WEB_PORT}")

    def _ask_text(self, title, message, default="", secure=False):
        """Nativer NSAlert-Dialog mit temporärem Policy-Wechsel für Tastatureingabe."""
        ns_app = AppKit.NSApplication.sharedApplication()

        # Menu-Bar-Apps (Accessory) können keine Tastatureingaben empfangen.
        # Kurz auf Regular wechseln damit der Dialog fokussierbar wird.
        ns_app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyRegular)
        ns_app.activateIgnoringOtherApps_(True)

        alert = AppKit.NSAlert.alloc().init()
        alert.setMessageText_(title)
        alert.setInformativeText_(message)
        alert.addButtonWithTitle_("Speichern")
        alert.addButtonWithTitle_("Abbrechen")

        import objc
        from Foundation import NSMakeRect
        rect = NSMakeRect(0, 0, 260, 22)
        if secure:
            field = AppKit.NSSecureTextField.alloc().initWithFrame_(rect)
        else:
            field = AppKit.NSTextField.alloc().initWithFrame_(rect)
        field.setStringValue_(default)
        alert.setAccessoryView_(field)
        alert.layout()
        alert.window().makeFirstResponder_(field)

        response = alert.runModal()

        # Zurück in den Menu-Bar-Modus
        ns_app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

        if response == AppKit.NSAlertFirstButtonReturn:
            return field.stringValue()
        return None

    def _set_username(self, _):
        cfg  = load_config()
        text = self._ask_text(
            "Fritzbox Benutzername",
            "Fritzbox-Benutzernamen eingeben\n(leer lassen wenn kein Benutzername gesetzt):",
            default=cfg.get("fritz_username", ""),
        )
        if text is not None:  # auch leerer String ist gültig
            cfg["fritz_username"] = text.strip()
            save_config(cfg)

    def _set_password(self, _):
        text = self._ask_text(
            "Fritzbox Passwort",
            "Fritzbox-Kennwort eingeben (wird lokal gespeichert):",
            secure=True,
        )
        if text:
            cfg = load_config()
            cfg["fritz_password"] = text
            save_config(cfg)

    def _set_address(self, _):
        cfg = load_config()
        text = self._ask_text(
            "Fritzbox Adresse",
            "IP oder Hostname der Fritzbox:",
            default=cfg.get("fritz_address", "fritz.box"),
        )
        if text:
            cfg["fritz_address"] = text.strip()
            save_config(cfg)


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    FritzMonitor().run()
