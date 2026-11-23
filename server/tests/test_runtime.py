"""Poller, Gerätezustand, Push-Entprellung und HTTP-Endpunkte — ohne echte API-Aufrufe."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest
import respx
import yaml
from fastapi.testclient import TestClient

from app.config import AppConfig, Secrets
from app.devices import DeviceStore
from app.main import Runtime, create_app
from app.models import Departure, LeaveSignal, Product
from app.notify import LeaveNotifier
from app.poller import Poller
from app.providers import ProviderError

from .conftest import local


class FakeProvider:
    name = "transitous"
    attribution = "Transitous"
    min_interval_seconds = 30.0

    def __init__(self):
        self.calls: list[str] = []
        self.fail = False
        self.deps: list[Departure] = []

    async def get_departures(self, station_id, limit=30):
        self.calls.append(station_id)
        if self.fail:
            raise ProviderError("down")
        return list(self.deps)

    async def search_stations(self, query, limit=10):
        return []

    async def aclose(self):
        pass


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


# ---------------------------------------------------------------- Poller --- #


def make_poller(cfg_dict, provider, needed):
    cfg = AppConfig.model_validate(cfg_dict)
    clock = Clock()
    return Poller(provider, lambda: cfg, lambda: needed, clock=clock), clock


async def drain(p: Poller):
    p.tick()
    await asyncio.gather(*list(p._inflight.values()))


async def test_poller_respects_interval_and_caches(cfg_dict):
    prov = FakeProvider()
    p, clock = make_poller(cfg_dict, prov, {"a", "b"})
    await drain(p)
    assert sorted(prov.calls) == ["a", "b"]
    clock.t += 10
    await drain(p)
    assert len(prov.calls) == 2  # noch nicht fällig
    clock.t += 25
    await drain(p)
    assert len(prov.calls) == 4


async def test_poller_backoff_and_stale(cfg_dict):
    prov = FakeProvider()
    p, clock = make_poller(cfg_dict, prov, {"a"})
    await drain(p)
    assert p.stale_since({"a"}) is None
    prov.fail = True
    clock.t += 30
    await drain(p)
    assert p.stale_since({"a"}) is not None
    assert p.cache["a"].failures == 1
    clock.t += 30
    await drain(p)
    assert p.cache["a"].failures == 2
    clock.t += 30  # Backoff jetzt 60 s → nicht fällig
    await drain(p)
    assert p.cache["a"].failures == 2
    prov.fail = False
    clock.t += 60
    await drain(p)
    assert p.stale_since({"a"}) is None


def test_rmv_budget_interval(cfg_dict):
    prov = FakeProvider()
    prov.name = "rmv"
    p, _ = make_poller(cfg_dict, prov, set())
    assert p.interval(1) == 30
    assert p.interval(2) == pytest.approx(86400 * 2 / 4500)  # 38,4 s
    assert p.interval(6) == pytest.approx(115.2)


async def test_refresh_never_faster_than_provider_allows(cfg_dict):
    prov = FakeProvider()
    p, clock = make_poller(cfg_dict, prov, {"a"})
    await drain(p)
    clock.t += 5
    p.request_refresh({"a"})
    await drain(p)
    assert len(prov.calls) == 1


# ---------------------------------------------------------------- Geräte --- #


def test_manual_profile_until_next_schedule_change(cfg_dict, tmp_path):
    cfg = AppConfig.model_validate(cfg_dict)
    store = DeviceStore(tmp_path / "d.json")
    mon_7 = local("2026-11-23T07:00")
    assert store.profile(cfg, "pi", mon_7) == "morgens"
    assert store.step_profile(cfg, "pi", +1, mon_7) == "abends"
    assert store.profile(cfg, "pi", local("2026-11-23T09:59")) == "abends"
    assert store.profile(cfg, "pi", local("2026-11-24T07:00")) == "morgens"  # Override abgelaufen
    # persistiert
    assert DeviceStore(tmp_path / "d.json").get("pi").profile == "abends"


def test_manual_night_toggle(cfg_dict, tmp_path):
    cfg = AppConfig.model_validate(cfg_dict)
    store = DeviceStore(tmp_path / "d.json")
    t = local("2026-11-23T23:30")
    assert store.night(cfg, "pi", t) is True
    assert store.toggle_night(cfg, "pi", t) is False
    assert store.night(cfg, "pi", local("2026-11-24T05:00")) is False
    assert store.night(cfg, "pi", local("2026-11-24T23:30")) is True  # nächste Nacht wieder automatisch


# ------------------------------------------------------------------ Push --- #


@respx.mock
async def test_notifier_once_per_departure():
    route = respx.post("http://n8n/webhook").mock(return_value=httpx.Response(200))
    n = LeaveNotifier("http://n8n/webhook")
    now = local("2026-11-23T08:00")
    d = Departure(
        line="U4",
        product=Product.U,
        direction="Bockenheimer Warte",
        stop="Merianplatz",
        planned=now + timedelta(minutes=8),
        trip_id="T1",
    )
    from app.config import StopConfig

    s = StopConfig(name="Merianplatz", ids={}, walk_minutes=5)
    assert await n.maybe_send("morgens", "t", d, s, now)
    assert not await n.maybe_send("morgens", "t", d, s, now + timedelta(seconds=30))
    assert route.call_count == 1
    body = route.calls.last.request.content.decode()
    assert "Jetzt los: U4" in body
    await n.aclose()


async def test_notifier_disabled_without_url():
    n = LeaveNotifier(None)
    assert not n.should_send(
        Departure(line="x", product=Product.U, direction="", stop="", planned=local("2026-11-23T08:00")),
        local("2026-11-23T08:00"),
    )
    await n.aclose()


# ------------------------------------------------------------------ HTTP --- #


@pytest.fixture
def client(cfg_dict, tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg_dict, allow_unicode=True), encoding="utf-8")
    cfg = AppConfig.model_validate(cfg_dict)
    prov = FakeProvider()
    sec = Secrets(edit_token="geheim")
    rt = Runtime(
        config_path=cfg_path,
        cfg=cfg,
        secrets=sec,
        provider=prov,
        transitous=prov,  # type: ignore[arg-type]
        poller=None,
        devices=DeviceStore(tmp_path / "d.json"),
        notifier=LeaveNotifier(None),
    )  # type: ignore[arg-type]
    rt.poller = Poller(prov, lambda: rt.cfg, rt.needed)
    fixed = local("2026-11-23T07:30")
    rt.now = lambda: fixed  # type: ignore[method-assign]
    prov.deps = [
        Departure(
            line="U4",
            product=Product.U,
            direction="Frankfurt (Main) Bockenheimer Warte",
            stop="Frankfurt (Main) Merianplatz",
            planned=fixed + timedelta(minutes=8),
            trip_id="T1",
        )
    ]
    with TestClient(create_app(rt, start_poller=False)) as c:
        c.rt = rt  # type: ignore[attr-defined]
        yield c


def test_board_endpoint(client):
    r = client.get("/api/board", params={"device": "pi"})
    assert r.status_code == 200
    b = r.json()
    assert b["profile"] == "morgens" and b["title"] == "Zuhause → Arbeit"
    assert b["profiles"] == ["morgens", "abends", "wochenende"]
    assert b["rows"][0]["line"] == "U4" and b["rows"][0]["direction"] == "Bockenheimer Warte"
    assert b["leave_signal"] == LeaveSignal.IDEAL
    assert b["stale"] is False and b["source"] == "Transitous"
    assert b["display"] == {"mode": "on", "brightness": 0.8}


def test_profile_switch_endpoint(client):
    assert client.post("/api/device/pi/profile", json={"step": 1}).json() == {"profile": "abends"}
    assert client.get("/api/board", params={"device": "pi"}).json()["profile"] == "abends"
    assert client.post("/api/device/pi/profile", json={"profile": "gibtsnicht"}).status_code == 404
    assert client.post("/api/device/pi/night").json() == {"night": True}


def test_config_requires_token(client):
    assert client.get("/api/config").status_code == 401
    assert client.get("/api/config", headers={"X-Edit-Token": "falsch"}).status_code == 401
    text = client.get("/api/config", headers={"X-Edit-Token": "geheim"}).text
    bad = client.put(
        "/api/config",
        content="default_profile: nix\nprofiles: {}\n",
        headers={"X-Edit-Token": "geheim", "Content-Type": "text/plain"},
    )
    assert bad.status_code == 422
    new = text.replace("Zuhause → Arbeit", "Zur Arbeit")
    ok = client.put(
        "/api/config", content=new.encode(), headers={"X-Edit-Token": "geheim", "Content-Type": "text/plain"}
    )
    assert ok.status_code == 200
    assert client.get("/api/board", params={"device": "pi"}).json()["title"] == "Zur Arbeit"


def test_editing_disabled_without_token(client):
    client.rt.secrets = Secrets()
    assert client.get("/api/config", headers={"X-Edit-Token": "x"}).status_code == 403


def test_healthz_and_index(client):
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/").status_code == 200
