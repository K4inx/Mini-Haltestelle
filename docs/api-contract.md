# API-Vertrag Server ↔ Client

Quelle der Wahrheit sind die pydantic-Modelle in `server/app/models.py` (`Board`, `BoardRow`,
`DisplayState`). Die OpenAPI-Beschreibung liefert der Server unter `/docs` bzw. `/openapi.json`.

Version 0.1 — Änderungen an Feldern sind Vertragsänderungen und brauchen ein Client-Update.
Neue, optionale Felder darf der Client ignorieren.

## `GET /api/board?device=<id>`

Fertig aufbereitetes Board. Profil, Nachtmodus, Gehzeit-Filter und Leave-Signal löst der
Server auf. Der Client zeigt nur an.

```json
{
  "generated_at": "2026-11-23T18:19:09+01:00",
  "profile": "morgens",
  "profiles": ["morgens", "abends", "wochenende"],
  "title": "Zuhause → Arbeit",
  "display": { "mode": "on", "brightness": 0.8 },
  "leave_signal": "ideal",
  "stale": false,
  "stale_since": null,
  "rows": [
    {
      "line": "U4", "product": "U", "direction": "Bockenheimer Warte",
      "stop": "Merianplatz",
      "departure": "2026-11-23T18:29:00+01:00",
      "planned": "2026-11-23T18:28:00+01:00",
      "delay_min": 1, "cancelled": false, "reachable": true,
      "platform": null, "color": "#1c62b0"
    }
  ],
  "messages": ["Aufzug Merianplatz außer Betrieb"],
  "source": "Transitous"
}
```

| Feld | Bedeutung |
|---|---|
| `generated_at` | Serverzeit (Ortszeit mit Offset). Der Client kann daraus die Uhrabweichung schätzen. |
| `profiles` | Alle Profile in Reihenfolge (für die Anzeige beim Umschalten mit A/B). |
| `display.mode` | `on` · `dim` · `off`. `brightness` 0…1 für die Hintergrundbeleuchtung. |
| `leave_signal` | `none` · `ideal` · `hurry` → RGB-LED. Im Nachtmodus immer `none`. |
| `stale` / `stale_since` | Datenquelle nicht erreichbar; letzte gültige Daten, seit `stale_since`. |
| `rows[].departure` | Beste bekannte Abfahrt (Echtzeit vor Soll). **Der Client zählt den Countdown selbst** aus diesem Feld herunter. |
| `rows[].planned`, `delay_min` | Soll-Zeit und Verspätung in Minuten (gerundet, kann negativ sein). |
| `rows[].cancelled` | Fällt aus → durchgestrichen. |
| `rows[].reachable` | Immer `true`: Nicht erreichbare Abfahrten (Abfahrt − jetzt < Gehzeit) entfernt der Server. |
| `rows[].product` | `S` · `U` · `TRAM` · `BUS` · `REGIO` · `FERN` · `OTHER` |
| `rows[].color` | Badge-Farbe `#rrggbb` aus der Config (Standard oder überschrieben). |
| `messages` | Störungs-/Hinweistexte, dedupliziert → Lauftext. |
| `source` | Datenquelle für die Quellenangabe (`Transitous` oder `RMV`). |

Zeiten sind ISO 8601 mit Offset.

## Geräte-Aktionen (vom Taster)

| Endpunkt | Body | Wirkung |
|---|---|---|
| `POST /api/device/<id>/profile` | `{"step": 1}` / `{"step": -1}` / `{"profile": "abends"}` | Profil wechseln. Gilt bis zum nächsten Wechsel im Zeitplan. Antwort `{"profile": "..."}` |
| `POST /api/device/<id>/night` | — | Nachtmodus umschalten. Gilt bis zum nächsten automatischen Wechsel. Antwort `{"night": true}` |
| `POST /api/device/<id>/refresh` | — | Sofort neu abrufen (höchstens so oft, wie der Provider erlaubt). |
| `POST /api/device/<id>/reset` | — | Manuelle Wahl verwerfen. |

## Weitere Endpunkte

| Endpunkt | Zweck |
|---|---|
| `GET /api/stations?q=<text>[&provider=transitous\|rmv]` | Stationssuche, um IDs zu finden |
| `GET /api/status` | Poller-, Geräte- und Provider-Status (für Web-UI und Info-Screen) |
| `GET /healthz` | Health-Check |
| `GET /api/config` · `PUT /api/config` | Config lesen/schreiben, Header `X-Edit-Token` |
| `GET /api/setup/nearby?address=` · `GET /api/setup/route?home=&work=` | Gehzeit-Assistent, Header `X-Edit-Token` |

## Push-Webhook („Jetzt los")

Ist `NOTIFY_WEBHOOK_URL` gesetzt und im Profil `leave.notify: true`, schickt der Server beim
Übergang auf `ideal` **einmal pro Fahrt** einen POST:

```json
{
  "event": "leave_now", "profile": "morgens", "title": "Zuhause → Arbeit",
  "line": "U4", "direction": "Frankfurt (Main) Bockenheimer Warte", "stop": "Merianplatz",
  "departure": "2026-11-23T08:12:00+01:00", "delay_min": 0,
  "minutes_until_departure": 8.0, "walk_minutes": 5,
  "text": "Jetzt los: U4 → Frankfurt (Main) Bockenheimer Warte ab Merianplatz um 08:12 (8 min, 5 min Fußweg)"
}
```

Ausgelöst wird beim Abruf des Boards. Ohne aktives Gerät gibt es also auch keinen Push.
