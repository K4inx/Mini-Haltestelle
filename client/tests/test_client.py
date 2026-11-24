from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from PIL import Image

from abfahrt_client.api import ApiError
from abfahrt_client.app import App, wifi_signal
from abfahrt_client.config import ClientConfig
from abfahrt_client.demo import demo_board_json
from abfahrt_client.display.base import Display
from abfahrt_client.model import Board
from abfahrt_client.render import FOOTER_BOX, Renderer, minutes_label

NOW = datetime(2026, 11, 24, 7, 30).astimezone()


class MemDisplay(Display):
    def __init__(self):
        super().__init__()
        self.pushes: list[tuple[int, int, int, int]] = []

    def _push(self, img, box):
        self.pushes.append(box)


class FakeApi:
    def __init__(self, board=None):
        self.b = board or Board.from_json(demo_board_json(NOW))
        self.fail = False
        self.calls: list[str] = []

    def board(self):
        if self.fail:
            raise ApiError("Server nicht erreichbar")
        return self.b

    def step_profile(self, step):
        self.calls.append(f"step{step:+d}")

    def toggle_night(self):
        self.calls.append("night")

    def remind(self):
        self.calls.append("remind")


class Clock:
    def __init__(self):
        self.mono = 100.0
        self.dt = NOW


@pytest.fixture
def env(monkeypatch):
    clock = Clock()
    api = FakeApi()
    disp = MemDisplay()
    app = App(ClientConfig(), api, disp, mono=lambda: clock.mono, now=lambda: clock.dt)
    # Aktionen synchron ausführen statt im Thread
    monkeypatch.setattr(app, "_action", lambda fn, *a: fn(*a))
    return app, api, disp, clock


def press(app, disp, clock, name, hold=0.1):
    disp.emit(name, True)
    disp.events.queue[-1].at = clock.mono
    app.process_buttons()
    clock.mono += hold
    app.process_buttons()
    disp.emit(name, False)
    app.process_buttons()


# ------------------------------------------------------------- Modell --- #


def test_board_parsing_and_visible_rows():
    b = Board.from_json(demo_board_json(NOW))
    assert len(b.rows) == 6 and b.destination == "Höchst"
    # S1 in 9 min, losgehen spätestens 9 − 6 − 2 = in 1 min → nach 2 min ausgeblendet
    assert b.visible_rows(NOW + timedelta(minutes=2))[0].line == "59"
    assert b.focus_row(NOW).line == "S1"


def test_minutes_label():
    b = Board.from_json(demo_board_json(NOW))
    assert minutes_label(b.rows[0], NOW) == "9'"
    assert minutes_label(b.rows[5], NOW).startswith("~")  # Fahrplan, ≥ 60 min → Uhrzeit
    assert minutes_label(b.rows[0], NOW + timedelta(minutes=10)) == "jetzt"


def test_config_load(tmp_path):
    p = tmp_path / "client.yaml"
    p.write_text("server_url: http://x:8080/\ndevice_id: flur\nunbekannt: 1\n")
    c = ClientConfig.load(p)
    assert c.server_url == "http://x:8080" and c.device_id == "flur" and c.extra == {"unbekannt": 1}
    assert ClientConfig.load(None).device_id == "pi"


# ------------------------------------------------------------- Render --- #


@pytest.mark.parametrize("theme", ["dark", "light"])
def test_all_views_render(theme):
    r = Renderer(theme)
    for kw in ({}, {"reminder": True}, {"stale": True}, {"messages": False}):
        b = Board.from_json(demo_board_json(NOW, **kw))
        for img in (r.list_view(b, NOW), r.detail_view(b, NOW), r.footer(b, NOW, "list", 40)):
            assert isinstance(img, Image.Image)
    assert r.info_view([("IP", "1.2.3.4")], NOW).size == (320, 240)
    assert r.message("verbinde …", "x" * 300).size == (320, 240)


def test_empty_board_renders():
    b = Board.from_json({"profile": "", "profiles": [], "title": "Keine Vorlage", "rows": []})
    Renderer().list_view(b, NOW)
    Renderer().detail_view(b, NOW)


# ---------------------------------------------------------------- App --- #


def test_boot_then_board(env):
    app, api, disp, _clock = env
    api.fail = True
    app.fetch_once()
    app.step()
    assert app.board is None and len(disp.pushes) == 1  # „verbinde …"
    api.fail = False
    app.fetch_once()
    app.step()
    assert app.board is not None and len(disp.pushes) == 2


def test_only_changed_frames_are_sent(env):
    app, api, disp, clock = env
    api.b.messages = []  # kein Lauftext
    app.fetch_once()
    app.step()
    n = len(disp.pushes)
    clock.mono += 1.5
    app.step()  # gleiche Minute, nichts geändert
    assert len(disp.pushes) == n
    clock.dt += timedelta(minutes=1)
    clock.mono += 1.5
    app.step()
    assert len(disp.pushes) == n + 1


