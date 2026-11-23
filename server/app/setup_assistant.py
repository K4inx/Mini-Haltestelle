"""Vorlagen-Assistent: Adresse suchen → Verbindungen ab nahen Stationen → Vorlage bauen.

Nutzt immer Transitous (Geocoding, OSM-Fußwege, Routing), auch wenn die Abfahrten später
von RMV kommen. Pro Suche: 1× Geocoding je Feld (beim Tippen), 1× Stationen im Umkreis,
1× Fußwege, bis zu ``MAX_STATIONS`` + 1 Routings — weit unter der Transitous-Grenze von
720 Routings pro Tag.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any

from pydantic import BaseModel

from .config import AppConfig, Place, ProfileConfig, RouteMatch, StopConfig
from .models import Product
from .providers.base import ProviderError
from .providers.transitous import MODE_MAP, TransitousProvider

MAX_STATIONS = 6
RADIUS_M = 900
MAX_WALK_MIN = 15


class PlaceSuggestion(Place):
    label: str
    kind: str  # ADDRESS | PLACE | STOP


class ConnectionOption(BaseModel):
    key: str
    stop: str
    stop_ids: list[str]
    walk_minutes: float
    line: str
    product: Product
    direction_id: str | None
    headsign: str
    color: str | None = None
    travel_minutes: int
    total_minutes: int
    transfers: int
    count: int


def walk_minutes(seconds: float | None) -> float | None:
    """Aufrunden auf ganze Minuten, plus eine Minute Puffer (Treppe, Ampel, Schuhe)."""
    if seconds is None:
        return None
    return float(math.ceil(seconds / 60) + 1)


# --------------------------------------------------------------------------- #
# Adresssuche
# --------------------------------------------------------------------------- #


def parse_places(data: list[dict[str, Any]]) -> list[PlaceSuggestion]:
    out: list[PlaceSuggestion] = []
    seen: set[str] = set()
    for m in data:
        city = next((a["name"] for a in m.get("areas", []) if a.get("default")), "")
        name = m.get("name", "")
        if m.get("type") == "ADDRESS" and m.get("street") and m.get("houseNumber"):
            name = f"{m['street']} {m['houseNumber']}"
        label = name if not city or city in name else f"{name}, {m.get('zip', '')} {city}".replace("  ", " ")
        if label in seen:
            continue
        seen.add(label)
        out.append(
            PlaceSuggestion(name=name, label=label, kind=m.get("type", ""), lat=m["lat"], lon=m["lon"])
        )
    return out


async def places(p: TransitousProvider, text: str, region: Place) -> list[PlaceSuggestion]:
    data = await p.geocode(text, near=(region.lat, region.lon))
    return parse_places(data)


# --------------------------------------------------------------------------- #
# Verbindungen
# --------------------------------------------------------------------------- #


def _ts_minutes(it: dict[str, Any]) -> int:
    return round(it.get("duration", 0) / 60)


def options_from_plan(
    plan: dict[str, Any],
    *,
    station: str | None = None,
    station_ids: list[str] | None = None,
    walk: float | None = None,
) -> list[ConnectionOption]:
    """Erste ÖPNV-Fahrt jeder Verbindung → Option.

    Mit ``station``: Routing ab einer Station — nur Verbindungen, die dort auch abfahren
    (nicht erst zu einer anderen Station laufen). Ohne: Routing ab der Adresse, die
    Gehzeit ist dann der Fußweg vor der ersten Fahrt.
    """
    out: list[ConnectionOption] = []
    for it in plan.get("itineraries", []):
        legs = it.get("legs", [])
        pre_walk = 0.0
        first = None
        for leg in legs:
            if leg.get("mode") == "WALK":
                pre_walk += leg.get("duration", 0)
                continue
            first = leg
            break
        if first is None:
            continue
        frm = first.get("from", {})
        if station is not None:
            if frm.get("name") != station or pre_walk > 120:
                continue
            ids = station_ids or [frm.get("parentId") or frm.get("stopId")]
            w = walk or 0.0
            travel = _ts_minutes(it) - round(pre_walk / 60)
        else:
            ids = [frm.get("parentId") or frm.get("stopId")]
            w = walk_minutes(pre_walk) or 0.0
            travel = _ts_minutes(it) - round(pre_walk / 60)
        line = first.get("displayName") or first.get("routeShortName") or "?"
        did = first.get("directionId")
        color = first.get("routeColor")
        out.append(
            ConnectionOption(
                key=f"{frm.get('name')}|{line}|{did}",
                stop=frm.get("name", ""),
                stop_ids=[i for i in ids if i],
                walk_minutes=w,
                line=line,
                product=MODE_MAP.get(first.get("mode", ""), Product.OTHER),
                direction_id=did,
                headsign=first.get("headsign", ""),
                color=f"#{color}" if color and not color.startswith("#") else color,
                travel_minutes=travel,
                total_minutes=round(w) + travel,
                transfers=it.get("transfers", 0),
                count=1,
            )
        )
    return out


def merge_options(options: list[ConnectionOption]) -> list[ConnectionOption]:
    """Gleiche Station + Linie + Richtung zusammenfassen: schnellste Fahrt, Häufigkeit summiert."""
    merged: dict[str, ConnectionOption] = {}
    for o in options:
        m = merged.get(o.key)
        if m is None:
            merged[o.key] = o.model_copy()
            continue
        m.count += o.count
        if o.walk_minutes < m.walk_minutes or (
            o.walk_minutes == m.walk_minutes and len(o.stop_ids) > len(m.stop_ids)
        ):
            m.walk_minutes, m.stop_ids = o.walk_minutes, o.stop_ids
        m.travel_minutes = min(m.travel_minutes, o.travel_minutes)
        m.transfers = min(m.transfers, o.transfers)
        m.total_minutes = round(m.walk_minutes) + m.travel_minutes
    return sorted(merged.values(), key=lambda o: (o.total_minutes, o.walk_minutes, -o.count))


async def connections(p: TransitousProvider, origin: Place, destination: Place) -> list[ConnectionOption]:
    stops = await p.nearby_stops(origin.lat, origin.lon, RADIUS_M)
    durations = await p.walk_seconds(origin.lat, origin.lon, [(s.lat, s.lon) for s in stops])
    near = sorted(
        ((s, walk_minutes(d)) for s, d in zip(stops, durations, strict=False) if d is not None),
        key=lambda x: x[1] or 0,
    )
    near = [(s, w) for s, w in near if (w or 0) <= MAX_WALK_MIN][:MAX_STATIONS]

    async def from_station(s: Any, w: float | None) -> list[ConnectionOption]:
        try:
            plan = await p.plan_from(s.ids[0], (destination.lat, destination.lon))
        except ProviderError:
            return []
        return options_from_plan(plan, station=s.name, station_ids=s.ids, walk=w)

    async def from_origin() -> list[ConnectionOption]:
        plan = await p.plan((origin.lat, origin.lon), (destination.lat, destination.lon))
        return options_from_plan(plan)

    results = await asyncio.gather(from_origin(), *(from_station(s, w) for s, w in near))
    return merge_options([o for r in results for o in r])


# --------------------------------------------------------------------------- #
# Vorlage bauen
# --------------------------------------------------------------------------- #


def build_profile(
    cfg: AppConfig, name: str, origin: Place, destination: Place, chosen: list[ConnectionOption]
) -> ProfileConfig:
    stops: dict[str, StopConfig] = {}
    for o in chosen:
        sname = cfg.strip(o.stop)
        stop = stops.get(sname)
        if stop is None:
            ids = list(dict.fromkeys(o.stop_ids))
            stop = StopConfig(
                name=sname,
                ids={"transitous": ids[0] if len(ids) == 1 else ids},
                walk_minutes=o.walk_minutes,
            )
            stops[sname] = stop
        route = RouteMatch(
            line=o.line,
            direction_id=o.direction_id,
            headsign=cfg.strip(o.headsign),
            travel_minutes=o.travel_minutes,
            transfers=o.transfers,
        )
        if not any(r.line == route.line and r.direction_id == route.direction_id for r in stop.routes):
            stop.routes.append(route)
    return ProfileConfig(
        title=name,
        stops=list(stops.values()),
        origin=origin,
        destination=destination,
        leave=None,  # LED nur per „Erinnere mich" — bei dichtem Takt wäre sie sonst dauernd an
    )
