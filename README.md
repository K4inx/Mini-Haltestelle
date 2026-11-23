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
| `client/` | Python-Client für den Pi, Mock-Modus | Phase 2 |
| `case/` | parametrisches OpenSCAD-Gehäuse | Phase 3 |
| `docs/` | [API-Vertrag](docs/api-contract.md), Setup-Anleitungen | |

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
- **Web-UI** (`/`): Live-Vorschau im Format 320×240 mit virtuellen Tastern, Config-Editor,
  Stationssuche, Status. Dazu ein **Gehzeit-Assistent**: Er sucht zu einer Adresse die nahen
  Stationen und berechnet den Fußweg über OpenStreetMap. Für Wohnung → Arbeit schlägt er eine
  „Jetzt los"-Linie vor.

## Quickstart

### Server (Docker)

```bash
cd server
cp .env.example .env                              # EDIT_TOKEN setzen, ggf. RMV_API_KEY
cp config/config.example.yaml config/config.yaml  # Stationen anpassen (oder per Web-UI)
docker compose up -d --build
# → http://<host>:8080
```

### Server (Entwicklung)

```bash
python3 -m venv .venv && .venv/bin/pip install -e 'server[dev]'
cd server
cp config/config.example.yaml config/config.yaml
CONFIG_PATH=config/config.yaml EDIT_TOKEN=dev ../.venv/bin/uvicorn app.main:app --reload --port 8080
../.venv/bin/pytest          # Tests, ohne echte API-Aufrufe
../.venv/bin/ruff check . && ../.venv/bin/ruff format --check .
```

### Client (Mock)

Kommt mit Phase 2.

## Konfiguration

`server/config/config.yaml` ist ausführlich kommentiert, siehe
[`config.example.yaml`](server/config/config.example.yaml). Kurzreferenz:

| Schlüssel | Bedeutung |
|---|---|
| `provider` | `auto` · `transitous` · `rmv` |
| `default_profile` | Profil außerhalb aller Zeitplan-Fenster |
| `profiles.<name>.title` | Kopfzeile auf dem Display |
| `profiles.<name>.stops[]` | `name`, `ids.{transitous,rmv}` (String oder Liste), `walk_minutes`, `products`, `lines.include/exclude`, `direction.include/exclude` |
| `profiles.<name>.sort` | `time` (Standard) oder `line` (gruppiert nach Linie/Richtung) |
| `profiles.<name>.max_rows` | Standard 6 |
| `profiles.<name>.leave` | `line`, `direction`, `stop`, `ideal: [2, 5]`, `hurry: [0, 2]`, `notify` |
| `schedule[]` | `profile`, `days` (`mon-fri` oder Liste), `from`, `to` (über Mitternacht erlaubt) |
| `night` | `days`, `from`, `to`, `mode: dim\|off`, `brightness` |
| `brightness`, `colors` | Tag-Helligkeit, Badge-Farben pro Verkehrsmittel |
| `poll` | `interval_seconds` (≥ 30), `departures_per_station`, `rmv_daily_budget`, `timeout_seconds` |

Secrets und Schalter in `server/.env` (siehe [`.env.example`](server/.env.example)):
`RMV_API_KEY`, `TRANSITOUS_CONTACT`, `EDIT_TOKEN`, `NOTIFY_WEBHOOK_URL`.

### Station-IDs finden

Im Web-UI unter **Stationssuche**, oder `GET /api/stations?q=Merianplatz&provider=transitous`.
Die IDs unterscheiden sich je Provider. Deshalb stehen sie pro Provider in `ids:`.
Transitous liefert für manche Stationen nur Bahnsteig-IDs ohne gemeinsame Eltern-ID.
Dann trägt man eine Liste ein, und der Assistent macht das automatisch.

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
