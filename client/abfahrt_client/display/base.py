from __future__ import annotations

import queue
import time
from dataclasses import dataclass

from PIL import Image

BUTTONS = ("A", "B", "X", "Y")


@dataclass
class ButtonEvent:
    name: str  # A | B | X | Y
    pressed: bool  # True = gedrückt, False = losgelassen
    at: float  # time.monotonic()


class Display:
    """Gemeinsame Schnittstelle. Unterklassen überschreiben ``_push`` und ggf. Licht/Tasten."""

    width = 320
    height = 240

    def __init__(self) -> None:
        self.events: queue.Queue[ButtonEvent] = queue.Queue()
        self.frame: Image.Image | None = None
        self.backlight = 1.0
        self.led = (0.0, 0.0, 0.0)

    # Bild -------------------------------------------------------------
    def show(self, img: Image.Image) -> None:
        self.frame = img.copy()
        self._push(img, (0, 0, self.width, self.height))

    def show_region(self, img: Image.Image, box: tuple[int, int, int, int]) -> None:
        """Nur einen Ausschnitt aktualisieren (Lauftext) — spart SPI-Bandbreite."""
        if self.frame is None:
            return
        self.frame.paste(img, box[:2])
        self._push(img, box)

    def _push(self, img: Image.Image, box: tuple[int, int, int, int]) -> None:
        raise NotImplementedError

    # Licht ------------------------------------------------------------
    def set_backlight(self, value: float) -> None:
        self.backlight = max(0.0, min(1.0, value))

    def set_led(self, r: float, g: float, b: float) -> None:
        self.led = (r, g, b)

    # Tasten / Lebenszyklus ---------------------------------------------
    def emit(self, name: str, pressed: bool) -> None:
        self.events.put(ButtonEvent(name, pressed, time.monotonic()))

    def pump(self) -> bool:
        """Für Fenster: Ereignisse abholen. False = Benutzer hat geschlossen."""
        return True

    def close(self) -> None:
        pass
