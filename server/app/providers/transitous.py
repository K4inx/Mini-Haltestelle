"""Transitous (MOTIS 2), https://api.transitous.org/api/ — ohne Key, Best-Effort.

Nutzungsrichtlinie (https://transitous.org/api/): User-Agent mit Name, Version und Kontakt,
nicht kommerziell, Quellcode offen, Quellenangabe mit Link auf transitous.org/sources.
Wir rufen jede Station höchstens alle 30 s ab (siehe Poller).

Feldnamen aus echten Antworten vom 23.11.2026 (Fixtures unter tests/fixtures/transitous/).
"""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

import httpx
from pydantic import BaseModel

from .. import __version__
from ..models import Departure, Product, StationMatch
from .base import ProviderError

BASE_URL = "https://api.transitous.org/api"

MODE_MAP: dict[str, Product] = {
    "SUBURBAN": Product.S,
    "SUBWAY": Product.U,
    "METRO": Product.U,
    "TRAM": Product.TRAM,
    "BUS": Product.BUS,
    "REGIONAL_RAIL": Product.REGIO,
    "REGIONAL_FAST_RAIL": Product.REGIO,
    "RAIL": Product.REGIO,
    "HIGHSPEED_RAIL": Product.FERN,
    "LONG_DISTANCE": Product.FERN,
    "NIGHT_RAIL": Product.FERN,
    "COACH": Product.FERN,
}


def _ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_stoptimes(data: dict[str, Any]) -> list[Departure]:
    out: list[Departure] = []
    for st in data.get("stopTimes", []):
        place = st.get("place", {})
        planned = _ts(place.get("scheduledDeparture"))
        if planned is None:  # reine Ankunft (Endhalt)
            continue
        actual = _ts(place.get("departure")) if st.get("realTime") else None
        messages = [
            a.get("headerText", "").strip() for a in place.get("alerts", []) or [] if a.get("headerText")
        ]
        out.append(
            Departure(
                line=st.get("displayName") or st.get("routeShortName") or "?",
                product=MODE_MAP.get(st.get("mode", ""), Product.OTHER),
                direction=st.get("headsign") or st.get("tripTo", {}).get("name", ""),
                stop=place.get("name", ""),
                planned=planned,
                realtime=actual,
                cancelled=bool(st.get("cancelled") or st.get("tripCancelled")),
                platform=place.get("track") or place.get("scheduledTrack"),
                trip_id=st.get("tripId"),
                direction_id=st.get("directionId"),
                messages=messages,
            )
        )
    return out


def parse_geocode(data: list[dict[str, Any]]) -> list[StationMatch]:
    out: list[StationMatch] = []
    for m in data:
        if m.get("type") != "STOP":
            continue
        products = sorted({MODE_MAP.get(x, Product.OTHER) for x in m.get("modes") or []})
        out.append(
            StationMatch(
                id=m["id"],
                name=m["name"],
                lat=m.get("lat"),
                lon=m.get("lon"),
                products=products,
                provider="transitous",
            )
        )
    return out


class NearbyStop(BaseModel):
    name: str
    ids: list[str]
    lat: float
    lon: float
    products: list[Product]


def group_map_stops(data: list[dict[str, Any]]) -> list[NearbyStop]:
    """Bahnsteige gleichen Namens zu einer Station zusammenfassen.

    Hat ein Bahnsteig eine ``parentId``, wird diese genutzt (liefert alle Bahnsteige);
    sonst bleibt die Bahnsteig-ID selbst — die Eltern-ID ohne Suffix funktioniert dann nicht.
    """
    groups: dict[str, dict[str, Any]] = {}
    for s in data:
        g = groups.setdefault(s["name"], {"ids": [], "lat": [], "lon": [], "modes": set()})
        sid = s.get("parentId") or s.get("stopId")
        if sid and sid not in g["ids"]:
            g["ids"].append(sid)
        g["lat"].append(s["lat"])
        g["lon"].append(s["lon"])
        g["modes"].update(s.get("modes") or [])
    out = []
    for name, g in groups.items():
        # Eltern-ID liefert alle Bahnsteige darunter → deren eigene IDs sind überflüssig
        ids = [i for i in g["ids"] if not any(i.startswith(p + ":") for p in g["ids"] if p != i)]
        out.append(
            NearbyStop(
                name=name,
                ids=ids,
                lat=sum(g["lat"]) / len(g["lat"]),
                lon=sum(g["lon"]) / len(g["lon"]),
                products=sorted({MODE_MAP.get(m, Product.OTHER) for m in g["modes"]}),
            )
        )
    return out


