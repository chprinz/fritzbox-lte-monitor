# Fritz LTE Monitor

macOS-Menüleisten-App für Fritzbox-LTE-Router (z.B. 6890 LTE). Misst alle 60 Sekunden RSRP, RSRQ und RSSI der verbundenen LTE-Zellen und zeigt den Verlauf in einem lokalen Web-Dashboard unter **http://127.0.0.1:5433**.

![Menüleiste: 🟢 −76 dBm](https://img.shields.io/badge/macOS-13%2B-blue) ![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue)

---

## Features

- **Menüleisten-Ampel** 🟢/🟡/🔴 mit aktuellem RSRP-Wert
- **Web-Dashboard** mit Live-Karten für RSRP, RSRQ, RSSI und Netzlast
- **Netzlast** direkt aus der Fritzbox (Feld `Utilization`), mit Fallback auf RSRQ-Schätzung
- **Carrier Aggregation** – 2. Zelle (RSRP / RSRQ) wird separat angezeigt
- **Signalverlauf** als Chart (6h / 24h / 3d / 7d)
- **Zellenabstand** (Primär- und Sekundärzelle)
- Alle Daten lokal in SQLite gespeichert – kein Cloud-Dienst, kein Internet erforderlich
- Optional als native macOS-App baubar (py2app)

---

## Dashboard

| Karte | Bedeutung | Gut | Ok | Schwach |
|-------|-----------|-----|----|---------|
| **RSRP** | Empfangsstärke der Zelle (dBm) | ≥ −80 | ≥ −95 | < −95 |
| **RSRQ** | Signalqualität – sinkt bei Netzlast (dB) | ≥ −9 | ≥ −12 | < −12 |
| **Netzlast** | Zellenauslastung % (direkt oder aus RSRQ) | < 40 % | < 65 % | ≥ 65 % |
| **RSSI** | Gesamtpegel inkl. Rauschen (dBm) | ≥ −80 | – | – |

Das Menüleisten-Icon zeigt:
- 🟢 RSRP ≥ −80 dBm
- 🟡 RSRP −80 bis −95 dBm
- 🔴 RSRP < −95 dBm
- ⚪ keine Verbindung / Fehler

---

## Einrichtung

### 1. Abhängigkeiten installieren

```bash
pip3 install rumps fritzconnection
```

### 2. App starten

```bash
python3 fritz_monitor.py
```

Das Menüleisten-Icon erscheint oben rechts (⚪ beim ersten Start).

### 3. Fritzbox-Zugang konfigurieren

Icon anklicken → **Passwort setzen…** → Fritzbox-Kennwort eingeben.

- Adresse ist standardmäßig `fritz.box` (passt für die meisten Setups)
- Zum Ändern: Icon → **Adresse ändern…**

Nach ca. 60 Sekunden wechselt das Icon auf 🟢/🟡/🔴 + RSRP-Wert.

### 4. Dashboard öffnen

Icon → **Dashboard öffnen** oder direkt: http://127.0.0.1:5433

---

## Als native macOS-App bauen (optional)

```bash
pip install py2app
python3 create_icon.py
rm -rf build dist
python3 setup.py py2app
```

Die fertige App liegt danach unter `dist/Fritz LTE Monitor.app`.

---

## Autostart mit launchd

```bash
# 1. plist kopieren
cp at.littleprinz.fritz-monitor.plist ~/Library/LaunchAgents/

# 2. Pfade anpassen (USERNAME, Pfad zu python3, Pfad zum Skript)
nano ~/Library/LaunchAgents/at.littleprinz.fritz-monitor.plist

# 3. Laden
launchctl load ~/Library/LaunchAgents/at.littleprinz.fritz-monitor.plist
```

Zum Deaktivieren:
```bash
launchctl unload ~/Library/LaunchAgents/at.littleprinz.fritz-monitor.plist
```

---

## Daten & Dateien

Alle Laufzeit-Dateien liegen in `~/.fritz_monitor/`:

| Datei | Inhalt |
|-------|--------|
| `config.json` | Fritzbox-Adresse, Passwort, Messintervall |
| `signals.db` | SQLite-Datenbank mit komplettem Signalverlauf |
| `stdout.log` / `stderr.log` | Logs (nur bei launchd-Betrieb) |

Die Datenbank kann direkt mit [DB Browser for SQLite](https://sqlitebrowser.org) geöffnet werden.

---

## Fehlerbehebung

**Icon bleibt ⚪ / keine Daten:**

Fritzbox-Erreichbarkeit testen:
```bash
python3 -c "
from fritzconnection import FritzConnection
fc = FritzConnection('fritz.box', password='DEIN_PW')
print(fc.call_action('X_AVM-DE_WANMobileConnection:1', 'GetInfoEx'))
"
```

**Netzlast zeigt „aus RSRQ" statt „direkt":**

Die Fritzbox liefert den Nutzungswert je nach Firmware/Modell unter dem Tag `<Utilization>`. Der `/api/debug`-Endpoint (http://127.0.0.1:5433/api/debug) zeigt das tatsächlich gelieferte XML — falls der Tag anders heißt, kann er in `_parse_cell()` angepasst werden.

**Debug-Endpoint:**

http://127.0.0.1:5433/api/debug zeigt die letzten 5 Roheinträge aus der Datenbank.

---

## Voraussetzungen

- macOS 13+
- Python 3.9+
- Fritzbox mit LTE-Modem (getestet mit 6890 LTE)
- TR-064 aktiviert: **Heimnetz → Netzwerk → Heimnetzfreigaben** (meist schon aktiv)

---

## Lizenz

MIT
