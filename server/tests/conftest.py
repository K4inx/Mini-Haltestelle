from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

FIX = Path(__file__).parent / "fixtures"
TZ = ZoneInfo("Europe/Berlin")


def load(name: str):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def local(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=TZ)


@pytest.fixture
def cfg_dict() -> dict:
    return {
        "default_profile": "abends",
        "profiles": {
            "morgens": {
                "title": "Zuhause → Arbeit",
                "stops": [
                    {
                        "name": "Merianplatz",
                        "ids": {"transitous": "de-DELFI_de:06412:512"},
                        "walk_minutes": 5,
                        "products": ["U"],
                    },
                    {
                        "name": "Konstablerwache",
                        "ids": {"transitous": "de-DELFI_de:06412:510_G"},
                        "walk_minutes": 19,
                        "products": ["S"],
                    },
                ],
                "leave": {"line": "U4", "direction": "Bockenheimer", "stop": "Merianplatz", "notify": True},
            },
            "abends": {"title": "Abends", "stops": [], "max_rows": 6},
            "wochenende": {"title": "Wochenende", "stops": []},
        },
        "schedule": [
            {"profile": "morgens", "days": "mon-fri", "from": "06:00", "to": "10:00"},
            {"profile": "wochenende", "days": ["sat", "sun"], "from": "08:00", "to": "20:00"},
        ],
        "night": {"from": "23:00", "to": "06:00", "mode": "dim", "brightness": 0.05},
    }
