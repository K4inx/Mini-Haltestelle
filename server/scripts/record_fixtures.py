"""Echte API-Antworten als Test-Fixtures aufzeichnen.

    python scripts/record_fixtures.py transitous <stopId> [name]
    RMV_API_KEY=... python scripts/record_fixtures.py rmv <id> [name]

Schreibt nach tests/fixtures/<provider>/. Der RMV-Key landet nicht in der Datei.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
CONTACT = os.environ.get("TRANSITOUS_CONTACT", "https://github.com/K4inx/abfahrtsanzeige")


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    provider, sid = sys.argv[1], sys.argv[2]
    name = sys.argv[3] if len(sys.argv) > 3 else sid.replace(":", "_").replace("@", "_")[:40]
    if provider == "transitous":
        r = httpx.get(
            "https://api.transitous.org/api/v6/stoptimes",
            params={"stopId": sid, "n": 40, "language": "de"},
            headers={"User-Agent": f"abfahrtsanzeige/fixtures (+{CONTACT})"},
            timeout=15,
        )
        target = OUT / "transitous" / f"stoptimes_{name}.json"
    elif provider == "rmv":
        key = os.environ["RMV_API_KEY"]
        r = httpx.get(
            "https://www.rmv.de/hapi/departureBoard",
            params={"accessId": key, "id": sid, "format": "json", "maxJourneys": 40, "duration": 90},
            timeout=15,
        )
        target = OUT / "rmv" / f"departureBoard_{name}.json"
    else:
        sys.exit("Provider: transitous | rmv")
    r.raise_for_status()
    target.write_text(json.dumps(r.json(), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"→ {target} ({target.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
