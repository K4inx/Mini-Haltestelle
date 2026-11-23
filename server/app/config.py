"""Konfiguration: config.yaml (gemountet, per Web-UI editierbar) + Secrets aus der Umgebung."""

from __future__ import annotations

import os
import re
from datetime import time
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_serializer, field_validator, model_validator

from .models import DisplayMode, Product

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

DEFAULT_COLORS: dict[str, str] = {
    "S": "#3f9d3b",
    "U": "#1c62b0",
    "TRAM": "#d2232a",
    "BUS": "#8e3d97",
    "REGIO": "#6d7277",
    "FERN": "#4a4f54",
    "OTHER": "#6d7277",
}


class TextFilter(BaseModel):
    """Substring-Liste; Einträge mit Präfix ``re:`` sind reguläre Ausdrücke. Groß/klein egal."""

    include: list[str] = Field(default_factory=list)
    exclude: list[str] = Field(default_factory=list)

    @staticmethod
    def _hit(pattern: str, text: str) -> bool:
        if pattern.startswith("re:"):
            return re.search(pattern[3:], text, re.IGNORECASE) is not None
        return pattern.casefold() in text.casefold()

    def matches(self, text: str) -> bool:
        if self.include and not any(self._hit(p, text) for p in self.include):
            return False
        return not any(self._hit(p, text) for p in self.exclude)


class RouteMatch(BaseModel):
    """Eine Linie in eine Richtung, die zum Ziel führt.

    Abgeglichen wird über Linie + GTFS-``direction_id`` (robust gegen verkürzte Fahrten mit
    anderem Endziel); fehlt die ID (z. B. RMV), über einen Teil des Zielnamens.
    """

    line: str
    direction_id: str | None = None
    headsign: str | None = None
    travel_minutes: int | None = Field(None, description="Fahrzeit Station → Ziel (für die Ankunftszeit)")
    transfers: int | None = None


class Place(BaseModel):
    name: str
    lat: float
    lon: float


class StopConfig(BaseModel):
    name: str
    ids: dict[str, str | list[str]] = Field(
        description="Station-ID(s) pro Provider, z. B. {transitous: ..., rmv: ...}. "
        "Eine Liste wird zusammengeführt (Transitous: Bahnsteige ohne gemeinsame Eltern-ID)."
    )
    walk_minutes: float = 0
    products: list[Product] = Field(default_factory=list, description="leer = alle")
    lines: TextFilter = Field(default_factory=TextFilter)
    direction: TextFilter = Field(default_factory=TextFilter)
    routes: list[RouteMatch] = Field(default_factory=list, description="leer = alle Linien")
    lat: float | None = None
    lon: float | None = None

    def ids_for(self, provider: str) -> list[str]:
        v = self.ids.get(provider)
        if v is None:
            return []
        return [v] if isinstance(v, str) else list(v)


class LeaveConfig(BaseModel):
    line: str | None = Field(None, description="leer = jede Abfahrt der Vorlage")
    direction: str = Field("", description="Substring oder re:...")
    stop: str | None = Field(None, description="Name der Station im Profil; leer = alle")
    ideal: tuple[float, float] = (2, 5)
    hurry: tuple[float, float] = (0, 2)
    notify: bool = False


class ProfileConfig(BaseModel):
    title: str
    stops: list[StopConfig] = Field(default_factory=list)
    sort: Literal["time", "line"] = "time"
    max_rows: int = Field(6, ge=1, le=20)
    leave: LeaveConfig | None = None
    origin: Place | None = None
    destination: Place | None = None


def _parse_hhmm(v: str | time) -> time:
    if isinstance(v, time):
        return v
    parts = str(v).split(":")
    return time(int(parts[0]), int(parts[1]))


class TimeWindow(BaseModel):
    """Zeitfenster an Wochentagen. ``from`` > ``to`` bedeutet: über Mitternacht."""

    days: list[str] = Field(default_factory=lambda: list(WEEKDAYS))
    start: time = Field(alias="from")
    end: time = Field(alias="to")

    model_config = {"populate_by_name": True}

    @field_validator("start", "end", mode="before")
    @classmethod
    def _time(cls, v: str | time) -> time:
        return _parse_hhmm(v)

    @field_serializer("start", "end")
    def _ser_time(self, v: time) -> str:
        return v.strftime("%H:%M")

    @field_validator("days", mode="before")
    @classmethod
    def _days(cls, v: list[str] | str) -> list[str]:
        """Erlaubt Listen (``[mon, tue]``) und Bereiche (``mon-fri``)."""
        items = v.split(",") if isinstance(v, str) else v
        out: list[str] = []
        for item in items:
            item = item.strip().lower()[:3] if "-" not in item else item.strip().lower()
            if "-" in item:
                a, b = (x.strip()[:3] for x in item.split("-"))
                ia, ib = WEEKDAYS.index(a), WEEKDAYS.index(b)
                rng = range(ia, ib + 1) if ia <= ib else [*range(ia, 7), *range(0, ib + 1)]
                out.extend(WEEKDAYS[i] for i in rng)
            else:
                if item not in WEEKDAYS:
                    raise ValueError(f"unbekannter Wochentag: {item}")
                out.append(item)
        return out


