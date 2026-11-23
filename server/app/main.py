"""FastAPI-App: Endpunkte, Lebenszyklus, Verdrahtung.

Das Web-UI ist nur fürs Heimnetz gedacht und hat keine Anmeldung: Wer die Seite erreicht,
kann Vorlagen anlegen und löschen. Deshalb den Port nie öffentlich freigeben.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import __version__, board, setup_assistant
from .config import AppConfig, NightConfig, Place, ScheduleEntry, Secrets, load_config, save_config
from .devices import DeviceStore
from .models import Board, BoardRow, DisplayMode, DisplayState, StationMatch
from .notify import LeaveNotifier
from .poller import Poller
from .providers import DepartureProvider, ProviderError, TransitousProvider, make_provider

log = logging.getLogger("abfahrtsanzeige")
WEB = Path(__file__).parent / "web"
UI_ACTIVE_FOR = timedelta(minutes=2)


@dataclass
class Runtime:
    config_path: Path
    cfg: AppConfig
    secrets: Secrets
    provider: DepartureProvider
    transitous: TransitousProvider  # für Assistent + Adresssuche, unabhängig vom Haupt-Provider
    poller: Poller
    devices: DeviceStore
    notifier: LeaveNotifier
    ui_seen: datetime | None = field(default=None)

    def now(self) -> datetime:
        return datetime.now(ZoneInfo(self.cfg.timezone))

    def station_ids(self, profile: str | None) -> dict[str, list[str]]:
        """Stationsname → IDs beim aktiven Provider."""
        if profile is None or profile not in self.cfg.profiles:
            return {}
        return {s.name: s.ids_for(self.provider.name) for s in self.cfg.profiles[profile].stops}

    def needed(self) -> set[str]:
        """Stationen, die der Poller abfragen soll: aktive Vorlagen aktiver Geräte,
        und alle Vorlagen, solange das Web-UI offen ist (für die Übersicht)."""
        now = self.now()
        profiles: set[str | None] = set()
        for dev in self.devices.active_devices(now):
            night = self.devices.night(self.cfg, dev, now)
            if night and self.cfg.night and self.cfg.night.mode == "off":
                continue  # Display aus → keine Abfragen
            profiles.add(self.devices.profile(self.cfg, dev, now))
        if self.ui_seen and now - self.ui_seen < UI_ACTIVE_FOR:
            profiles.update(self.cfg.profiles)
        return {i for p in profiles for ids in self.station_ids(p).values() for i in ids}

    def save(self, cfg: AppConfig) -> None:
        save_config(self.config_path, cfg)
        self.cfg = cfg

    def build(self, profile: str, now: datetime, *, night: bool = False, reminder_key: str | None = None):
        ids_by_stop = self.station_ids(profile)
        all_ids = {i for ids in ids_by_stop.values() for i in ids}
        deps = {
            name: [d for sid in ids for d in self.poller.departures(sid)] for name, ids in ids_by_stop.items()
        }
        return board.build_board(
            self.cfg,
            profile,
            deps,
            now,
            night=night,
            stale_since=self.poller.stale_since(all_ids),
            source=self.provider.attribution,
            reminder_key=reminder_key,
        )


def build_runtime(config_path: Path, data_dir: Path) -> Runtime:
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        save_config(config_path, AppConfig())
    cfg = load_config(config_path)
    sec = Secrets.from_env()
    provider = make_provider(cfg, sec)
    transitous = (
        provider
        if isinstance(provider, TransitousProvider)
        else TransitousProvider(sec.transitous_contact, timeout=cfg.poll.timeout_seconds)
    )
    rt: Runtime = Runtime(
        config_path=config_path,
        cfg=cfg,
        secrets=sec,
        provider=provider,
        transitous=transitous,
        poller=None,  # type: ignore[arg-type]
        devices=DeviceStore(data_dir / "devices.json"),
        notifier=LeaveNotifier(sec.notify_webhook_url),
    )
    rt.poller = Poller(provider, lambda: rt.cfg, rt.needed)
    return rt


def rt_dep(request: Request) -> Runtime:
    return request.app.state.rt


RT = Annotated[Runtime, Depends(rt_dep)]


# --------------------------------------------------------------------------- #
# Request-/Response-Modelle
# --------------------------------------------------------------------------- #


class ProfileChange(BaseModel):
    profile: str | None = None
    step: int | None = None  # +1 / -1 für Taster B / A


class ConnectionQuery(BaseModel):
    origin: Place
    destination: Place


class ScheduleIn(BaseModel):
    days: list[str]
    start: str = Field(alias="from")
    end: str = Field(alias="to")


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    origin: Place
    destination: Place
    options: list[setup_assistant.ConnectionOption] = Field(min_length=1)
    schedule: ScheduleIn | None = None


class NightIn(BaseModel):
    enabled: bool = True
    start: str = Field("23:00", alias="from")
    end: str = Field("06:00", alias="to")
    mode: Literal["dim", "off"] = "dim"


class SettingsIn(BaseModel):
    night: NightIn
    buffer_minutes: float = Field(2, ge=0, le=15)


def _no_template_board(rt: Runtime, now: datetime) -> Board:
    return Board(
        generated_at=now,
        profile="",
        profiles=[],
        title="Keine Vorlage",
        display=DisplayState(mode=DisplayMode.ON, brightness=rt.cfg.brightness),
        messages=["Im Web-UI eine Vorlage anlegen"],
        source=rt.provider.attribution,
    )


def create_app(runtime: Runtime | None = None, *, start_poller: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        rt = runtime or build_runtime(
            Path(os.environ.get("CONFIG_PATH", "config/config.yaml")),
            Path(os.environ.get("DATA_DIR", "data")),
        )
        app.state.rt = rt
        log.info("Abfahrtsanzeige %s, Provider %s", __version__, rt.provider.name)
        if start_poller:
            rt.poller.start()
        yield
        await rt.poller.stop()
        await rt.provider.aclose()
        if rt.transitous is not rt.provider:
            await rt.transitous.aclose()
        await rt.notifier.aclose()

    app = FastAPI(title="Abfahrtsanzeige", version=__version__, lifespan=lifespan)

    # ------------------------------------------------------------------ #
    # Client-API
    # ------------------------------------------------------------------ #

    @app.get("/api/board", response_model=Board)
    async def get_board(rt: RT, device: str = Query("default", max_length=64)) -> Board:
        now = rt.now()
        rt.devices.touch(device, now)
        profile = rt.devices.profile(rt.cfg, device, now)
        if profile is None:
            return _no_template_board(rt, now)
        night = rt.devices.night(rt.cfg, device, now)
        await rt.poller.ensure_loaded({i for ids in rt.station_ids(profile).values() for i in ids})
        rkey = rt.devices.reminder(device, profile)
        result, trigger = rt.build(profile, now, night=night, reminder_key=rkey)
        p = rt.cfg.profiles[profile]
        if rkey and result.reminder is None and not result.stale:
            rt.devices.set_reminder(device, None)  # Fahrt weg oder nicht mehr zu schaffen
        wants_push = rkey or (p.leave and p.leave.notify and result.leave_signal == "ideal")
        if trigger and result.leave_signal != "none" and wants_push:
            await rt.notifier.maybe_send(profile, p.title, trigger[0], trigger[1], now)
        return result

    @app.post("/api/device/{device}/remind")
    async def remind(rt: RT, device: str) -> dict[str, Any]:
        """Taster Y: nächste erreichbare Fahrt merken — oder eine bestehende Erinnerung löschen."""
        now = rt.now()
        profile = rt.devices.profile(rt.cfg, device, now)
        if profile is None:
            raise HTTPException(409, "Keine Vorlage aktiv")
        if rt.devices.reminder(device, profile):
            rt.devices.set_reminder(device, None)
            return {"reminder": None}
        await rt.poller.ensure_loaded({i for ids in rt.station_ids(profile).values() for i in ids})
        b, _ = rt.build(profile, now)
        first = next((r for r in b.rows if not r.cancelled), None)
        if first is None:
            raise HTTPException(409, "Keine Abfahrt, die du noch schaffst")
        rt.devices.set_reminder(device, first.key, profile)
        result, _ = rt.build(profile, now, reminder_key=first.key)
        return {"reminder": result.reminder.model_dump(mode="json") if result.reminder else None}

    @app.post("/api/device/{device}/profile")
    async def change_profile(rt: RT, device: str, body: ProfileChange) -> dict[str, str | None]:
        now = rt.now()
        try:
            if body.profile:
                rt.devices.set_profile(rt.cfg, device, body.profile, now)
            elif body.step:
                rt.devices.step_profile(rt.cfg, device, body.step, now)
            else:
                raise HTTPException(422, "profile oder step angeben")
        except KeyError:
            raise HTTPException(404, f"Vorlage unbekannt: {body.profile}") from None
        return {"profile": rt.devices.profile(rt.cfg, device, now)}

    @app.post("/api/device/{device}/night")
    async def toggle_night(rt: RT, device: str) -> dict[str, bool]:
        return {"night": rt.devices.toggle_night(rt.cfg, device, rt.now())}

    @app.post("/api/device/{device}/reset")
    async def reset_device(rt: RT, device: str) -> dict[str, str]:
        rt.devices.reset(device)
        return {"status": "ok"}

    @app.post("/api/device/{device}/refresh")
    async def refresh(rt: RT, device: str) -> dict[str, str]:
        profile = rt.devices.profile(rt.cfg, device, rt.now())
        rt.poller.request_refresh({i for ids in rt.station_ids(profile).values() for i in ids})
        return {"status": "ok"}

    # ------------------------------------------------------------------ #
    # Vorlagen-Assistent
    # ------------------------------------------------------------------ #

    @app.get("/api/places", response_model=list[setup_assistant.PlaceSuggestion])
    async def places(rt: RT, q: str = Query(..., min_length=3, max_length=100)):
        try:
            return await setup_assistant.places(rt.transitous, q, rt.cfg.region)
        except ProviderError as e:
            raise HTTPException(502, "Adresssuche gerade nicht erreichbar") from e

    @app.post("/api/connections", response_model=list[setup_assistant.ConnectionOption])
    async def connections(rt: RT, body: ConnectionQuery):
        try:
            return await setup_assistant.connections(rt.transitous, body.origin, body.destination)
        except ProviderError as e:
            raise HTTPException(502, "Verbindungssuche gerade nicht erreichbar") from e

    @app.get("/api/templates")
    async def list_templates(rt: RT) -> dict[str, Any]:
        now = rt.now()
        rt.ui_seen = now
        all_ids = {i for p in rt.cfg.profiles for ids in rt.station_ids(p).values() for i in ids}
        await rt.poller.ensure_loaded(all_ids)
        out = []
        for name, p in rt.cfg.profiles.items():
            b, _ = rt.build(name, now)
            out.append(
                {
                    "name": name,
                    "origin": p.origin,
                    "destination": p.destination,
                    "stops": [
                        {
                            "name": s.name,
                            "walk_minutes": s.walk_minutes,
                            "routes": [{"line": r.line, "headsign": r.headsign} for r in s.routes],
                        }
                        for s in p.stops
                    ],
                    "schedule": [
                        {"days": e.days, "from": e.start.strftime("%H:%M"), "to": e.end.strftime("%H:%M")}
                        for e in rt.cfg.schedule
                        if e.profile == name
                    ],
                    "next": [r.model_dump(mode="json") for r in _next_two(b.rows)],
                    "stale": b.stale,
                }
            )
        devices = {d: rt.devices.profile(rt.cfg, d, now) for d in rt.devices.active_devices(now)}
        return {"templates": out, "devices": devices, "source": rt.provider.attribution}

    @app.post("/api/templates")
    async def save_template(rt: RT, body: TemplateIn) -> dict[str, str]:
        name = body.name.strip()
        profile = setup_assistant.build_profile(rt.cfg, name, body.origin, body.destination, body.options)
        cfg = rt.cfg.model_copy(deep=True)
        cfg.profiles[name] = profile
        cfg.schedule = [e for e in cfg.schedule if e.profile != name]
        if body.schedule and body.schedule.days:
            try:
                cfg.schedule.append(
                    ScheduleEntry.model_validate(
                        {
                            "profile": name,
                            "days": body.schedule.days,
                            "from": body.schedule.start,
                            "to": body.schedule.end,
                        }
                    )
                )
            except ValueError as e:
                raise HTTPException(422, f"Zeitplan ungültig: {e}") from e
        rt.save(AppConfig.model_validate(cfg.model_dump(by_alias=True)))
        return {"status": "gespeichert", "name": name}

    @app.delete("/api/templates/{name}")
    async def delete_template(rt: RT, name: str) -> dict[str, str]:
        if name not in rt.cfg.profiles:
            raise HTTPException(404, "Vorlage unbekannt")
        cfg = rt.cfg.model_copy(deep=True)
        del cfg.profiles[name]
        cfg.schedule = [e for e in cfg.schedule if e.profile != name]
        if cfg.default_profile == name:
            cfg.default_profile = None
        rt.save(AppConfig.model_validate(cfg.model_dump(by_alias=True)))
        return {"status": "gelöscht"}

    # ------------------------------------------------------------------ #
    # Einstellungen (Nachtmodus)
    # ------------------------------------------------------------------ #

    @app.get("/api/settings")
    async def get_settings(rt: RT) -> dict[str, Any]:
        n = rt.cfg.night
        return {
            "buffer_minutes": rt.cfg.buffer_minutes,
            "night": {
                "enabled": n is not None,
                "from": n.start.strftime("%H:%M") if n else "23:00",
                "to": n.end.strftime("%H:%M") if n else "06:00",
                "mode": n.mode if n else "dim",
            },
        }

    @app.put("/api/settings")
    async def put_settings(rt: RT, body: SettingsIn) -> dict[str, str]:
        cfg = rt.cfg.model_copy(deep=True)
        if body.night.enabled:
            try:
                cfg.night = NightConfig.model_validate(
                    {"from": body.night.start, "to": body.night.end, "mode": body.night.mode}
                )
            except ValueError as e:
                raise HTTPException(422, f"Uhrzeit ungültig: {e}") from e
        else:
            cfg.night = None
        cfg.buffer_minutes = body.buffer_minutes
        rt.save(cfg)
        return {"status": "gespeichert"}

    # ------------------------------------------------------------------ #
    # Stationssuche, Status
    # ------------------------------------------------------------------ #

    @app.get("/api/stations", response_model=list[StationMatch])
    async def stations(
        rt: RT, q: str = Query(..., min_length=2, max_length=100), provider: str | None = None
    ) -> list[StationMatch]:
        p: DepartureProvider = rt.transitous if provider == "transitous" else rt.provider
        try:
            return await p.search_stations(q)
        except ProviderError as e:
            raise HTTPException(502, str(e)) from e

    @app.get("/healthz")
    async def healthz(rt: RT) -> dict[str, Any]:
        errors = {k: v.last_error for k, v in rt.poller.cache.items() if v.last_error}
        return {"status": "ok", "version": __version__, "provider": rt.provider.name, "errors": errors}

    @app.get("/api/status")
    async def status(rt: RT) -> dict[str, Any]:
        needed = rt.needed()
        return {
            "version": __version__,
            "provider": rt.provider.name,
            "attribution": rt.provider.attribution,
            "interval_seconds": rt.poller.interval(len(needed)),
            "notify_enabled": bool(rt.secrets.notify_webhook_url),
            "stations": {
                sid: {
                    "ok": e.last_ok,
                    "error": e.last_error,
                    "failures": e.failures,
                    "requests": e.requests,
                    "departures": len(e.departures),
                    "polling": sid in needed,
                }
                for sid, e in rt.poller.cache.items()
            },
        }

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(WEB / "index.html")

    return app


def _next_two(rows: list[BoardRow]) -> list[BoardRow]:
    return [r for r in rows if not r.cancelled][:2]


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
app = create_app()
