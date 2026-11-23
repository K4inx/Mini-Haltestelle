# RMV-Fixtures — SYNTHETISCH

Diese Dateien sind **nicht** aufgezeichnet, sondern von Hand nach dem XSD `rest-2.39`
(https://www.rmv.de/hapi/xsd) gebaut, solange kein API-Key da ist. Die JSON-Verpackung
(`Departure` als Liste, `Product` als Liste, `Messages.Message`, `stopLocationOrCoordLocation`)
ist eine Annahme.

Sobald `RMV_API_KEY` gesetzt ist:

    python scripts/record_fixtures.py rmv <station-id>

und die Tests in `test_providers.py` auf die echten Dateien umstellen.