class TransitousProvider:
    name = "transitous"
    attribution = "Transitous"
    min_interval_seconds = 30.0

    def __init__(self, contact: str, timeout: float = 10.0, client: httpx.AsyncClient | None = None):
        self._client = client or httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=timeout,
            headers={"User-Agent": f"abfahrtsanzeige/{__version__} (+{contact})"},
        )

    async def _get(self, path: str, params: dict[str, Any]) -> Any:
        try:
            r = await self._client.get(path, params=params)
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ProviderError(f"Transitous {path}: {e}") from e

    async def search_stations(self, query: str, limit: int = 10) -> list[StationMatch]:
        data = await self._get(
            "/v1/geocode", {"text": query, "type": "STOP", "numResults": limit, "language": "de"}
        )
        return parse_geocode(data)

    async def get_departures(self, station_id: str, limit: int = 30) -> list[Departure]:
        data = await self._get("/v6/stoptimes", {"stopId": station_id, "n": limit, "language": "de"})
        if not isinstance(data, dict) or "stopTimes" not in data:
            raise ProviderError("Transitous: Antwort ohne stopTimes")
        return parse_stoptimes(data)

    # --- nur für den Einrichtungs-Assistenten ------------------------------ #

    async def geocode(self, text: str, near: tuple[float, float] | None = None) -> list[dict[str, Any]]:
        """Adressen, Orte und Haltestellen — für die Vorschlagsliste beim Tippen."""
        params: dict[str, Any] = {"text": text, "numResults": 8, "language": "de"}
        if near:
            params |= {"place": f"{near[0]},{near[1]}", "placeBias": 5}
        return await self._get("/v1/geocode", params)

    async def nearby_stops(self, lat: float, lon: float, radius_m: int = 800) -> list[NearbyStop]:
        """Stationen im Umkreis (Bounding-Box über /v6/map/stops), nach Namen gruppiert."""
        dlat = radius_m / 111_320
        dlon = radius_m / (111_320 * max(0.1, math.cos(math.radians(lat))))
        data = await self._get(
            "/v6/map/stops",
            {"min": f"{lat - dlat},{lon - dlon}", "max": f"{lat + dlat},{lon + dlon}"},
        )
        return group_map_stops(data)

    async def walk_seconds(
        self, lat: float, lon: float, targets: list[tuple[float, float]], max_seconds: int = 1800
    ) -> list[float | None]:
        """Fußweg-Dauer (OSM) von einem Punkt zu mehreren Zielen; None = kein Weg gefunden."""
        if not targets:
            return []
        data = await self._get(
            "/v1/one-to-many",
            {
                "one": f"{lat};{lon}",
                "many": ",".join(f"{a};{b}" for a, b in targets),
                "mode": "WALK",
                "max": max_seconds,
                "maxMatchingDistance": 250,
                "arriveBy": "false",
            },
        )
        return [d.get("duration") if isinstance(d, dict) else None for d in data]

    async def plan(self, from_ll: tuple[float, float], to_ll: tuple[float, float]) -> dict[str, Any]:
        return await self._get(
            "/v6/plan",
            {
                "fromPlace": f"{from_ll[0]},{from_ll[1]}",
                "toPlace": f"{to_ll[0]},{to_ll[1]}",
                "numItineraries": 8,
                "searchWindow": 3600,
                "language": "de",
            },
        )

    async def plan_from(self, stop_id: str, to_ll: tuple[float, float]) -> dict[str, Any]:
        """Verbindungen ab einer Station (``fromPlace`` als Stop-ID), eine Stunde Suchfenster."""
        return await self._get(
            "/v6/plan",
            {
                "fromPlace": stop_id,
                "toPlace": f"{to_ll[0]},{to_ll[1]}",
                "searchWindow": 3600,
                "numItineraries": 8,
                "language": "de",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()
