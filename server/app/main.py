"""FastAPI-App: Endpunkte, Lebenszyklus, Verdrahtung."""

from __future__ import annotations

import logging
import os
import secrets as pysecrets
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from . import __version__, board, setup_assistant
from .config import AppConfig, Secrets, load_config, parse_config_text, resolve_provider
from .devices import DeviceStore
from .models import Board, StationMatch
from .notify import LeaveNotifier
from .poller import Poller
from .providers import DepartureProvider, ProviderError, TransitousProvider, make_provider

log = logging.getLogger("abfahrtsanzeige")
WEB = Path(__file__).parent / "web"


@dataclass
class Runtime:
    config_path: Path
    cfg: AppConfig
    secrets: Secrets
    provider: DepartureProvider
    transitous: TransitousProvider  # für Assistent + Stationssuche, unabhängig vom Haupt-Provider
    poller: Poller
    devices: DeviceStore
    notifier: LeaveNotifier

    def now(self) -> datetime:
        return datetime.now(ZoneInfo(self.cfg.timezone))

    def station_ids(self, profile: str) -> dict[str, list[str]]:
        """Stationsname → IDs beim aktiven Provider."""
        return {s.name: s.ids_for(self.provider.name) for s in self.cfg.profiles[profile].stops}

    def needed(self) -> set[str]:
        """Stationen, die der Poller gerade abfragen soll: aktive Profile aktiver Geräte."""
        now = self.now()
        ids: set[str] = set()
        for dev in self.devices.active_devices(now):
            night = self.devices.night(self.cfg, dev, now)
            if night and self.cfg.night and self.cfg.night.mode == "off":
                continue  # Display aus → keine Abfragen
            for sid_list in self.station_ids(self.devices.profile(self.cfg, dev, now)).values():
                ids.update(sid_list)
        return ids


def build_runtime(config_path: Path, data_dir: Path) -> Runtime:
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
        poller=None,
        devices=DeviceStore(data_dir / "devices.json"),  # type: ignore[arg-type]
        notifier=LeaveNotifier(sec.notify_webhook_url),
    )
    rt.poller = Poller(provider, lambda: rt.cfg, rt.needed)
    return rt


def rt_dep(request: Request) -> Runtime:
    return request.app.state.rt


RT = Annotated[Runtime, Depends(rt_dep)]


def require_token(rt: RT, x_edit_token: Annotated[str | None, Header()] = None) -> None:
    expected = rt.secrets.edit_token
    if not expected:
        raise HTTPException(403, "Bearbeiten deaktiviert: EDIT_TOKEN ist nicht gesetzt")
    if not x_edit_token or not pysecrets.compare_digest(x_edit_token, expected):
        raise HTTPException(401, "Token fehlt oder ist falsch")


Token = Annotated[None, Depends(require_token)]