class ScheduleEntry(TimeWindow):
    profile: str


class NightConfig(TimeWindow):
    mode: Literal["off", "dim"] = "dim"
    brightness: float = Field(0.05, ge=0, le=1)

    @property
    def display_mode(self) -> DisplayMode:
        return DisplayMode(self.mode)


class PollConfig(BaseModel):
    interval_seconds: int = Field(30, ge=30, description="Transitous: mind. 30 s pro Station")
    departures_per_station: int = Field(30, ge=5, le=100)
    rmv_daily_budget: int = Field(4500, description="RMV erlaubt 5000/Tag, 600/h")
    timeout_seconds: float = 10


class AppConfig(BaseModel):
    provider: Literal["auto", "transitous", "rmv"] = "auto"
    timezone: str = "Europe/Berlin"
    default_profile: str | None = Field(None, description="leer = erste Vorlage")
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    schedule: list[ScheduleEntry] = Field(default_factory=list)
    night: NightConfig | None = None
    brightness: float = Field(0.8, ge=0, le=1)
    buffer_minutes: float = Field(
        2, ge=0, le=15, description="Sicherheitspuffer: so viele Minuten vor Abfahrt an der Station sein"
    )
    colors: dict[str, str] = Field(default_factory=dict, description="überschreibt Badge-Farben")
    strip_prefixes: list[str] = Field(
        default_factory=lambda: ["Frankfurt (Main) ", "Frankfurt (Main)-", "Frankfurt am Main, "],
        description="Wird von Ziel- und Stationsnamen vorne abgeschnitten",
    )
    poll: PollConfig = Field(default_factory=PollConfig)
    region: Place = Field(
        default_factory=lambda: Place(name="Frankfurt am Main", lat=50.1109, lon=8.6821),
        description="Bevorzugte Gegend für die Adresssuche",
    )

    @model_validator(mode="after")
    def _check_profiles(self) -> AppConfig:
        names = set(self.profiles)
        if self.default_profile is not None and self.default_profile not in names:
            raise ValueError(f"default_profile '{self.default_profile}' ist kein Profil")
        for entry in self.schedule:
            if entry.profile not in names:
                raise ValueError(f"schedule verweist auf unbekanntes Profil '{entry.profile}'")
        return self

    @property
    def fallback_profile(self) -> str | None:
        return self.default_profile or next(iter(self.profiles), None)

    def color_for(self, product: Product) -> str:
        return self.colors.get(product.value, DEFAULT_COLORS[product.value])

    def strip(self, name: str) -> str:
        for p in self.strip_prefixes:
            if name.startswith(p) and len(name) > len(p):
                return name[len(p) :]
        return name


class Secrets(BaseModel):
    rmv_api_key: str | None = None
    transitous_contact: str = "https://github.com/K4inx/Mini-Haltestelle"
    notify_webhook_url: str | None = None

    @classmethod
    def from_env(cls) -> Secrets:
        return cls(
            rmv_api_key=os.environ.get("RMV_API_KEY") or None,
            transitous_contact=os.environ.get("TRANSITOUS_CONTACT")
            or cls.model_fields["transitous_contact"].default,
            notify_webhook_url=os.environ.get("NOTIFY_WEBHOOK_URL") or None,
        )


def resolve_provider(cfg: AppConfig, secrets: Secrets) -> str:
    if cfg.provider == "auto":
        return "rmv" if secrets.rmv_api_key else "transitous"
    return cfg.provider


def load_config(path: Path) -> AppConfig:
    return AppConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def save_config(path: Path, cfg: AppConfig) -> None:
    """Config wird vom Web-UI verwaltet: atomar schreiben, vorherige Fassung als .bak."""
    data = cfg.model_dump(mode="json", by_alias=True, exclude_none=True, exclude_defaults=False)
    text = "# Wird vom Web-UI verwaltet. Von Hand ändern geht, Kommentare gehen dabei verloren.\n"
    text += yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    if path.exists():
        path.with_suffix(".yaml.bak").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def parse_config_text(text: str) -> AppConfig:
    data = yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ValueError("Config muss ein YAML-Objekt sein")
    return AppConfig.model_validate(data)
