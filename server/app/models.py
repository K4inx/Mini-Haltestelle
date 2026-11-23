"""Datenmodelle.

Zwei Ebenen:
- ``Departure`` / ``StationMatch``: internes Modell, das jeder Provider liefert.
- ``Board`` / ``BoardRow`` / ``DisplayState``: der API-Vertrag zum Client
  (siehe docs/api-contract.md). Änderungen hier sind Vertragsänderungen.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Product(StrEnum):
    """Verkehrsmittel, provider-unabhängig."""

    S = "S"
    U = "U"
    TRAM = "TRAM"
    BUS = "BUS"
    REGIO = "REGIO"  # RB, RE
    FERN = "FERN"  # ICE, IC, Fernbus
    OTHER = "OTHER"


class Departure(BaseModel):
    """Eine Abfahrt an einer Station, so wie ein Provider sie liefert."""

    line: str
    product: Product
    direction: str
    stop: str
    planned: datetime  # Soll-Zeit, zeitzonenbehaftet
    realtime: datetime | None = None  # Ist-Zeit, falls Echtzeit vorhanden
    cancelled: bool = False
    platform: str | None = None
    trip_id: str | None = None
    messages: list[str] = Field(default_factory=list)

    @property
    def departure(self) -> datetime:
        """Beste bekannte Abfahrtszeit: Echtzeit vor Soll-Zeit."""
        return self.realtime or self.planned

    @property
    def delay_min(self) -> int:
        if self.realtime is None:
            return 0
        return round((self.realtime - self.planned).total_seconds() / 60)

    @property
    def key(self) -> str:
        """Stabile Kennung einer Fahrt, z. B. für die Push-Entprellung."""
        return self.trip_id or f"{self.line}|{self.direction}|{self.planned.isoformat()}"


class StationMatch(BaseModel):
    """Treffer der Stationssuche."""

    id: str
    name: str
    lat: float | None = None
    lon: float | None = None
    products: list[Product] = Field(default_factory=list)
    provider: str


# --------------------------------------------------------------------------- #
# API-Vertrag zum Client
# --------------------------------------------------------------------------- #


class DisplayMode(StrEnum):
    ON = "on"
    DIM = "dim"
    OFF = "off"


class LeaveSignal(StrEnum):
    NONE = "none"
    IDEAL = "ideal"
    HURRY = "hurry"


class DisplayState(BaseModel):
    mode: DisplayMode = DisplayMode.ON
    brightness: float = Field(0.8, ge=0.0, le=1.0)


class BoardRow(BaseModel):
    line: str
    product: Product
    direction: str
    stop: str
    departure: datetime
    planned: datetime
    delay_min: int
    cancelled: bool
    reachable: bool = True
    platform: str | None = None
    color: str | None = Field(None, description="Badge-Farbe als #rrggbb (aus der Config)")


class Board(BaseModel):
    generated_at: datetime
    profile: str
    profiles: list[str] = Field(description="Alle Profile in Reihenfolge, für Taster A/B")
    title: str
    display: DisplayState
    leave_signal: LeaveSignal = LeaveSignal.NONE
    stale: bool = False
    stale_since: datetime | None = None
    rows: list[BoardRow] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)
    source: str = Field(description="Datenquelle für die Quellenangabe, z. B. 'Transitous'")
