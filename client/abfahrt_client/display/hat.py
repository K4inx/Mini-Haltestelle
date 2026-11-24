"""Pimoroni Display HAT Mini (ST7789, 320×240) — eigener schlanker Treiber.

Warum nicht Pimorons ``displayhatmini``-Bibliothek? Stand 11/2026 ist sie seit 2022
unverändert (0.0.2), nutzt ``RPi.GPIO`` und zieht per pip ``st7789`` 1.0.1 nach, das
wiederum ``gpiod``/``gpiodevice`` und ``numpy>=1.26.4`` will — auf einem Pi Zero (ARMv6)
heißt das im Zweifel stundenlanges Kompilieren. Dieser Treiber braucht nur Pakete aus apt:
``python3-spidev``, ``python3-numpy``, ``python3-lgpio``.

GPIO bewusst direkt über ``lgpio`` statt gpiozero — am echten Zero gemessen (24.11.2026):
gpiozero-Taster kosten dauerhaft ~12 % CPU (Überwachungs-Thread), eigenes Abfragen alle
25 ms ~1,5 %. Software-PWM (lgpio) läuft nur, solange sie gebraucht wird.

Pinbelegung (BCM, laut pinout.xyz und Pimoroni-Bibliothek):
  SPI0 CE1 (spidev0.1), DC = GPIO 9, Backlight = GPIO 13
  Taster A=5, B=6, X=16, Y=24 (gegen Masse, Pull-up)
  RGB-LED R=17, G=27, B=22 (active low)
Init-Sequenz und Fensteradressierung wie in pimoroni/st7789-python.
"""

from __future__ import annotations

import threading
import time

from PIL import Image

from .base import Display

PIN_DC = 9
PIN_BACKLIGHT = 13
PIN_BUTTONS = {"A": 5, "B": 6, "X": 16, "Y": 24}
PIN_LED = (17, 27, 22)
BUTTON_POLL_S = 0.025

# ST7789-Befehle
SWRESET, SLPOUT, INVON, DISPON = 0x01, 0x11, 0x21, 0x29
CASET, RASET, RAMWR, MADCTL, COLMOD = 0x2A, 0x2B, 0x2C, 0x36, 0x3A
INIT = [
    (MADCTL, [0x70]),  # Querformat 320×240
    (0xB2, [0x0C, 0x0C, 0x00, 0x33, 0x33]),  # FRMCTR2 Porch
    (COLMOD, [0x05]),  # 16 bit RGB565
    (0xB7, [0x14]),  # GCTRL
    (0xBB, [0x37]),  # VCOMS
    (0xC0, [0x2C]),  # LCMCTRL
    (0xC2, [0x01]),  # VDVVRHEN
    (0xC3, [0x12]),  # VRHS
    (0xC4, [0x20]),  # VDVS
    (0xD0, [0xA4, 0xA1]),  # PWCTRL1
    (0xC6, [0x0F]),  # FRCTRL2 60 Hz
    (0xE0, [0xD0, 0x04, 0x0D, 0x11, 0x13, 0x2B, 0x3F, 0x54, 0x4C, 0x18, 0x0D, 0x0B, 0x1F, 0x23]),
    (0xE1, [0xD0, 0x04, 0x0C, 0x11, 0x13, 0x2C, 0x3F, 0x44, 0x51, 0x2F, 0x1F, 0x1F, 0x20, 0x23]),
]


def to_rgb565(img: Image.Image) -> bytes:
    """RGB565 big-endian (gleiches Ergebnis wie ``render.rgb565``, für Vollbilder schneller)."""
    import numpy as np

    a = np.asarray(img.convert("RGB"), dtype=np.uint16)
    v = ((a[..., 0] & 0xF8) << 8) | ((a[..., 1] & 0xFC) << 3) | (a[..., 2] >> 3)
    return v.astype(">u2").tobytes()


