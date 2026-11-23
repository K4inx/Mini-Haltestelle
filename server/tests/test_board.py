from __future__ import annotations

from datetime import timedelta

from app import board
from app.config import AppConfig, LeaveConfig, StopConfig
from app.models import Departure, DisplayMode, LeaveSignal, Product
from app.providers.transitous import parse_stoptimes

from .conftest import load, local, utc


def dep(
    line="U4",
    minutes=10,
    *,
    now,
    product=Product.U,
    direction="Bockenheimer Warte",
    cancelled=False,
    delay=0,
    trip=None,
    msgs=(),
):
    planned = now + timedelta(minutes=minutes - delay)
    return Departure(
        line=line,
        product=product,
        direction=direction,
        stop="Merianplatz",
        planned=planned,
        realtime=planned + timedelta(minutes=delay) if delay else None,
        cancelled=cancelled,
        trip_id=trip,
        messages=list(msgs),
    )


NOW = local("2026-11-23T08:00")
STOP = StopConfig(name="Merianplatz", ids={"transitous": "x"}, walk_minutes=5)


# ---------------------------------------------------------------- Filter --- #


def test_product_filter():
    s = STOP.model_copy(update={"products": [Product.S]})
    assert not board.passes_filters(dep(now=NOW), s)
    assert board.passes_filters(dep(now=NOW, line="S1", product=Product.S), s)


def test_line_whitelist_is_exact_and_blacklist():
    s = StopConfig.model_validate({"name": "x", "ids": {}, "lines": {"include": ["U4"]}})
    assert board.passes_filters(dep(now=NOW, line="U4"), s)
    assert not board.passes_filters(dep(now=NOW, line="U41"), s)
    s = StopConfig.model_validate({"name": "x", "ids": {}, "lines": {"exclude": ["re:^N\\d+"]}})
    assert not board.passes_filters(dep(now=NOW, line="N8"), s)
    assert board.passes_filters(dep(now=NOW, line="30"), s)


def test_direction_substring_and_regex():
    s = StopConfig.model_validate({"name": "x", "ids": {}, "direction": {"include": ["bockenheim"]}})
    assert board.passes_filters(dep(now=NOW), s)
    assert not board.passes_filters(dep(now=NOW, direction="Enkheim"), s)
    s = StopConfig.model_validate({"name": "x", "ids": {}, "direction": {"exclude": ["re:(Enk|Seck)"]}})
    assert not board.passes_filters(dep(now=NOW, direction="Seckbacher Landstraße"), s)


# --------------------------------------------------------------- Gehzeit --- #


def test_walk_time_hides_unreachable():
    p = AppConfig.model_validate(
        {"default_profile": "p", "profiles": {"p": {"title": "t", "stops": [STOP.model_dump()]}}}
    ).profiles["p"]
    deps = [dep(now=NOW, minutes=m, trip=str(m)) for m in (3, 5, 6, 12)]
    got = board.collect(p, {"Merianplatz": deps}, NOW)
    assert [round((d.departure - NOW).total_seconds() / 60) for d, _ in got] == [5, 6, 12]


def test_delay_counts_for_reachability():
    # Soll in 3 min (nicht zu schaffen), aber 4 min Verspätung → in 7 min → erreichbar
    d = dep(now=NOW, minutes=7, delay=4)
    assert board.slack_minutes(d, STOP, NOW) == 2


# -------------------------------------------------- Virtual Stop / Merge --- #


def test_virtual_stop_merge_with_real_fixtures(cfg_dict):
    cfg = AppConfig.model_validate(cfg_dict)
    now = utc("2026-11-23T17:05:00")
    deps = {
        "Merianplatz": parse_stoptimes(load("transitous/stoptimes_merianplatz.json")),
        "Konstablerwache": parse_stoptimes(load("transitous/stoptimes_konstablerwache.json")),
    }
    b, _ = board.build_board(cfg, "morgens", deps, now, night=False, stale_since=None, source="Transitous")
    assert len(b.rows) == 6
    assert {r.product for r in b.rows} <= {Product.U, Product.S}
    assert [r.departure for r in b.rows] == sorted(r.departure for r in b.rows)
    # Gehzeit Konstablerwache 19 min → keine S-Bahn vor 17:24
    assert all(r.departure >= utc("2026-11-23T17:24:00") for r in b.rows if r.product == Product.S)
    assert all(r.departure >= utc("2026-11-23T17:10:00") for r in b.rows)
    # Präfix „Frankfurt (Main) " abgeschnitten, Farbe gesetzt
    assert b.rows[0].direction == "Bockenheimer Warte"
    assert b.rows[0].stop == "Merianplatz"
    assert b.rows[0].color == "#1c62b0"


def test_merge_deduplicates_same_trip():
    p = AppConfig.model_validate(
        {"default_profile": "p", "profiles": {"p": {"title": "t", "stops": [STOP.model_dump()]}}}
    ).profiles["p"]
    d = dep(now=NOW, minutes=10, trip="T1")
    assert len(board.collect(p, {"Merianplatz": [d, d.model_copy()]}, NOW)) == 1