def test_marquee_updates_only_footer(env):
    app, _api, disp, clock = env
    app.fetch_once()
    app.step()
    clock.mono += 0.2
    app.step()
    assert disp.pushes[-1] == FOOTER_BOX


def test_short_and_long_presses(env):
    app, api, disp, clock = env
    app.fetch_once()
    press(app, disp, clock, "A")
    press(app, disp, clock, "B")
    press(app, disp, clock, "Y")
    assert api.calls == ["step-1", "step+1", "remind"]
    press(app, disp, clock, "X")
    assert app.view == "detail"
    press(app, disp, clock, "X")
    assert app.view == "list"
    press(app, disp, clock, "X", hold=3.1)
    assert api.calls[-1] == "night" and app.view == "list"  # langer Druck löst keinen kurzen aus
    press(app, disp, clock, "Y", hold=3.1)
    assert app.view == "info"
    press(app, disp, clock, "A")
    assert app.view == "list" and api.calls[-1] == "night"  # Info verlassen, ohne Aktion


def test_night_first_press_only_wakes(env):
    app, api, disp, clock = env
    api.b.display_mode, api.b.brightness = "dim", 0.05
    app.fetch_once()
    app.step()
    assert disp.backlight == pytest.approx(0.05)
    press(app, disp, clock, "A")
    assert api.calls == []
    app.step()
    assert disp.backlight == pytest.approx(0.8)
    press(app, disp, clock, "A")
    assert api.calls == ["step-1"]
    clock.mono += 31
    app.step()
    assert disp.backlight == pytest.approx(0.05)


def test_led_blinks_for_reminder_and_off_at_night(env):
    app, api, disp, clock = env
    api.b = Board.from_json(demo_board_json(NOW, reminder=True))
    app.fetch_once()
    clock.mono = 100.0
    app.lights()
    first = disp.led
    clock.mono = 100.5
    app.lights()
    assert {first, disp.led} == {(0, 0, 0), (0.0, 0.3, 0.0)}
    api.b.display_mode = "dim"
    clock.mono = 100.0
    app.lights()
    assert disp.led == (0, 0, 0)


def test_offline_keeps_data_then_error(env):
    app, api, _disp, clock = env
    app.fetch_once()
    api.fail = True
    clock.dt += timedelta(minutes=3)
    app.fetch_once()
    b = app.effective_board()
    assert b.stale and b.stale_since == NOW
    assert not app.offline_too_long(clock.dt)
    clock.dt += timedelta(minutes=8)
    assert app.offline_too_long(clock.dt)
    img = app.frame(clock.dt)
    assert img.size == (320, 240)


def test_flash_on_api_error(env):
    app, api, _disp, _clock = env
    app.fetch_once()

    def boom():
        raise ApiError("Keine Abfahrt, die du noch schaffst")

    api.remind = boom
    app._action = App._action.__get__(app)  # echter Thread-Pfad
    app.on_short("Y")
    import time as _t

    for _ in range(50):
        if app.flash_text:
            break
        _t.sleep(0.01)
    assert app.flash_text == "Keine Abfahrt, die du noch schaffst"


def test_wifi_signal_parsing(tmp_path):
    p = tmp_path / "wireless"
    p.write_text(
        "Inter-| sta-|   Quality\n face | tus | link level noise\n wlan0: 0000   48.  -62.  -256 0 0 0 0 0 0\n"
    )
    assert wifi_signal(p) == "-62 dBm (gut)"
    assert wifi_signal(tmp_path / "fehlt") == "–"


def test_rgb565_and_rotation_window():
    from abfahrt_client.display.hat import to_rgb565

    img = Image.new("RGB", (2, 1), (255, 0, 0))
    img.putpixel((1, 0), (0, 0, 255))
    assert to_rgb565(img) == bytes([0xF8, 0x00, 0x00, 0x1F])


def test_footer_keeps_arrival_time_with_long_destination():
    from abfahrt_client.render import fit_keep_tail, font

    f = font("Regular", 13)
    text = "Ankunft Varisano Klinikum Frankfurt Höchst, Haupteingang ca. 07:58"
    out = fit_keep_tail(text, f, 220)
    assert out.endswith("… ca. 07:58") and f.getlength(out) <= 220
    assert fit_keep_tail("Ankunft Höchst ca. 07:58", f, 220) == "Ankunft Höchst ca. 07:58"


def test_placeholder_server_url_gives_clear_error():
    from abfahrt_client.api import ServerApi

    with pytest.raises(ApiError, match="Server-Adresse fehlt"):
        ServerApi("http://<server-ip>:8080", "pi").board()
