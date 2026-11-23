"""RMV Open Data, HAFAS ReST (https://www.rmv.de/hapi/), Key aus ``RMV_API_KEY``.

ACHTUNG — noch nicht gegen eine echte Antwort geprüft (Stand 23.11.2026, Key beantragt).
Die Feldnamen stammen aus dem XSD rest-2.39 (``Departure``-Attribute ``name``, ``direction``,
``stop``, ``time``, ``date``, ``rtTime``, ``rtDate``, ``cancelled``, ``track``/``rtTrack``,
Kind-Elemente ``Product``, ``Messages``). Die JSON-Verpackung (Listen vs. Einzelobjekt,
``stopLocationOrCoordLocation``) ist aus anderen HAFAS-Projekten übernommen. Deshalb wird
hier bewusst tolerant geparst. Sobald der Key da ist: echte Antworten als Fixture
aufzeichnen (``scripts/record_fixtures.py rmv``) und die Tests darauf umstellen.

Nutzungsbedingungen: max. 600 Abfragen/h und 5000/Tag (der Poller hält ein Tagesbudget ein),
RMV als Quelle nennen.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from .. import __version__
from ..models import Departure, Product, StationMatch
from .base import ProviderError

BASE_URL = "https://www.rmv.de/hapi"
TZ = ZoneInfo("Europe/Berlin")

# Kurzbezeichnungen der Produktkategorie (catOutS / catIn / catOut) → Verkehrsmittel.
CATEGORY_MAP: dict[str, Product] = {
    "S": Product.S,
    "U": Product.U,
    "TRAM": Product.TRAM,
    "STR": Product.TRAM,
    "STRAB": Product.TRAM,
    "BUS": Product.BUS,
    "NIGHTBUS": Product.BUS,
    "AST": Product.BUS,
    "RB": Product.REGIO,
    "RE": Product.REGIO,
    "R": Product.REGIO,
    "REX": Product.REGIO,
    "IRE": Product.REGIO,
    "ICE": Product.FERN,
    "IC": Product.FERN,
    "EC": Product.FERN,
    "FLX": Product.FERN,
}


def _as_list(v: Any) -> list[Any]:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _truthy(v: Any) -> bool:
    return v is True or (isinstance(v, str) and v.lower() == "true")


def _dt(date: str | None, time: str | None) -> datetime | None:
    if not date or not time:
        return None
    fmt = "%Y-%m-%d %H:%M:%S" if time.count(":") == 2 else "%Y-%m-%d %H:%M"
    return datetime.strptime(f"{date} {time}", fmt).replace(tzinfo=TZ)


def product_of(prod: dict[str, Any]) -> Product:
    for key in ("catOutS", "catIn", "catOut"):
        cat = str(prod.get(key, "")).strip().upper()
        if cat in CATEGORY_MAP:
            return CATEGORY_MAP[cat]
    cat = str(prod.get("catOutL", "")).casefold()
    for word, p in (
        ("s-bahn", Product.S),
        ("u-bahn", Product.U),
        ("tram", Product.TRAM),
        ("straßenbahn", Product.TRAM),
        ("bus", Product.BUS),
    ):
        if word in cat:
            return p
    return Product.OTHER


def _message_texts(container: Any) -> list[str]:
    """``Messages: {Message: [...]}`` bzw. direkt eine Liste von Message-Objekten."""
    msgs: list[Any] = []
    for c in _as_list(container):
        if isinstance(c, dict) and "Message" in c:
            msgs.extend(_as_list(c["Message"]))
        else:
            msgs.append(c)
    out = []
    for m in msgs:
        if isinstance(m, dict) and m.get("act", True) not in (False, "false"):
            text = (m.get("head") or m.get("lead") or m.get("text") or "").strip()
            if text:
                out.append(text)
    return out


def parse_departure_board(data: dict[str, Any]) -> tuple[list[Departure], list[str]]:
    if "errorCode" in data:
        raise ProviderError(f"RMV: {data.get('errorCode')} {data.get('errorText', '')}")
    deps: list[Departure] = []
    for d in _as_list(data.get("Departure")):
        planned = _dt(d.get("date"), d.get("time"))
        if planned is None:
            continue
        prods = _as_list(d.get("Product")) or _as_list(d.get("ProductAtStop"))
        prod = prods[0] if prods else {}
        line = (
            prod.get("line") or prod.get("displayNumber") or prod.get("name") or d.get("name") or "?"
        ).strip()
        deps.append(
            Departure(
                line=line,
                product=product_of(prod),
                direction=d.get("direction", ""),
                stop=d.get("stop", ""),
                planned=planned,
                realtime=_dt(d.get("rtDate") or d.get("date"), d.get("rtTime")),
                cancelled=_truthy(d.get("cancelled")),
                platform=d.get("rtTrack") or d.get("track"),
                trip_id=(d.get("JourneyDetailRef") or {}).get("ref"),
                messages=_message_texts(d.get("Messages")),
            )
        )
    return deps, _message_texts(data.get("Message"))


def parse_location_name(data: dict[str, Any]) -> list[StationMatch]:
    out: list[StationMatch] = []
    for entry in _as_list(data.get("stopLocationOrCoordLocation")):
        s = entry.get("StopLocation") if isinstance(entry, dict) else None
        if not s:
            continue
        out.append(
            StationMatch(id=s["id"], name=s["name"], lat=s.get("lat"), lon=s.get("lon"), provider="rmv")
        )
    return out


class RmvProvider:
    name = "rmv"
    attribution = "RMV"
    min_interval_seconds = 30.0

    def __init__(self, api_key: str, timeout: float = 10.0, client: httpx.AsyncClient | None = None):
        self._key = api_key
        self._client = client or httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=timeout,
            headers={"User-Agent": f"abfahrtsanzeige/{__version__}"},
        )

    async def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            r = await self._client.get(path, params={"accessId": self._key, "format": "json", **params})
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as e:
            # Key nie in Fehlermeldungen/Logs
            raise ProviderError(f"RMV {path}: {str(e).replace(self._key, '***')}") from e
        if not isinstance(data, dict):
            raise ProviderError(f"RMV {path}: unerwartete Antwort")
        return data

    async def search_stations(self, query: str, limit: int = 10) -> list[StationMatch]:
        data = await self._get("/location.name", {"input": query, "maxNo": limit, "type": "S"})
        return parse_location_name(data)

    async def get_departures(self, station_id: str, limit: int = 30) -> list[Departure]:
        data = await self._get("/departureBoard", {"id": station_id, "maxJourneys": limit, "duration": 90})
        deps, board_msgs = parse_departure_board(data)
        # Stationsweite Meldungen an die erste Abfahrt hängen, damit sie im Board landen.
        if board_msgs and deps:
            deps[0].messages.extend(m for m in board_msgs if m not in deps[0].messages)
        return deps

    async def aclose(self) -> None:
        await self._client.aclose()
