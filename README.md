# Mini-Haltestelle — Live-Abfahrtsanzeige für den Schreibtisch

Eine kleine Haltestelle für den Schreibtisch: ein Raspberry Pi Zero W mit 2"-Display
(Pimoroni Display HAT Mini) zeigt die nächsten Abfahrten an. Der Bildschirm sitzt im
gedruckten Gehäuse eines Haltestellenmasts. Gebaut für Frankfurt (RMV), funktioniert
aber überall, wo es Transitous-Daten gibt.

```
Transitous (MOTIS 2) ─┐
                      ├─► server/ (FastAPI, Docker, Homelab) ──JSON──► client/ (Pi Zero W + Display HAT Mini)
RMV HAFAS ReST ───────┘          │
                                 └──► Webhook (z. B. n8n → Discord): „Jetzt los"
```

**Thin Client.** Die ganze Logik liegt auf dem Server: Profile, Filter, Gehzeit,
Nachtmodus, „Jetzt los". Der Zero zeigt nur an. So bleibt er schnell, und Änderungen
brauchen kein Update am Gerät.

| Ordner | Inhalt | Stand |
|---|---|---|
| `server/` | FastAPI-Backend, Poller, Web-UI, Docker | Phase 1 ✔ |
| `client/` | Python-Client für den Pi, Mock-Fenster für den PC | Phase 2 ✔ (Mock), Hardware-Test offen |
| `case/` | parametrisches OpenSCAD-Gehäuse | Phase 3 |
| `docs/` | [API-Vertrag](docs/api-contract.md), [Pi einrichten](docs/pi-setup.md), [Mock unter Windows](docs/mock-windows.md) | |

## Funktionen (Server)

- **Austauschbare Datenquelle**: `DepartureProvider` mit `transitous` (ohne Key) und `rmv`
  (Key). `provider: auto` nimmt RMV, sobald `RMV_API_KEY` gesetzt ist.
- **Profile** (`morgens`, `abends`, …) mit Zeitplan (`mon-fri 06:00–10:00`). Umschalten
  per Taster: Die Wahl gilt bis zum nächsten Wechsel im Zeitplan.
- **Virtual Stop**: mehrere Stationen pro Profil, zu einer Liste zusammengeführt.
  Gefiltert wird nach Verkehrsmittel, Linie (exakt oder `re:`) und Richtung (Substring oder `re:`).
- **Gehzeit**: Abfahrten, die du nicht mehr schaffst, blendet der Server aus. Verspätungen zählen mit.
- **„Jetzt los"**: ein Leave-Signal `ideal`/`hurry` für die RGB-LED, optional ein Webhook-Push,
  höchstens einer pro Fahrt.
- **Nachtmodus**: Display gedimmt oder aus. Bei `off` pausiert auch der Poller.
- **Poller mit Cache**: Nur der Poller fragt die Datenquelle ab, höchstens alle 30 s pro
  Station und nur für aktive Geräte. Bei RMV hält er ein Tagesbudget ein. Bei Fehlern wartet
  er mit Backoff und liefert die letzten Daten mit `stale: true` aus.
- **Web-UI** (`/`), ganz ohne Config-Datei: Start und Ziel eintippen (mit Vorschlagsliste),
  „Verbindung suchen" zeigt die Haltestellen in der Nähe mit Fußweg und den Linien, die zum Ziel
  führen. Ankreuzen, Namen vergeben, optional „automatisch Mo–Fr 6–10 Uhr" → **Vorlage**.
  Die Übersicht zeigt pro Vorlage Start → Ziel und die nächsten zwei Abfahrten, die du noch
  schaffst. Dazu Live-Vorschau des Displays (320×240) mit virtuellen Tastern und Nachtmodus.
  Kein Login: nur fürs Heimnetz.

## Quickstart

### Server (Docker)

```bash
cd server
cp .env.example .env          # ggf. RMV_API_KEY, NOTIFY_WEBHOOK_URL
docker compose up -d --build  # legt config/config.yaml leer an
# → http://<host>:8080, dort die erste Vorlage anlegen
```

Update später: `server/update.sh` (holt `git pull`, baut neu, räumt alte Images weg).

### Server (Entwicklung)

```bash
python3 -m venv .venv && .venv/bin/pip install -e 'server[dev]'
cd server
CONFIG_PATH=config/config.yaml ../.venv/bin/uvicorn app.main:app --reload --port 8080
../.venv/bin/pytest          # Tests, ohne echte API-Aufrufe
../.venv/bin/ruff check . && ../.venv/bin/ruff format --check .
```

### Client

