"""Hauptschleife: Daten holen, Tasten auswerten, nur Geändertes zeichnen, Licht steuern.

- Abruf im Hintergrund-Thread (alle ``poll_seconds`` oder sofort nach einer Aktion), damit
  ein langsamer Server nie die Anzeige blockiert.
- Countdown läuft lokal aus ``departure``; ganzes Bild höchstens einmal pro Sekunde und nur
  übertragen, wenn es sich geändert hat. Der Lauftext aktualisiert nur die Fußzeile.
- Server weg: letzte Daten bleiben stehen (Countdown läuft weiter) mit „offline seit X min";
  nach ``offline_error_minutes`` ein klarer Fehler-Screen.
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image

from . import __version__
from .api import ApiError
from .config import ClientConfig
from .display.base import Display
from .model import Board
from .render import FOOTER_BOX, Renderer

log = logging.getLogger(__name__)

LED_COLORS = {"ideal": (0.0, 1.0, 0.0), "hurry": (1.0, 0.45, 0.0)}
MARQUEE_PX_PER_S = 30
TICK_MARQUEE = 0.15  # ~7 Bilder/s: auf dem Zero ein guter Kompromiss aus Ruhe und CPU
TICK_IDLE = 0.25


def local_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # kein Paket wird gesendet, nur Route bestimmt
            return s.getsockname()[0]
    except OSError:
        return "–"


def wifi_signal(path: Path = Path("/proc/net/wireless")) -> str:
    try:
        for line in path.read_text().splitlines()[2:]:
            parts = line.split()
            if parts and parts[0].startswith("wlan"):
                dbm = int(float(parts[3].rstrip(".")))
                q = "sehr gut" if dbm > -55 else "gut" if dbm > -67 else "mäßig" if dbm > -75 else "schwach"
                return f"{dbm} dBm ({q})"
    except (OSError, ValueError, IndexError):
        pass
    return "–"


class App:
    def __init__(
        self,
        cfg: ClientConfig,
        api: Any,
        display: Display,
        renderer: Renderer | None = None,
        *,
        mono: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now().astimezone(),
    ):
        self.cfg, self.api, self.display = cfg, api, display
        self.r = renderer or Renderer(cfg.theme)
        self.mono, self.now = mono, now
        self.lock = threading.Lock()
        self.board: Board | None = None
        self.last_ok: datetime | None = None
        self.last_error: str | None = None
        self.view = "list"
        self.wake_until = 0.0
        self.flash_text: str | None = None
        self.flash_until = 0.0
        self._pressed: dict[str, float] = {}
        self._long_fired: set[str] = set()
        self._refresh = threading.Event()
        self._stop = threading.Event()
        self._last_full: bytes | None = None
        self._marquee_start = 0.0
        self._marquee_key: tuple[Any, ...] | None = None
        self._dirty = True
        self._data_version = 0
        self._last_key: tuple[Any, ...] | None = None

    # ------------------------------------------------------------------ #
    # Daten
    # ------------------------------------------------------------------ #

    def fetch_once(self) -> None:
        try:
            b = self.api.board()
        except ApiError as e:
            with self.lock:
                self.last_error = str(e)
            log.warning("Abruf fehlgeschlagen: %s", e)
        else:
            with self.lock:
                self.board, self.last_ok, self.last_error = b, self.now(), None
        self._data_version += 1
        self._dirty = True

    def _poll_loop(self) -> None:
        while not self._stop.is_set():
            self.fetch_once()
            self._refresh.wait(self.cfg.poll_seconds)
            self._refresh.clear()

    def start(self) -> None:
        threading.Thread(target=self._poll_loop, name="poll", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        self._refresh.set()

    def effective_board(self) -> Board | None:
        """Board mit lokal erkanntem Verbindungsverlust (Server meldet ja nichts mehr)."""
        with self.lock:
            b, ok, err = self.board, self.last_ok, self.last_error
        if b is None:
            return None
        if err and ok:
            since = min(filter(None, [b.stale_since, ok]))
            return replace(b, stale=True, stale_since=since)
        return b

    def offline_too_long(self, now: datetime) -> bool:
        with self.lock:
            ok, err = self.last_ok, self.last_error
        return bool(err and ok and (now - ok).total_seconds() > self.cfg.offline_error_minutes * 60)

    # ------------------------------------------------------------------ #
    # Tasten
    # ------------------------------------------------------------------ #

    def process_buttons(self) -> None:
        while not self.display.events.empty():
            ev = self.display.events.get_nowait()
            if ev.pressed:
                self._pressed[ev.name] = ev.at
                self._long_fired.discard(ev.name)
            else:
                start = self._pressed.pop(ev.name, None)
                if start is not None and ev.name not in self._long_fired:
                    self.on_short(ev.name)
        t = self.mono()
        for name, start in list(self._pressed.items()):
            if name not in self._long_fired and t - start >= self.cfg.long_press_seconds:
                self._long_fired.add(name)
                self.on_long(name)

    def _consume_wake(self) -> bool:
        """Nachts weckt der erste Druck nur das Display."""
        b = self.board
        t = self.mono()
        if b is not None and b.display_mode != "on" and t >= self.wake_until:
            self.wake_until = t + self.cfg.wake_seconds
            self._dirty = True
            return True
        if t < self.wake_until:
            self.wake_until = t + self.cfg.wake_seconds
        return False

    def on_short(self, name: str) -> None:
        if self._consume_wake():
            return
        if self.view == "info":
            self.view = "list"
        elif name == "A":
            self._action(self.api.step_profile, -1)
        elif name == "B":
            self._action(self.api.step_profile, 1)
        elif name == "X":
            self.view = "list" if self.view == "detail" else "detail"
        elif name == "Y":
            self._action(self.api.remind)
        self._dirty = True

    def on_long(self, name: str) -> None:
        if self._consume_wake():
            return
        if name == "X":
            self._action(self.api.toggle_night)
        elif name == "Y":
            self.view = "list" if self.view == "info" else "info"
        self._dirty = True

    def _action(self, fn: Callable[..., Any], *args: Any) -> None:
        def run() -> None:
            try:
                fn(*args)
            except ApiError as e:
                self.flash(str(e))
            self._refresh.set()  # sofort neu abrufen

        threading.Thread(target=run, daemon=True).start()

    def flash(self, text: str, seconds: float = 2.5) -> None:
        self.flash_text, self.flash_until = text, self.mono() + seconds
        self._dirty = True

    # ------------------------------------------------------------------ #
    # Zeichnen
    # ------------------------------------------------------------------ #

    def info(self) -> list[tuple[str, str]]:
        with self.lock:
            ok, err = self.last_ok, self.last_error
        server = "erreichbar" if not err else "Fehler"
        return [
            ("Gerät", self.cfg.device_id),
            ("IP", local_ip()),
            ("WLAN", wifi_signal()),
            ("Server", f"{server} · {self.cfg.server_url.split('//')[-1]}"),
            ("Stand", ok.strftime("%H:%M:%S") if ok else "–"),
            ("Fehler", err or "–"),
            ("Version", __version__),
        ]

    def frame(self, now: datetime, footer_offset: int = 0) -> Image.Image:
        if self.flash_text and self.mono() < self.flash_until:
            return self.r.message(self.flash_text, now=now)
        self.flash_text = None
        if self.view == "info":
            return self.r.info_view(self.info(), now)
        b = self.effective_board()
        if b is None:
            with self.lock:
                err = self.last_error
            return self.r.message("verbinde …", err or self.cfg.server_url, now=now)
        if self.offline_too_long(now):
            assert self.last_ok is not None
            return self.r.message(
                "Keine Verbindung",
                f"Seit {self.last_ok.strftime('%H:%M')} kein Kontakt zum Server {self.cfg.server_url}",
                now=now,
                error=True,
            )
        if self.view == "detail":
            return self.r.detail_view(b, now, footer_offset)
        return self.r.list_view(b, now, footer_offset)

    def _marquee(self, now: datetime) -> tuple[bool, int]:
        b = self.effective_board()
        if b is None or self.view == "info" or self.flash_text or (b.stale and b.stale_since):
            return False, 0
        text, scrolling = self.r.footer_text(b, now, self.view)
        if not scrolling:
            return False, 0
        key = (text, self.view)
        if key != self._marquee_key:
            self._marquee_key, self._marquee_start = key, self.mono()
        return True, int((self.mono() - self._marquee_start) * MARQUEE_PX_PER_S)

    def lights(self) -> None:
        b = self.board
        t = self.mono()
        awake = t < self.wake_until
        if b is None:
            self.display.set_backlight(0.8)
            self.display.set_led(0, 0, 0)
            return
        mode = "on" if awake else b.display_mode
        self.display.set_backlight(0.0 if mode == "off" else (0.8 if awake else b.brightness))
        color = LED_COLORS.get(b.leave_signal) if b.display_mode == "on" else None  # nachts nie
        if color and b.reminder:  # Erinnerung: blinken, knapp = schneller
            period = 0.4 if b.leave_signal == "hurry" else 1.0
            on = (t % period) < period / 2
            color = color if on else None
        k = self.cfg.led_brightness
        self.display.set_led(*((c * k for c in color) if color else (0, 0, 0)))

    def content_key(self, now: datetime) -> tuple[Any, ...]:
        """Alles, wovon das Vollbild abhängt. Nur wenn sich das ändert, wird neu gezeichnet —
        im Alltag einmal pro Minute (Uhr, Countdown) oder bei neuen Daten/Tastendruck."""
        flash = self.flash_text if self.flash_text and self.mono() < self.flash_until else None
        return (
            self.view,
            now.strftime("%H:%M"),
            self._data_version,
            flash,
            self.offline_too_long(now),
            self.last_error is None,
            self.view == "info" and int(self.mono() // 5),
        )

    def step(self) -> float:
        """Eine Runde; gibt die Wartezeit bis zur nächsten zurück."""
        self.process_buttons()
        now = self.now()
        scrolling, offset = self._marquee(now)
        key = self.content_key(now)
        if self._dirty or key != self._last_key:
            self._dirty = False
            self._last_key = key
            img = self.frame(now, offset)
            raw = img.tobytes()
            if raw != self._last_full:
                self._last_full = raw
                self.display.show(img)
        elif scrolling:
            b = self.effective_board()
            if b is not None:
                self.display.show_region(self.r.footer(b, now, self.view, offset), FOOTER_BOX)
        self.lights()
        return TICK_MARQUEE if scrolling or self._pressed else TICK_IDLE

    def run(self) -> None:
        self.display.show(self.r.message("verbinde …", self.cfg.server_url))
        self.display.set_backlight(0.8)
        self.start()
        try:
            while self.display.pump():
                time.sleep(self.step())
        finally:
            self.stop()
            self.display.close()