def test_sort_by_line_groups():
    items = [
        (dep(now=NOW, line=l, minutes=m, trip=f"{l}{m}"), STOP)
        for l, m in [("U4", 10), ("U5", 11), ("U4", 15), ("U5", 20)]
    ]
    got = [d.line for d, _ in board.sort_rows(items, "line")]
    assert got == ["U4", "U4", "U5", "U5"]


def test_max_rows_and_messages(cfg_dict):
    cfg_dict["profiles"]["morgens"]["max_rows"] = 2
    cfg = AppConfig.model_validate(cfg_dict)
    deps = [dep(now=NOW, minutes=m, trip=str(m), msgs=["Aufzug defekt"]) for m in (10, 11, 12)]
    b, _ = board.build_board(
        cfg, "morgens", {"Merianplatz": deps}, NOW, night=False, stale_since=None, source="x"
    )
    assert len(b.rows) == 2 and b.messages == ["Aufzug defekt"]


# ---------------------------------------------------------- Leave-Signal --- #

LEAVE = LeaveConfig(line="U4", direction="Bockenheimer", ideal=(2, 5), hurry=(0, 2))


def test_leave_ideal_hurry_none():
    # Gehzeit 5: Abfahrt in 8 → Puffer 3 → ideal
    assert board.leave_signal(LEAVE, [(dep(now=NOW, minutes=8), STOP)], NOW)[0] == LeaveSignal.IDEAL
    # Abfahrt in 6 → Puffer 1 → knapp
    assert board.leave_signal(LEAVE, [(dep(now=NOW, minutes=6), STOP)], NOW)[0] == LeaveSignal.HURRY
    # Abfahrt in 15 → Puffer 10 → nichts
    assert board.leave_signal(LEAVE, [(dep(now=NOW, minutes=15), STOP)], NOW)[0] == LeaveSignal.NONE


def test_leave_ideal_beats_hurry_and_ignores_others():
    items = [(dep(now=NOW, minutes=6, trip="a"), STOP), (dep(now=NOW, minutes=9, trip="b"), STOP)]
    sig, (d, _) = board.leave_signal(LEAVE, items, NOW)
    assert sig == LeaveSignal.IDEAL and d.trip_id == "b"
    wrong = [
        (dep(now=NOW, minutes=8, direction="Enkheim"), STOP),
        (dep(now=NOW, minutes=8, line="U5"), STOP),
        (dep(now=NOW, minutes=8, cancelled=True), STOP),
    ]
    assert board.leave_signal(LEAVE, wrong, NOW)[0] == LeaveSignal.NONE


def test_leave_signal_off_at_night(cfg_dict):
    cfg = AppConfig.model_validate(cfg_dict)
    b, trig = board.build_board(
        cfg,
        "morgens",
        {"Merianplatz": [dep(now=NOW, minutes=8)]},
        NOW,
        night=True,
        stale_since=None,
        source="x",
    )
    assert b.leave_signal == LeaveSignal.NONE and trig is None


# ------------------------------------------------------ Zeitplan / Nacht --- #


def test_schedule(cfg_dict):
    cfg = AppConfig.model_validate(cfg_dict)
    assert board.scheduled_profile(cfg, local("2026-11-23T07:00")) == "morgens"  # Montag
    assert board.scheduled_profile(cfg, local("2026-11-23T10:00")) == "abends"  # Ende exklusiv
    assert board.scheduled_profile(cfg, local("2026-11-28T07:00")) == "abends"  # Samstag früh
    assert board.scheduled_profile(cfg, local("2026-11-28T09:00")) == "wochenende"


def test_night_over_midnight(cfg_dict):
    cfg = AppConfig.model_validate(cfg_dict)
    assert board.is_night(cfg, local("2026-11-23T23:30"))
    assert board.is_night(cfg, local("2026-11-24T02:00"))
    assert not board.is_night(cfg, local("2026-11-24T06:00"))
    assert not board.is_night(cfg, local("2026-11-23T22:59"))


def test_night_restricted_days_after_midnight():
    w = board.TimeWindow.model_validate({"days": ["fri"], "from": "22:00", "to": "02:00"})
    assert board.in_window(w, local("2026-11-28T01:00"))  # Sa 01:00 gehört zu Freitagnacht
    assert not board.in_window(w, local("2026-11-27T01:00"))  # Fr 01:00 gehört zu Donnerstag


def test_display_state(cfg_dict):
    cfg = AppConfig.model_validate(cfg_dict)
    assert board.display_state(cfg, False).mode == DisplayMode.ON
    dim = board.display_state(cfg, True)
    assert dim.mode == DisplayMode.DIM and dim.brightness == 0.05
    cfg_dict["night"]["mode"] = "off"
    off = board.display_state(AppConfig.model_validate(cfg_dict), True)
    assert off.mode == DisplayMode.OFF and off.brightness == 0


def test_next_change(cfg_dict):
    cfg = AppConfig.model_validate(cfg_dict)
    t = board.next_change(lambda x: board.is_night(cfg, x), local("2026-11-23T20:15"))
    assert t == local("2026-11-23T23:00")