Auf dem Pi (Raspberry Pi OS Lite 32-bit, Legacy/Bookworm). Alles kommt aus apt, nichts muss kompiliert werden:

```bash
sudo git clone https://github.com/K4inx/Mini-Haltestelle.git /opt/mini-haltestelle
sudo /opt/mini-haltestelle/client/install.sh http://<server-ip>:8080
```

Komplette Anleitung inklusive Löten und SD-Karte: [docs/pi-setup.md](docs/pi-setup.md).

Ohne Hardware, im Fenster auf dem PC (Tasten A/B/X/Y, halten = langer Druck):

```bash
cd client
python -m venv .venv && .venv/bin/pip install -r requirements-pc.txt
.venv/bin/python -m abfahrt_client --mock --server http://<server-ip>:8080   # oder --demo
.venv/bin/python -m abfahrt_client --png out --demo                         # nur Bilddatei
```

Windows: [docs/mock-windows.md](docs/mock-windows.md).

**Client-Aufbau:** Der Client hat einen eigenen schlanken ST7789-Treiber (`spidev` +
`gpiozero`) statt Pimorons `displayhatmini`-Bibliothek. Die ist seit 2022 eingefroren, nutzt
`RPi.GPIO` und zieht per pip Abhängigkeiten nach, die auf ARMv6 kompiliert werden müssten.
Das Vollbild wird nur bei neuen Daten, Tastendruck oder Minutenwechsel neu gezeichnet. Der
Lauftext aktualisiert nur die Fußzeile per Teilfenster.

## Konfiguration

Normalerweise reicht das Web-UI. Es schreibt `server/config/config.yaml`, die vorherige Fassung
bleibt als `config.yaml.bak` liegen. Von Hand ändern geht auch. Kurzreferenz:

| Schlüssel | Bedeutung |
|---|---|
| `provider` | `auto` · `transitous` · `rmv` |
| `default_profile` | Profil außerhalb aller Zeitplan-Fenster |
| `profiles.<name>` | eine **Vorlage**: `title` (Kopfzeile), `origin`/`destination` (nur zur Anzeige) |
| `profiles.<name>.stops[]` | `name`, `ids.{transitous,rmv}` (String oder Liste), `walk_minutes`, `routes[]` (`line` + `direction_id`, sonst `headsign`), optional `products`, `lines`, `direction` |
| `profiles.<name>.sort` | `time` (Standard) oder `line` (gruppiert nach Linie/Richtung) |
| `profiles.<name>.max_rows` | Standard 6 |
| `profiles.<name>.leave` | `ideal: [2, 5]`, `hurry: [0, 2]`, `notify`; optional `line`/`direction`/`stop`, sonst zählt jede Abfahrt der Vorlage |
| `schedule[]` | `profile`, `days` (`mon-fri` oder Liste), `from`, `to` (über Mitternacht erlaubt) |
| `night` | `days`, `from`, `to`, `mode: dim\|off`, `brightness` |
| `brightness`, `colors` | Tag-Helligkeit, Badge-Farben pro Verkehrsmittel |
| `region` | Gegend, die die Adresssuche bevorzugt (Standard Frankfurt) |
| `poll` | `interval_seconds` (≥ 30), `departures_per_station`, `rmv_daily_budget`, `timeout_seconds` |

Secrets und Schalter in `server/.env` (siehe [`.env.example`](server/.env.example)):
`RMV_API_KEY`, `TRANSITOUS_CONTACT`, `NOTIFY_WEBHOOK_URL`.

### Station-IDs finden

Macht das Web-UI beim Anlegen einer Vorlage. Von Hand: `GET /api/stations?q=Merianplatz&provider=transitous`.
Die IDs unterscheiden sich je Provider, deshalb stehen sie pro Provider in `ids:`.
Transitous liefert für manche Stationen nur Bahnsteig-IDs ohne gemeinsame Eltern-ID —
dann steht dort eine Liste.

## Datenquellen und Nutzungsbedingungen

- **[Transitous](https://transitous.org)**: Community-Dienst, Best-Effort, nicht kommerziell.
  Quellen: <https://transitous.org/sources/>, Kartendaten © OpenStreetMap-Mitwirkende.
  Der Server schickt einen User-Agent mit Kontaktangabe (`TRANSITOUS_CONTACT`) und fragt
  jede Station höchstens alle 30 s ab.
- **[RMV Open Data](https://www.rmv.de/s/de/rmv-open-data)**: Key nötig. Höchstens 600 Abfragen
  pro Stunde und 5000 pro Tag. Das Intervall passt sich deshalb an die Zahl der aktiven Stationen an.
  Die Daten stammen vom RMV.

## Lizenz

MIT, siehe [LICENSE](LICENSE).
