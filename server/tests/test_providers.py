from __future__ import annotations

import httpx
import pytest
import respx

from app.models import Product
from app.providers import ProviderError, RmvProvider, TransitousProvider
from app.providers.rmv import parse_departure_board, parse_location_name
from app.providers.transitous import BASE_URL, group_map_stops, parse_geocode, parse_stoptimes
from app.setup_assistant import summarize_plan, walk_minutes

from .conftest import load, utc

# --------------------------------------------------------------------------- #
# Transitous (echte, aufgezeichnete Antworten)
# --------------------------------------------------------------------------- #


def test_transitous_stoptimes_realtime_and_delay():
    deps = parse_stoptimes(load("transitous/stoptimes_merianplatz.json"))
    assert len(deps) > 5
    first = deps[0]
    assert first.line == "U4"
    assert first.product == Product.U
    assert first.direction == "Frankfurt (Main) Bockenheimer Warte"
    assert first.planned == utc("2026-11-23T17:09:00")
    assert first.departure == utc("2026-11-23T17:10:00")
    assert first.delay_min == 1
    assert first.trip_id


def test_transitous_modes_and_cancelled():
    deps = parse_stoptimes(load("transitous/stoptimes_konstablerwache.json"))
    products = {d.product for d in deps}
    assert {Product.S, Product.U, Product.TRAM, Product.BUS} <= products
    cancelled = [d for d in deps if d.cancelled]
    assert cancelled and all(d.line == "S1" for d in cancelled)


def test_transitous_regional_and_long_distance():
    deps = parse_stoptimes(load("transitous/stoptimes_hauptbahnhof.json"))
    by_product = {d.product for d in deps}
    assert Product.FERN in by_product or Product.REGIO in by_product
    assert all(d.product != Product.OTHER for d in deps)


def test_transitous_geocode():
    matches = parse_geocode(load("transitous/geocode_merianplatz.json"))
    assert matches[0].id == "de-DELFI_de:06412:512"
    assert matches[0].products == [Product.U]


def test_group_map_stops_uses_parent_or_platform_ids():
    groups = {g.name: g for g in group_map_stops(load("transitous/map_stops.json"))}
    assert groups["Frankfurt (Main) Merianplatz"].ids == ["de-DELFI_de:06412:512"]
    # Höhenstraße: U-Bahnsteige mit Eltern-ID, Bussteige ohne
    # (die Eltern-ID liefert auch die Busse, siehe Live-Test 23.11.2026)
    assert groups["Frankfurt (Main) Höhenstraße"].ids == ["de-DELFI_de:06412:502"]
    # Waldschmidtstraße: keine Eltern-ID → Bahnsteig-IDs
    assert len(groups["Frankfurt (Main) Waldschmidtstraße"].ids) == 2


def test_summarize_plan():
    sug = summarize_plan(load("transitous/plan.json"))
    top = sug[0]
    assert (top.line, top.stop, top.product) == ("U4", "Frankfurt (Main) Höhenstraße", Product.U)
    assert top.direction == "Frankfurt (Main) Bockenheimer Warte"
    assert top.walk_minutes == 5  # 240 s → 4 min + 1 min Puffer
    assert top.count >= 3


def test_walk_minutes_rounding():
    assert walk_minutes(322) == 7
    assert walk_minutes(None) is None


@respx.mock
async def test_transitous_sends_user_agent_and_handles_errors():
    route = respx.get(f"{BASE_URL}/v6/stoptimes").mock(
        return_value=httpx.Response(200, json=load("transitous/stoptimes_merianplatz.json"))
    )
    p = TransitousProvider("https://kainx.de")
    deps = await p.get_departures("de-DELFI_de:06412:512")
    assert deps
    ua = route.calls.last.request.headers["user-agent"]
    assert ua.startswith("abfahrtsanzeige/") and "https://kainx.de" in ua

    respx.get(f"{BASE_URL}/v6/stoptimes").mock(return_value=httpx.Response(503))
    with pytest.raises(ProviderError):
        await p.get_departures("x")
    respx.get(f"{BASE_URL}/v6/stoptimes").mock(side_effect=httpx.ConnectTimeout("timeout"))
    with pytest.raises(ProviderError):
        await p.get_departures("x")
    await p.aclose()


# --------------------------------------------------------------------------- #
# RMV (synthetische Fixtures — siehe fixtures/rmv/README.md)
# --------------------------------------------------------------------------- #


def test_rmv_departure_board():
    deps, msgs = parse_departure_board(load("rmv/departureBoard_synthetic.json"))
    assert [d.line for d in deps] == ["U4", "U4", "30"]
    u4 = deps[0]
    assert u4.product == Product.U
    assert u4.delay_min == 1
    assert u4.messages == ["Aufzug Merianplatz außer Betrieb"]
    assert u4.platform == "2"
    assert deps[1].cancelled is True  # "true" als String
    assert deps[1].realtime is None
    assert deps[2].product == Product.BUS
    assert deps[2].delay_min == 5  # über Mitternacht
    assert msgs == ["Bauarbeiten S-Bahn-Tunnel"]


def test_rmv_error_response():
    with pytest.raises(ProviderError):
        parse_departure_board({"errorCode": "API_AUTH", "errorText": "access denied"})


def test_rmv_location_name():
    m = parse_location_name(load("rmv/location_name_synthetic.json"))
    assert len(m) == 1 and m[0].name == "Frankfurt (Main) Merianplatz"


@respx.mock
async def test_rmv_key_not_leaked_in_errors():
    respx.get("https://www.rmv.de/hapi/departureBoard").mock(side_effect=httpx.ConnectError("boom"))
    p = RmvProvider("SECRETKEY123")
    with pytest.raises(ProviderError) as e:
        await p.get_departures("x")
    assert "SECRETKEY123" not in str(e.value)
    await p.aclose()
