"""Pimoroni Display HAT Mini (ST7789, 320×240) — eigener schlanker Treiber.

Warum nicht Pimorons ``displayhatmini``-Bibliothek? Stand 11/2026 ist sie seit 2022
unverändert (0.0.2), nutzt ``RPi.GPIO`` und zieht per pip ``st7789`` 1.0.1 nach, das
wiederum ``gpiod``/``gpiodevice`` und ``numpy>=1.26.4`` will — auf einem Pi Zero (ARMv6)
heißt das im Zweifel stundenlanges Kompilieren. Dieser Treiber braucht nur Pakete aus apt:
``python3-spidev``, ``python3-numpy``, ``python3-gpiozero``.

Pinbelegung (BCM, laut pinout.xyz und Pimoroni-Bibliothek):
  SPI0 CE1 (spidev0.1), DC = GPIO 9, Backlight = GPIO 13 (PWM)
  Taster A=5, B=6, X=16, Y=24 (gegen Masse, Pull-up)
  RGB-LED R=17, G=27, B=22 (active low)
Init-Sequenz und Fensteradressierung wie in pimoroni/st7789-python.
"""

from __future__ import annotations

import time

from PIL import Image

from .base import Display

PIN_DC = 9
PIN_BACKLIGHT = 13
PIN_BUTTONS = {"A": 5, "B": 6, "X": 16, "Y": 24}
PIN_LED = (17, 27, 22)

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
    import numpy as np

    a = np.asarray(img.convert("RGB"), dtype=np.uint16)
    v = ((a[..., 0] & 0xF8) << 8) | ((a[..., 1] & 0xFC) << 3) | (a[..., 2] >> 3)
    return v.astype(">u2").tobytes()


class HatDisplay(Display):
    def __init__(self, rotation: int = 180, spi_hz: int = 60_000_000):
        """``rotation=180`` entspricht der Montage, wie Pimoroni sie vorsieht (Taster links/rechts)."""
        super().__init__()
        import spidev
        from gpiozero import RGBLED, Button, DigitalOutputDevice, PWMOutputDevice

        self.rotation = rotation % 360
        self.spi = spidev.SpiDev()
        self.spi.open(0, 1)
        self.spi.max_speed_hz = spi_hz
        self.spi.mode = 0
        self.dc = DigitalOutputDevice(PIN_DC)
        self.bl = PWMOutputDevice(PIN_BACKLIGHT, frequency=500, initial_value=0)
        self.rgb = RGBLED(*PIN_LED, active_high=False, pwm=True)
        self.buttons = []
        for name, pin in PIN_BUTTONS.items():
            b = Button(pin, pull_up=True, bounce_time=0.02)
            b.when_pressed = lambda n=name: self.emit(n, True)
            b.when_released = lambda n=name: self.emit(n, False)
            self.buttons.append(b)
        self._init_panel()

    # --- SPI --------------------------------------------------------------
    def _cmd(self, c: int, data: list[int] | bytes | None = None) -> None:
        self.dc.off()
        self.spi.writebytes([c])
        if data:
            self.dc.on()
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
        """Fenster inklusive x0..x1-1 / y0..y1-1."""
        self._cmd(CASET, [x0 >> 8, x0 & 0xFF, (x1 - 1) >> 8, (x1 - 1) & 0xFF])
        self._cmd(RASET, [y0 >> 8, y0 & 0xFF, (y1 - 1) >> 8, (y1 - 1) & 0xFF])
        self._cmd(RAMWR)

    def _push(self, img: Image.Image, box: tuple[int, int, int, int]) -> None:
        x0, y0, x1, y1 = box
        if self.rotation == 180:
            img = img.rotate(180)
            x0, y0, x1, y1 = self.width - x1, self.height - y1, self.width - x0, self.height - y0
        self._window(x0, y0, x1, y1)
        self.dc.on()
        self.spi.writebytes2(to_rgb565(img))

    # --- Licht -----------------------------------------------------------
    def set_backlight(self, value: float) -> None:
        super().set_backlight(value)
        self.bl.value = self.backlight

    def set_led(self, r: float, g: float, b: float) -> None:
        if (r, g, b) != self.led:
            super().set_led(r, g, b)
            self.rgb.color = (r, g, b)

    def close(self) -> None:
        self.set_led(0, 0, 0)
        self.set_backlight(0)
        self.spi.close()
