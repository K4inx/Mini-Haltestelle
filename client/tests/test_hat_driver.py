"""HAT-Treiber mit nachgebautem lgpio/spidev — prüft Logik, nicht die echte Hardware."""

from __future__ import annotations

import sys
import time
import types

import pytest
from PIL import Image


class FakeLgpio(types.ModuleType):
    SET_PULL_UP = 32

    def __init__(self):
        super().__init__("lgpio")
        self.levels: dict[int, int] = {}
        self.writes: list[tuple[int, int]] = []
        self.pwm: dict[int, tuple[float, float]] = {}

    def gpiochip_open(self, n):
        return 1

    def gpiochip_close(self, h):
        pass

    def gpio_claim_output(self, h, pin, level):
        self.levels[pin] = level

    def gpio_claim_input(self, h, pin, flags):
        self.levels[pin] = 1  # Pull-up: nicht gedrückt

    def gpio_write(self, h, pin, level):
        self.levels[pin] = level
        self.writes.append((pin, level))

    def gpio_read(self, h, pin):
        return self.levels[pin]

    def tx_pwm(self, h, pin, freq, duty):
        if freq == 0:
            self.pwm.pop(pin, None)
        else:
            self.pwm[pin] = (freq, duty)


class FakeSpi:
    def __init__(self):
        self.sent: list[bytes] = []

    def open(self, a, b):
        pass

    def writebytes(self, b):
        self.sent.append(bytes(b))

    def writebytes2(self, b):
        self.sent.append(bytes(b))

    def close(self):
        pass


@pytest.fixture
def hat(monkeypatch):
    lg = FakeLgpio()
    spi_mod = types.ModuleType("spidev")
    spi_mod.SpiDev = FakeSpi
    monkeypatch.setitem(sys.modules, "lgpio", lg)
    monkeypatch.setitem(sys.modules, "spidev", spi_mod)
    monkeypatch.setitem(sys.modules, "numpy", pytest.importorskip("numpy"))
    from abfahrt_client.display.hat import HatDisplay

    d = HatDisplay(rotation=180)
    yield d, lg
    d.close()


def wait_for(pred, timeout=1.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_buttons_debounced_press_and_release(hat):
    d, lg = hat
    lg.levels[16] = 0  # X gedrückt
    assert wait_for(lambda: not d.events.empty())
    ev = d.events.get()
    assert (ev.name, ev.pressed) == ("X", True)
    lg.levels[16] = 1
    assert wait_for(lambda: not d.events.empty())
    assert d.events.get().pressed is False


def test_backlight_full_and_off_without_pwm(hat):
    d, lg = hat
    d.set_backlight(1.0)
    assert 13 not in lg.pwm and lg.levels[13] == 1
    d.set_backlight(0.05)
    assert lg.pwm[13] == (500, 5.0)
    d.set_backlight(0.0)
    assert 13 not in lg.pwm and lg.levels[13] == 0


def test_led_active_low_and_off_is_digital(hat):
    d, lg = hat
    d.set_led(0, 0.3, 0)
    assert lg.levels[17] == 1 and lg.levels[22] == 1  # rot/blau aus = high
    assert lg.pwm[27] == (2000, 70.0)  # 30 % an = 70 % high
    d.set_led(0, 0, 0)
    assert not lg.pwm and lg.levels[27] == 1


def test_same_window_skips_caset_raset(hat):
    d, _lg = hat
    img = Image.new("RGB", (320, 26), "red")
    d.show(Image.new("RGB", (320, 240)))
    d.spi.sent.clear()
    d.push_raw(b"\x00\x01" * (320 * 26), (0, 214, 320, 240))
    first = list(d.spi.sent)
    d.spi.sent.clear()
    d.push_raw(b"\x00\x01" * (320 * 26), (0, 214, 320, 240))
    assert bytes([0x2A]) in first and bytes([0x2B]) in first
    assert d.spi.sent[0] == bytes([0x2C]) and bytes([0x2A]) not in d.spi.sent  # nur RAMWR
    # 180°: Bereich wird gespiegelt (Fußzeile liegt physisch oben)
    assert d._win == (0, 0, 320, 26)
    assert img.size == (320, 26)
