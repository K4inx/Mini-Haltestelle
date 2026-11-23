"""Board-Logik — reine Funktionen ohne I/O, damit sie vollständig testbar sind."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime, timedelta

from .config import (
    WEEKDAYS,
    AppConfig,
    LeaveConfig,
    ProfileConfig,
    RouteMatch,
    StopConfig,
    TextFilter,
    TimeWindow,
)
from .models import (
    Board,
    BoardRow,
    Departure,
    DisplayMode,
    DisplayState,
    LeaveSignal,
)

# --------------------------------------------------------------------------- #
# Zeitfenster
# --------------------------------------------------------------------------- #


def in_window(w: TimeWindow, now: datetime) -> bool:
    """``now`` muss in der Zeitzone der Config sein (lokale Uhrzeit)."""
    t = now.time().replace(second=0, microsecond=0)
    today = WEEKDAYS[now.weekday()]
    if w.start <= w.end:
        return today in w.days and w.start <= t < w.end
    # über Mitternacht: Abend zählt zum Starttag, Morgen zum Vortag
    yesterday = WEEKDAYS[(now.weekday() - 1) % 7]
    return (today in w.days and t >= w.start) or (yesterday in w.days and t < w.end)


def scheduled_profile(cfg: AppConfig, now: datetime) -> str | None:
    for entry in cfg.schedule:
        if in_window(entry, now) and entry.profile in cfg.profiles:
            return entry.profile
    return cfg.fallback_profile


def is_night(cfg: AppConfig, now: datetime) -> bool:
    return cfg.night is not None and in_window(cfg.night, now)


def next_change(fn: Callable[[datetime], object], now: datetime, horizon_h: int = 48) -> datetime:
    """Nächster Zeitpunkt (minutengenau), an dem ``fn`` einen anderen Wert liefert."""
    start = now.replace(second=0, microsecond=0)
    current = fn(start)
    for i in range(1, horizon_h * 60 + 1):
        t = start + timedelta(minutes=i)
        if fn(t) != current:
            return t
    return start + timedelta(hours=horizon_h)


# --------------------------------------------------------------------------- #
# Filtern, Zusammenführen, Sortieren
# --------------------------------------------------------------------------- #


def slack_minutes(dep: Departure, stop: StopConfig, now: datetime) -> float:
    """Puffer in Minuten: Zeit bis zur Abfahrt minus Gehzeit. < 0 = nicht mehr zu schaffen."""
    return (dep.departure - now).total_seconds() / 60 - stop.walk_minutes


def passes_filters(dep: Departure, stop: StopConfig) -> bool:
    if stop.products and dep.product not in stop.products:
        return False
    if not _line_ok(stop.lines, dep.line):
        return False
    if stop.routes and not any(route_hit(r, dep) for r in stop.routes):
        return False
    return stop.direction.matches(dep.direction)


def route_hit(r: RouteMatch, dep: Departure) -> bool:
    if r.line.strip().casefold() != dep.line.strip().casefold():
        return False
    if r.direction_id is not None and dep.direction_id is not None:
        return r.direction_id == dep.direction_id
    if r.headsign:
        return r.headsign.casefold() in dep.direction.casefold()
    return True


def _line_ok(f: TextFilter, line: str) -> bool:
    """Linien vergleichen exakt (U4 ≠ U41); ``re:`` erlaubt Muster."""

    def hit(p: str) -> bool:
        if p.startswith("re:"):
            return f._hit(p, line)
        return p.strip().casefold() == line.strip().casefold()

    if f.include and not any(hit(p) for p in f.include):
        return False
    return not any(hit(p) for p in f.exclude)


def collect(
    profile: ProfileConfig, deps_by_stop: dict[str, list[Departure]], now: datetime
) -> list[tuple[Departure, StopConfig]]:
    """Virtual Stop: alle Stationen des Profils filtern, unerreichbare entfernen, zusammenführen.

    ``deps_by_stop`` ist nach ``StopConfig.name`` geschlüsselt. Doppelte Fahrten (gleiche
    Fahrt über mehrere IDs derselben Station) werden entfernt.
    """
    out: list[tuple[Departure, StopConfig]] = []
    seen: set[tuple[str, str]] = set()
    for stop in profile.stops:
        for dep in deps_by_stop.get(stop.name, []):
            k = (stop.name, dep.key)
            if k in seen:
                continue
            seen.add(k)
            if not passes_filters(dep, stop):
                continue
            if slack_minutes(dep, stop, now) < 0:
                continue
            out.append((dep, stop))
    return out


def sort_rows(items: list[tuple[Departure, StopConfig]], mode: str) -> list[tuple[Departure, StopConfig]]:
    items = sorted(items, key=lambda x: (x[0].departure, x[0].line))
    if mode != "line":
        return items
    first: dict[tuple[str, str], datetime] = {}
    for dep, _ in items:
        first.setdefault((dep.line, dep.direction), dep.departure)
    return sorted(items, key=lambda x: (first[(x[0].line, x[0].direction)], x[0].departure))


# --------------------------------------------------------------------------- #
# „Jetzt los"
# --------------------------------------------------------------------------- #


def leave_candidates(
    leave: LeaveConfig, items: Iterable[tuple[Departure, StopConfig]]
) -> list[tuple[Departure, StopConfig]]:
    f = TextFilter(include=[leave.direction] if leave.direction else [])
    return [
        (d, s)
        for d, s in items
        if not d.cancelled
        and (leave.line is None or d.line.casefold() == leave.line.casefold())
        and f.matches(d.direction)
        and (leave.stop is None or leave.stop == s.name)
    ]


def leave_signal(
    leave: LeaveConfig | None, items: Iterable[tuple[Departure, StopConfig]], now: datetime
) -> tuple[LeaveSignal, tuple[Departure, StopConfig] | None]:
    """Ideal schlägt knapp: Wenn eine passende Fahrt im Ideal-Fenster liegt, gilt ``ideal``."""
    if leave is None:
        return LeaveSignal.NONE, None
    hurry: tuple[Departure, StopConfig] | None = None
    for dep, stop in sorted(leave_candidates(leave, items), key=lambda x: x[0].departure):
        slack = slack_minutes(dep, stop, now)
        if leave.ideal[0] <= slack <= leave.ideal[1]:
            return LeaveSignal.IDEAL, (dep, stop)
        if hurry is None and leave.hurry[0] <= slack < leave.hurry[1]:
            hurry = (dep, stop)
    if hurry is not None:
        return LeaveSignal.HURRY, hurry
    return LeaveSignal.NONE, None


# --------------------------------------------------------------------------- #
# Board zusammenbauen
# --------------------------------------------------------------------------- #


def display_state(cfg: AppConfig, night: bool) -> DisplayState:
    if night and cfg.night is not None:
        mode = cfg.night.display_mode
        return DisplayState(mode=mode, brightness=0.0 if mode == DisplayMode.OFF else cfg.night.brightness)
    return DisplayState(mode=DisplayMode.ON, brightness=cfg.brightness)


def build_board(
    cfg: AppConfig,
    profile_name: str,
    deps_by_stop: dict[str, list[Departure]],
    now: datetime,
    *,
    night: bool,
    stale_since: datetime | None,
    source: str,
) -> tuple[Board, tuple[Departure, StopConfig] | None]:
    """Liefert das Board und (für die Push-Logik) die Fahrt, die das Leave-Signal auslöst."""
    profile = cfg.profiles[profile_name]
    items = collect(profile, deps_by_stop, now)
    signal, trigger = leave_signal(profile.leave, items, now)
    if night:
        signal, trigger = LeaveSignal.NONE, None

    rows = [
        BoardRow(
            line=d.line,
            product=d.product,
            direction=cfg.strip(d.direction),
            stop=cfg.strip(d.stop or s.name),
            departure=d.departure.astimezone(now.tzinfo),
            planned=d.planned.astimezone(now.tzinfo),
            delay_min=d.delay_min,
            cancelled=d.cancelled,
            reachable=True,
            platform=d.platform,
            color=cfg.color_for(d.product),
        )
        for d, s in sort_rows(items, profile.sort)[: profile.max_rows]
    ]
    messages: list[str] = []
    for d, _ in items:
        for m in d.messages:
            if m not in messages:
                messages.append(m)

    board = Board(
        generated_at=now,
        profile=profile_name,
        profiles=list(cfg.profiles),
        title=profile.title,
        display=display_state(cfg, night),
        leave_signal=signal,
        stale=stale_since is not None,
        stale_since=stale_since,
        rows=rows,
        messages=messages,
        source=source,
    )
    return board, trigger
