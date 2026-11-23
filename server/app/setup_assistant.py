"""Einrichtungs-Assistent: Adresse → nahe Stationen mit Gehzeit, Wohnung → Arbeit → „Jetzt los"-Linie.

Nutzt immer Transitous (Geocoding, OSM-Fußwege, Routing), auch wenn die Abfahrten später
von RMV kommen. Die RMV-ID trägt man dann über die Stationssuche nach.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any

from pydantic import BaseModel

from .models import Product
from .providers.transitous import MODE_MAP, TransitousProvider


class Place(BaseModel):
    name: str
    lat: float
    lon: float


class NearbySuggestion(BaseModel):
    name: str
    transitous_ids: list[str]
    products: list[Product]
    walk_minutes: float | None
    lat: float
    lon: float


class RouteSuggestion(BaseModel):
    line: str
    direction: str
    stop: str
    stop_id: str | None
    walk_minutes: float
    product: Product
    count: int


def walk_minutes(seconds: float | None) -> float | None:
    """Aufrunden auf ganze Minuten, plus eine Minute Puffer (Treppe, Ampel, Schuhe)."""
    if seconds is None:
        return None
    return float(math.ceil(seconds / 60) + 1)


async def geocode(p: TransitousProvider, address: str) -> Place:
    matches = await p.geocode_address(address)
    if not matches:
        raise LookupError(f"Adresse nicht gefunden: {address}")
    m = matches[0]
    return Place(name=m.get("name", address), lat=m["lat"], lon=m["lon"])


async def nearby(p: TransitousProvider, home: Place, radius_m: int = 800) -> list[NearbySuggestion]:
    stops = await p.nearby_stops(home.lat, home.lon, radius_m)
    durations = await p.walk_seconds(home.lat, home.lon, [(s.lat, s.lon) for s in stops])
    out = [
        NearbySuggestion(
            name=s.name,
            transitous_ids=s.ids,
            products=s.products,
            walk_minutes=walk_minutes(d),
            lat=s.lat,
            lon=s.lon,
        )
        for s, d in zip(stops, durations, strict=False)
    ]
    return sorted(out, key=lambda x: (x.walk_minutes is None, x.walk_minutes or 0))


def summarize_plan(plan: dict[str, Any]) -> list[RouteSuggestion]:
    """Erste ÖPNV-Fahrt jeder Verbindung → Vorschlag für Linie/Richtung/Station."""
    counter: Counter[tuple[str, str, str]] = Counter()
    info: dict[tuple[str, str, str], dict[str, Any]] = {}
    for it in plan.get("itineraries", []):
        walk = 0.0
        for leg in it.get("legs", []):
            if leg.get("mode") == "WALK":
                walk += leg.get("duration", 0)
                continue
            key = (
                leg.get("displayName") or leg.get("routeShortName") or "?",
                leg.get("headsign", ""),
                leg["from"].get("name", ""),
            )
            counter[key] += 1
            info.setdefault(
                key,
                {
                    "stop_id": leg["from"].get("parentId") or leg["from"].get("stopId"),
                    "walk": walk,
                    "product": MODE_MAP.get(leg.get("mode", ""), Product.OTHER),
                },
            )
            break
    return [
        RouteSuggestion(
            line=k[0],
            direction=k[1],
            stop=k[2],
            stop_id=info[k]["stop_id"],
            walk_minutes=walk_minutes(info[k]["walk"]) or 0,
            product=info[k]["product"],
            count=n,
        )
        for k, n in counter.most_common()
    ]


async def route(p: TransitousProvider, home: Place, work: Place) -> list[RouteSuggestion]:
    plan = await p.plan((home.lat, home.lon), (work.lat, work.lon))
    return summarize_plan(plan)
