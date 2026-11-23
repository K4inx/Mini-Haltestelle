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
      "platform": "2", "color": "#1c62b0",
      "walk_minutes": 5, "arrival": "2026-11-23T18:49:00+01:00", "transfers": 1,
      "key": "20261123_18:05_de-DELFI_3454095106"
    }
  ],
  "messages": ["Aufzug Merianplatz außer Betrieb"],
  "source": "Transitous",
  "destination": "Höchst",
  "reminder": {
    "line": "U4", "direction": "Bockenheimer Warte", "stop": "Merianplatz",
    "departure": "2026-11-23T18:29:00+01:00", "leave_at": "2026-11-23T18:24:00+01:00",
    "signal": "ideal"
  }
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
| `rows[].reachable` | Immer `true`: Abfahrten, die du mit Fußweg **und Puffer** nicht mehr schaffst, entfernt der Server. |
| `rows[].product` | `S` · `U` · `TRAM` · `BUS` · `REGIO` · `FERN` · `OTHER` |
| `rows[].color` | Badge-Farbe `#rrggbb` aus der Config (Standard oder überschrieben). |
| `messages` | Störungs-/Hinweistexte, dedupliziert → Lauftext. |
| `source` | Datenquelle für die Quellenangabe (`Transitous` oder `RMV`). Rechts klein in der Fußzeile. |
| `rows[].walk_minutes` | Fußweg zur Station. |
| `rows[].leave_at` | Spätestens losgehen = Abfahrt − Fußweg − Puffer (`buffer_minutes`, Standard 2) → Detail-Ansicht. |
| `rows[].realtime` | `false` = nur Fahrplanzeit, keine Live-Daten → Client zeigt `~` vor der Zeit. |
| `rows[].arrival`, `transfers` | Geschätzte Ankunft am Ziel (Abfahrt + Fahrzeit aus der Verbindungssuche) und Umstiege. Fehlt bei Vorlagen ohne diese Angabe. |
| `rows[].key` | Kennung der Fahrt. |
| `destination` | Ziel der Vorlage, für „Ankunft Höchst ca. 08:42“. |
| `reminder` | Aktive Erinnerung (Taster Y) oder `null`. Die LED folgt dann nur ihr und **blinkt** (`hurry` schneller). Ohne Erinnerung ist `leave_signal` bei Vorlagen `none`. |

### Darstellung (Client)

| Ansicht | Inhalt |
|---|---|
| Liste (Standard) | Kopf: Titel, ggf. 🔔 + Losgeh-Zeit, Uhr · bis zu 6 Zeilen · Fußzeile: Lauftext mit Meldungen, sonst „Ankunft <Ziel> ca. HH:MM“ der nächsten Bahn; rechts klein `source` |
| Detail (X) | nächste (bzw. gemerkte) Bahn groß: Linie, Richtung, Station/Gleis, „in N min“, Uhrzeit + Verspätung, „Losgehen spätestens“, Ankunft + Umstiege; Fußzeile: Meldungen |
| Info (Y 3 s) | IP, WLAN, Server, Version, Stand |

Neu gezeichnet wird nur, was sich ändert: Uhr und Minuten jede Sekunde, Zeilen und Fußzeile nur bei neuen Daten (sonst startet der Lauftext ständig neu).

Zeiten sind ISO 8601 mit Offset.

## Geräte-Aktionen (vom Taster)

| Endpunkt | Body | Wirkung |
|---|---|---|
| `POST /api/device/<id>/profile` | `{"step": 1}` / `{"step": -1}` / `{"profile": "abends"}` | Profil wechseln. Gilt bis zum nächsten Wechsel im Zeitplan. Antwort `{"profile": "..."}` |
| `POST /api/device/<id>/night` | — | Nachtmodus umschalten. Gilt bis zum nächsten automatischen Wechsel. Antwort `{"night": true}` |
| `POST /api/device/<id>/remind` | — | Taster Y: nächste erreichbare Bahn merken; ist schon eine gemerkt, wird sie gelöscht. Antwort `{"reminder": {...}}` bzw. `{"reminder": null}`, 409 ohne Abfahrt. Erlischt von selbst, wenn die Bahn nicht mehr zu schaffen ist. Mit `NOTIFY_WEBHOOK_URL` kommt beim Signal ein Push. |
| `POST /api/device/<id>/refresh` | — | Sofort neu abrufen (höchstens so oft, wie der Provider erlaubt). |
| `POST /api/device/<id>/reset` | — | Manuelle Wahl verwerfen. |

## Weitere Endpunkte

| Endpunkt | Zweck |
|---|---|
| `GET /api/places?q=<text>` | Adress-/Ortsvorschläge beim Tippen (Transitous-Geocoder, Gegend aus `region`) |
| `POST /api/connections` | `{origin, destination}` → Verbindungsoptionen ab den nahen Stationen (Linie, Richtung, Fußweg, Fahrzeit) |
| `GET /api/templates` | Vorlagen mit Start/Ziel, Stationen, Zeitplan und den nächsten zwei erreichbaren Abfahrten |
| `POST /api/templates` | `{name, origin, destination, options[], schedule?}` → Vorlage anlegen/überschreiben |
| `DELETE /api/templates/<name>` | Vorlage löschen |
| `GET /api/settings` · `PUT /api/settings` | Nachtmodus `{night: {enabled, from, to, mode}}` |
| `GET /api/stations?q=<text>[&provider=transitous\|rmv]` | Stationssuche nach Namen |
| `GET /api/status` | Poller- und Provider-Status |
| `GET /healthz` | Health-Check |

Das Web-UI hat **keine Anmeldung** — es ist nur fürs Heimnetz gedacht. Port nie öffentlich freigeben.

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
