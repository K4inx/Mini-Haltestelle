"""Schreibt jedes neue Bild als PNG — zum Anschauen ohne Hardware und für Tests."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .base import Display


class PngDisplay(Display):
    def __init__(self, out_dir: Path, sequence: bool = False):
        super().__init__()
        self.out = out_dir
        self.out.mkdir(parents=True, exist_ok=True)
        self.sequence = sequence
        self.count = 0

    def _push(self, img: Image.Image, box: tuple[int, int, int, int]) -> None:
        if self.frame is None or box != (0, 0, self.width, self.height):
            return  # Lauftext-Teilbilder nicht einzeln speichern
        self.count += 1
        out = self.render_mock()
        out.save(self.out / "frame.png")
        if self.sequence:
            out.save(self.out / f"frame-{self.count:04d}.png")

    def render_mock(self) -> Image.Image:
        """Bild mit simulierter Helligkeit und LED rechts daneben."""
        assert self.frame is not None
        img = Image.new("RGB", (self.width + 24, self.height), "#222")
        frame = self.frame
        if self.backlight < 1:
            frame = Image.eval(frame, lambda v: int(v * max(self.backlight, 0.0)))
        img.paste(frame, (0, 0))
        r, g, b = (int(c * 255) for c in self.led)
        ImageDraw.Draw(img).ellipse(
            (self.width + 6, 112, self.width + 18, 124), fill=(r, g, b) if any(self.led) else "#333"
        )
        return img
