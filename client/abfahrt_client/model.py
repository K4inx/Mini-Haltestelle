"""Board vom Server (siehe docs/api-contract.md) als einfache Dataclasses.

Tolerant gegenüber fehlenden/neuen Feldern, damit ein neuerer Server den Client nicht bricht.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


def _dt(v: Any) -> datetime | None:
    if not v:
        return None
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


@dataclass
class Row:
    line: str
    product: str
    direction: str
    stop: str
    departure: datetime
    planned: datetime
    delay_min: int = 0
    cancelled: bool = False
    platform: str | None = None
    color: str = "#6d7277"
    walk_minutes: float = 0
    leave_at: datetime | None = None
    realtime: bool = True
    arrival: datetime | None = None
    transfers: int | None = None
    key: str = ""

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Row:
        dep = _dt(d.get("departure"))
        assert dep is not None
        return cls(
            line=str(d.get("line", "?")),
            product=str(d.get("product", "OTHER")),
            direction=str(d.get("direction", "")),
            stop=str(d.get("stop", "")),
            departure=dep,
            planned=_dt(d.get("planned")) or dep,
            delay_min=int(d.get("delay_min") or 0),
            cancelled=bool(d.get("cancelled")),
            platform=d.get("platform"),
            color=d.get("color") or "#6d7277",
            walk_minutes=float(d.get("walk_minutes") or 0),
            leave_at=_dt(d.get("leave_at")),
            realtime=bool(d.get("realtime", True)),
            arrival=_dt(d.get("arrival")),
            transfers=d.get("transfers"),
            key=str(d.get("key") or ""),
        )


@dataclass
class Reminder:
    line: str
    direction: str
    stop: str
    departure: datetime
    leave_at: datetime
    signal: str = "none"


@dataclass
class Board:
    generated_at: datetime
    profile: str
    profiles: list[str]
    title: str
    display_mode: str = "on"  # on | dim | off
    brightness: float = 0.8
    leave_signal: str = "none"
    stale: bool = False
    stale_since: datetime | None = None
    rows: list[Row] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    source: str = ""
    destination: str | None = None
    reminder: Reminder | None = None

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> Board:
        disp = d.get("display") or {}
        rem = d.get("reminder")
        return cls(
            generated_at=_dt(d.get("generated_at")) or datetime.now().astimezone(),
            profile=str(d.get("profile") or ""),
            profiles=list(d.get("profiles") or []),
            title=str(d.get("title") or ""),
            display_mode=str(disp.get("mode", "on")),
            brightness=float(disp.get("brightness", 0.8)),
            leave_signal=str(d.get("leave_signal", "none")),
            stale=bool(d.get("stale")),
            stale_since=_dt(d.get("stale_since")),
            rows=[Row.from_json(r) for r in d.get("rows") or []],
            messages=[str(m) for m in d.get("messages") or []],
            source=str(d.get("source") or ""),
            destination=d.get("destination"),
            reminder=Reminder(
                line=rem["line"],
                direction=rem["direction"],
                stop=rem["stop"],
                departure=_dt(rem["departure"]),
                leave_at=_dt(rem["leave_at"]),  # type: ignore[arg-type]
                signal=rem.get("signal", "none"),
            )
            if rem
            else None,
        )

    def visible_rows(self, now: datetime) -> list[Row]:
        """Zwischen zwei Abrufen fallen Bahnen weg, für die man nicht mehr rechtzeitig loskommt."""
        out = []
        for r in self.rows:
            limit = r.leave_at or r.departure
            if r.cancelled or limit >= now.replace(second=0, microsecond=0):
                out.append(r)
        return out

    def focus_row(self, now: datetime) -> Row | None:
        """Gemerkte Bahn (Erinnerung), sonst die nächste, die nicht ausfällt."""
        rows = self.visible_rows(now)
        if self.reminder:
            for r in rows:
                if r.line == self.reminder.line and r.departure == self.reminder.departure:
                    return r
        return next((r for r in rows if not r.cancelled), None)