class HatDisplay(Display):
    supports_raw = True

    def __init__(self, rotation: int = 180, spi_hz: int = 60_000_000):
        """``rotation=180`` entspricht der Montage, wie Pimoroni sie vorsieht (Taster links/rechts)."""
        super().__init__()
        import lgpio
        import spidev

        self.lg = lgpio
        self.rotation = rotation % 360
        self._dc_state: bool | None = None
        self._win: tuple[int, int, int, int] | None = None
        self._bl_set = False

        self.spi = spidev.SpiDev()
        self.spi.open(0, 1)
        self.spi.max_speed_hz = spi_hz
        self.spi.mode = 0

        self.h = lgpio.gpiochip_open(0)
        lgpio.gpio_claim_output(self.h, PIN_DC, 0)
        lgpio.gpio_claim_output(self.h, PIN_BACKLIGHT, 0)
        for pin in PIN_LED:
            lgpio.gpio_claim_output(self.h, pin, 1)  # active low → 1 = aus
        for pin in PIN_BUTTONS.values():
            lgpio.gpio_claim_input(self.h, pin, lgpio.SET_PULL_UP)

        self._stop = threading.Event()
        threading.Thread(target=self._poll_buttons, name="buttons", daemon=True).start()
        self._init_panel()

    # --- GPIO -------------------------------------------------------------
    def _set_dc(self, data: bool) -> None:
        if self._dc_state is not data:  # nur bei echtem Wechsel
            self.lg.gpio_write(self.h, PIN_DC, 1 if data else 0)
            self._dc_state = data

    def _poll_buttons(self) -> None:
        """Alle 25 ms lesen; ein Zustand gilt, wenn er zweimal hintereinander gleich ist (Entprellen)."""
        names = list(PIN_BUTTONS)
        stable = dict.fromkeys(names, False)
        last = dict.fromkeys(names, False)
        while not self._stop.is_set():
            for n in names:
                pressed = self.lg.gpio_read(self.h, PIN_BUTTONS[n]) == 0  # Taster ziehen nach Masse
                if pressed == last[n] and pressed != stable[n]:
                    stable[n] = pressed
                    self.emit(n, pressed)
                last[n] = pressed
            time.sleep(BUTTON_POLL_S)

    def _pwm(self, pin: int, level: float, active_low: bool = False, freq: int = 500) -> None:
        """0 % und 100 % digital (keine Software-PWM), dazwischen lgpio-PWM."""
        on = 1 - level if active_low else level
        if on <= 0.005 or on >= 0.995:
            self.lg.tx_pwm(self.h, pin, 0, 0)
            self.lg.gpio_write(self.h, pin, 1 if on >= 0.995 else 0)
        else:
            self.lg.tx_pwm(self.h, pin, freq, round(on * 100, 1))

    # --- SPI --------------------------------------------------------------
    def _cmd(self, c: int, data: list[int] | bytes | None = None) -> None:
        self._set_dc(False)
        self.spi.writebytes([c])
        if data:
            self._set_dc(True)
            self.spi.writebytes2(bytes(data))

    def _init_panel(self) -> None:
        self._cmd(SWRESET)
        time.sleep(0.15)
        for c, d in INIT:
            self._cmd(c, d)
        self._cmd(INVON)
        self._cmd(SLPOUT)
        self._cmd(DISPON)
        time.sleep(0.1)

    def _window(self, x0: int, y0: int, x1: int, y1: int) -> None:
        """Fenster inkl. x0..x1-1 / y0..y1-1. Gleiches Fenster → nur RAMWR (Schreibzeiger auf Anfang)."""
        if self._win == (x0, y0, x1, y1):
            self._cmd(RAMWR)
            return
        self._win = (x0, y0, x1, y1)
        self._cmd(CASET, [x0 >> 8, x0 & 0xFF, (x1 - 1) >> 8, (x1 - 1) & 0xFF])
        self._cmd(RASET, [y0 >> 8, y0 & 0xFF, (y1 - 1) >> 8, (y1 - 1) & 0xFF])
        self._cmd(RAMWR)

    def _rotate_box(self, box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        x0, y0, x1, y1 = box
        if self.rotation == 180:
            return self.width - x1, self.height - y1, self.width - x0, self.height - y0
        return box

    def _push(self, img: Image.Image, box: tuple[int, int, int, int]) -> None:
        if self.rotation == 180:
            img = img.rotate(180)
        self._window(*self._rotate_box(box))
        self._set_dc(True)
        self.spi.writebytes2(to_rgb565(img))

    def push_raw(self, data: bytes, box: tuple[int, int, int, int]) -> None:
        if self.rotation == 180:  # Pixelreihenfolge umdrehen, die zwei Bytes je Pixel bleiben
            data = memoryview(data).cast("H")[::-1].tobytes()
        self._window(*self._rotate_box(box))
        self._set_dc(True)
        self.spi.writebytes2(data)

    # --- Licht -----------------------------------------------------------
    def set_backlight(self, value: float) -> None:
        old = self.backlight
        super().set_backlight(value)
        if self.backlight != old or not self._bl_set:
            self._pwm(PIN_BACKLIGHT, self.backlight)
            self._bl_set = True

    def set_led(self, r: float, g: float, b: float) -> None:
        if (r, g, b) != self.led:
            super().set_led(r, g, b)
            for pin, v in zip(PIN_LED, (r, g, b), strict=True):
                self._pwm(pin, v, active_low=True, freq=2000)

    def close(self) -> None:
        self._stop.set()
        self.set_led(0, 0, 0)
        self.set_backlight(0)
        self.spi.close()
        self.lg.gpiochip_close(self.h)
