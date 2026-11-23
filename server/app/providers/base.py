"""Provider-Schnittstelle: jede Datenquelle liefert dasselbe interne Modell."""

from __future__ import annotations

from typing import Protocol

from ..models import Departure, StationMatch


class ProviderError(Exception):
    """Abruf fehlgeschlagen (Netz, HTTP-Fehler, unerwartete Antwort)."""


class DepartureProvider(Protocol):
    name: str
    """Interner Name, zugleich Schlüssel in ``StopConfig.ids``."""
    attribution: str
    """Quellenangabe für Display und Web-UI."""
    min_interval_seconds: float
    """Kürzester erlaubter Abstand zwischen zwei Abrufen derselben Station."""

    async def search_stations(self, query: str, limit: int = 10) -> list[StationMatch]: ...

    async def get_departures(self, station_id: str, limit: int = 30) -> list[Departure]: ...

    async def aclose(self) -> None: ...
