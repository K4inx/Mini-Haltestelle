"""Demo-Board ohne Server — für ``--demo`` und Tests. Zeiten relativ zu jetzt."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def demo_board_json(
    now: datetime | None = None, *, messages: bool = True, reminder: bool = False, stale: bool = False
) -> dict[str, Any]:
    now = (now or datetime.now().astimezone()).replace(second=0, microsecond=0)

    def t(minutes: float) -> str:
        return (now + timedelta(minutes=minutes)).isoformat()

    def row(
        line,
        product,
        color,
        direction,
        stop,
        m,
        delay=0,
        cancelled=False,
        realtime=True,
        walk=6,
        travel=24,
        transfers=0,
        platform=None,
    ):
        return {
            "line": line,
            "product": product,
            "color": color,
            "direction": direction,
            "stop": stop,
            "departure": t(m),
            "planned": t(m - delay),
            "delay_min": delay,
            "cancelled": cancelled,
            "platform": platform,
            "walk_minutes": walk,
            "leave_at": t(m - walk - 2),
            "realtime": realtime,
            "arrival": t(m + travel),
            "transfers": transfers,
            "key": f"{line}-{m}",
        }

    rows = [
        row("S1", "S", "#3f9d3b", "Rödermark-Ober-Roden Bahnhof", "Nied Bahnhof", 9, platform="2"),
        row("59", "BUS", "#8e3d97", "Cheruskerweg", "Nied Bahnhof/Lotzstraße", 11, walk=4, travel=15),
        row("S2", "S", "#3f9d3b", "Dietzenbach Bahnhof", "Nied Bahnhof", 14, delay=3, platform="2"),
        row("S1", "S", "#3f9d3b", "Rödermark-Ober-Roden Bahnhof", "Nied Bahnhof", 24, cancelled=True),
        row(
            "11", "TRAM", "#d2232a", "Schießhüttenstraße", "Luthmerstraße", 27, delay=6, walk=8, travel=31, transfers=1
        ),
        row("S2", "S", "#3f9d3b", "Dietzenbach Bahnhof", "Nied Bahnhof", 74, realtime=False),
    ]
    rem = None
    if reminder:
        r0 = rows[0]
        rem = {
            "line": r0["line"],
            "direction": r0["direction"],
            "stop": r0["stop"],
            "departure": r0["departure"],
            "leave_at": r0["leave_at"],
            "signal": "ideal",
        }
    return {
        "generated_at": now.isoformat(),
        "profile": "Zur Arbeit",
        "profiles": ["Zur Arbeit", "Therapie"],
        "title": "Zur Arbeit",
        "display": {"mode": "on", "brightness": 0.8},
        "leave_signal": "ideal" if reminder else "none",
        "stale": stale,
        "stale_since": t(-4) if stale else None,
        "rows": rows,
        "messages": ["Bauarbeiten im S-Bahn-Tunnel: Abends keine S-Bahn zwischen Hauptbahnhof und Ostendstraße"]
        if messages
        else [],
        "source": "Transitous",
        "destination": "Höchst",
        "reminder": rem,
    }
