"""Fenster auf dem PC (pygame): Display doppelt so groß, Tasten A/B/X/Y auf der Tastatur.

Y und Z sind beide Taste Y (deutsche Tastatur). Halten = langer Druck, wie am Gerät.
"""

from __future__ import annotations

from PIL import Image

from .base import Display

SCALE = 2
PAD = 16
KEYS = {"a": "A", "b": "B", "x": "X", "y": "Y", "z": "Y"}


class WindowDisplay(Display):
    def __init__(self, title: str = "Mini-Haltestelle (Mock)"):
        super().__init__()
        import pygame

        self.pg = pygame
        pygame.init()
        self.size = (self.width * SCALE + 2 * PAD + 40, self.height * SCALE + 2 * PAD + 40)
        self.win = pygame.display.set_mode(self.size)
        pygame.display.set_caption(title)
        self.hint_font = pygame.font.SysFont(None, 22)
        self._held: set[str] = set()

    def _push(self, img: Image.Image, box: tuple[int, int, int, int]) -> None:
        self._redraw()

    def set_backlight(self, value: float) -> None:
        super().set_backlight(value)
        self._redraw()

    def set_led(self, r: float, g: float, b: float) -> None:
        if (r, g, b) != self.led:
            super().set_led(r, g, b)
            self._redraw()

    def _redraw(self) -> None:
        pg = self.pg
        self.win.fill((34, 34, 34))
        if self.frame is not None:
            frame = self.frame
            if self.backlight < 1:
                k = max(self.backlight, 0.0)
                frame = Image.eval(frame, lambda v: int(v * k))
            surf = pg.image.fromstring(frame.tobytes(), frame.size, "RGB")
            surf = pg.transform.scale(surf, (self.width * SCALE, self.height * SCALE))
            self.win.blit(surf, (PAD, PAD))
        x = PAD + self.width * SCALE + 20
        color = tuple(int(c * 255) for c in self.led) if any(self.led) else (60, 60, 60)
        pg.draw.circle(self.win, color, (x, PAD + self.height * SCALE // 2), 9)
        hint = "A/B Vorlage  ·  X Details (halten: Nacht)  ·  Y Erinnern (halten: Info)  ·  Esc Ende"
        held = "  ".join(sorted(self._held))
        self.win.blit(self.hint_font.render(hint, True, (200, 200, 200)), (PAD, self.size[1] - 34))
        if held:
            self.win.blit(self.hint_font.render("gedrückt: " + held, True, (159, 224, 168)), (PAD, self.size[1] - 56))
        pg.display.flip()

    def pump(self) -> bool:
        pg = self.pg
        for ev in pg.event.get():
            if ev.type == pg.QUIT or (ev.type == pg.KEYDOWN and ev.key == pg.K_ESCAPE):
                return False
            if ev.type in (pg.KEYDOWN, pg.KEYUP):
                name = KEYS.get(pg.key.name(ev.key).lower())
                if name:
                    pressed = ev.type == pg.KEYDOWN
                    if pressed and name in self._held:
                        continue  # Tastenwiederholung des Betriebssystems ignorieren
                    (self._held.add if pressed else self._held.discard)(name)
                    self.emit(name, pressed)
                    self._redraw()
        return True

    def close(self) -> None:
        self.pg.quit()
