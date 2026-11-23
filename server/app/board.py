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
    Reminder,
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


def slack_minutes(dep: Departure, stop: StopConfig, now: datetime, buffer: float = 0) -> float:
    """Spielraum in Minuten: Zeit bis zur Abfahrt minus Gehzeit minus Sicherheitspuffer.
    < 0 = nicht mehr (mit Puffer) zu schaffen."""
    return (dep.departure - now).total_seconds() / 60 - stop.walk_minutes - buffer


def leave_at(dep: Departure, stop: StopConfig, buffer: float = 0) -> datetime:
    """Spätestens losgehen, damit man ``buffer`` Minuten vor Abfahrt an der Station ist."""
    return dep.departure - timedelta(minutes=stop.walk_minutes + buffer)


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
    profile: ProfileConfig, deps_by_stop: dict[str, list[Departure]], now: datetime, buffer: float = 0
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
            if slack_minutes(dep, stop, now, buffer) < 0:
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
    leave: LeaveConfig | None,
    items: Iterable[tuple[Departure, StopConfig]],
    now: datetime,
    buffer: float = 0,
) -> tuple[LeaveSignal, tuple[Departure, StopConfig] | None]:
    """Ideal schlägt knapp: Wenn eine passende Fahrt im Ideal-Fenster liegt, gilt ``ideal``."""
    if leave is None:
        return LeaveSignal.NONE, None
    hurry: tuple[Departure, StopConfig] | None = None
    for dep, stop in sorted(leave_candidates(leave, items), key=lambda x: x[0].departure):
        slack = slack_minutes(dep, stop, now, buffer)
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
    reminder_key: str | None = None,
) -> tuple[Board, tuple[Departure, StopConfig] | None]:
    """Liefert das Board und (für die Push-Logik) die Fahrt, die das Leave-Signal auslöst.

    Mit ``reminder_key`` folgt das Signal nur dieser Fahrt („Erinnere mich"). Ist sie nicht
    mehr erreichbar, fehlt ``board.reminder`` — der Aufrufer löscht die Erinnerung dann.
    """
    profile = cfg.profiles[profile_name]
    buf = cfg.buffer_minutes
    items = collect(profile, deps_by_stop, now, buf)
    reminder: Reminder | None = None
    if reminder_key is not None:
        hit = [(d, s) for d, s in items if d.key == reminder_key and not d.cancelled]
        signal, trigger = leave_signal(LeaveConfig(), hit, now, buf)
        if hit:
            d, s = hit[0]
            reminder = Reminder(
                line=d.line,
                direction=cfg.strip(d.direction),
                stop=cfg.strip(d.stop or s.name),
                departure=d.departure.astimezone(now.tzinfo),
                leave_at=leave_at(d, s, buf).astimezone(now.tzinfo),
                signal=signal,
            )
    else:
        signal, trigger = leave_signal(profile.leave, items, now, buf)
    if night:
        signal, trigger = LeaveSignal.NONE, None
        if reminder:
            reminder.signal = LeaveSignal.NONE

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
            walk_minutes=s.walk_minutes,
            leave_at=leave_at(d, s, buf).astimezone(now.tzinfo),
            realtime=d.realtime is not None,
            arrival=_arrival(d, s, now),
            transfers=_route(d, s).transfers if _route(d, s) else None,
            key=d.key,
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
        destination=cfg.strip(profile.destination.name) if profile.destination else None,
        reminder=reminder,
    )
    return board, trigger


def _route(dep: Departure, stop: StopConfig) -> RouteMatch | None:
    return next((r for r in stop.routes if route_hit(r, dep)), None)


def _arrival(dep: Departure, stop: StopConfig, now: datetime) -> datetime | None:
    r = _route(dep, stop)
    if r is None or r.travel_minutes is None or dep.cancelled:
        return None
    return (dep.departure + timedelta(minutes=r.travel_minutes)).astimezone(now.tzinfo)