class ProfileChange(BaseModel):
    profile: str | None = None
    step: int | None = None  # +1 / -1 für Taster B / A


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
        night = rt.devices.night(rt.cfg, device, now)
        ids_by_stop = rt.station_ids(profile)
        all_ids = {i for ids in ids_by_stop.values() for i in ids}
        await rt.poller.ensure_loaded(all_ids)
        deps_by_stop = {
            name: [d for sid in ids for d in rt.poller.departures(sid)] for name, ids in ids_by_stop.items()
        }
        result, trigger = board.build_board(
            rt.cfg,
            profile,
            deps_by_stop,
            now,
            night=night,
            stale_since=rt.poller.stale_since(all_ids),
            source=rt.provider.attribution,
        )
        p = rt.cfg.profiles[profile]
        if trigger and p.leave and p.leave.notify and result.leave_signal == "ideal":
            await rt.notifier.maybe_send(profile, p.title, trigger[0], trigger[1], now)
        return result

    @app.post("/api/device/{device}/profile")
    async def change_profile(rt: RT, device: str, body: ProfileChange) -> dict[str, str]:
        now = rt.now()
        try:
            if body.profile:
                rt.devices.set_profile(rt.cfg, device, body.profile, now)
            elif body.step:
                rt.devices.step_profile(rt.cfg, device, body.step, now)
            else:
                raise HTTPException(422, "profile oder step angeben")
        except KeyError:
            raise HTTPException(404, f"Profil unbekannt: {body.profile}") from None
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
    # Stationssuche, Status
    # ------------------------------------------------------------------ #

    @app.get("/api/stations", response_model=list[StationMatch])
    async def stations(
        rt: RT, q: str = Query(..., min_length=2, max_length=100), provider: str | None = None
    ) -> list[StationMatch]:
        p: DepartureProvider = rt.provider
        if provider == "transitous":
            p = rt.transitous
        elif provider == "rmv" and rt.provider.name != "rmv":
            raise HTTPException(400, "RMV nicht aktiv (RMV_API_KEY fehlt)")
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
        now = rt.now()
        needed = rt.needed()
        return {
            "version": __version__,
            "provider": rt.provider.name,
            "provider_configured": rt.cfg.provider,
            "attribution": rt.provider.attribution,
            "interval_seconds": rt.poller.interval(len(needed)),
            "editing_enabled": bool(rt.secrets.edit_token),
            "notify_enabled": bool(rt.secrets.notify_webhook_url),
            "profiles": list(rt.cfg.profiles),
            "devices": {
                d: {
                    "profile": rt.devices.profile(rt.cfg, d, now),
                    "night": rt.devices.night(rt.cfg, d, now),
                    "last_seen": rt.devices.get(d).last_seen,
                }
                for d in rt.devices.devices
            },
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

    # ------------------------------------------------------------------ #
    # Config bearbeiten (Token)
    # ------------------------------------------------------------------ #

    @app.get("/api/config", response_class=PlainTextResponse)
    async def get_config(rt: RT, _: Token) -> str:
        return rt.config_path.read_text(encoding="utf-8")

    @app.put("/api/config")
    async def put_config(
        rt: RT, _: Token, text: Annotated[str, Body(media_type="text/plain")]
    ) -> dict[str, str]:
        try:
            new = parse_config_text(text)
        except Exception as e:  # YAML- oder Validierungsfehler → zurück ans UI
            raise HTTPException(422, f"Config ungültig: {e}") from e
        if resolve_provider(new, rt.secrets) != rt.provider.name:
            raise HTTPException(422, "Provider-Wechsel braucht einen Neustart des Containers")
        backup = rt.config_path.with_suffix(".yaml.bak")
        backup.write_text(rt.config_path.read_text(encoding="utf-8"), encoding="utf-8")
        rt.config_path.write_text(text, encoding="utf-8")
        rt.cfg = new
        return {"status": "gespeichert", "backup": backup.name}

    # ------------------------------------------------------------------ #
    # Einrichtungs-Assistent (Token, weil Adressen nach außen gehen)
    # ------------------------------------------------------------------ #

    @app.get("/api/setup/nearby")
    async def setup_nearby(rt: RT, _: Token, address: str = Query(..., min_length=3), radius: int = 800):
        try:
            home = await setup_assistant.geocode(rt.transitous, address)
            return {
                "home": home,
                "stops": await setup_assistant.nearby(rt.transitous, home, min(radius, 2000)),
            }
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        except ProviderError as e:
            raise HTTPException(502, str(e)) from e

    @app.get("/api/setup/route")
    async def setup_route(
        rt: RT, _: Token, home: str = Query(..., min_length=3), work: str = Query(..., min_length=3)
    ):
        try:
            h = await setup_assistant.geocode(rt.transitous, home)
            w = await setup_assistant.geocode(rt.transitous, work)
            return {"home": h, "work": w, "suggestions": await setup_assistant.route(rt.transitous, h, w)}
        except LookupError as e:
            raise HTTPException(404, str(e)) from e
        except ProviderError as e:
            raise HTTPException(502, str(e)) from e

    # ------------------------------------------------------------------ #
    # Web-UI
    # ------------------------------------------------------------------ #

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(WEB / "index.html")

    return app


logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
app = create_app()
