"""Zeichnet das Board mit Pillow auf 320×240 — identisch für Hardware, Fenster und PNG.

Aufteilung (Pixel):  Kopf 0–27 · Zeilen 28–213 (6 × 31) · Fußzeile 214–239.
Die Fußzeile ist ein eigener Bereich (``FOOTER_BOX``), damit der Lauftext allein neu
gezeichnet werden kann, ohne das ganze Bild über SPI zu schicken.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

from .model import Board, Row

W, H = 320, 240
HEAD_H = 28
ROW_H = 31
FOOT_Y = 214
FOOTER_BOX = (0, FOOT_Y, W, H)
FONT_DIR = Path(__file__).parent / "fonts"


@dataclass(frozen=True)
class Theme:
    bg: str
    ink: str  # Haupttext (DFI: bernstein)
    head: str
    muted: str
    rule: str
    late: str
    very_late: str
    highlight: str  # Zeile der gemerkten Bahn
    bell: str = "#9fe0a8"
    badge_ink: str = "#ffffff"


THEMES = {
    "dark": Theme(
        bg="#0b0d10",
        ink="#f4d35e",
        head="#f4d35e",
        muted="#8b96a3",
        rule="#2a2f36",
        late="#ff9f40",
        very_late="#ff5a4f",
        highlight="#13301b",
    ),
    "light": Theme(
        bg="#f7f7f2",
        ink="#141414",
        head="#141414",
        muted="#6b7076",
        rule="#cfd2cc",
        late="#b85c00",
        very_late="#c0271d",
        highlight="#d9f0dc",
        bell="#1f7a45",
    ),
}


@lru_cache(maxsize=32)
def font(weight: str, size: int) -> ImageFont.FreeTypeFont:
    # BASIC statt Raqm: für lateinische Schrift gleich, aber spürbar schneller
    return ImageFont.truetype(str(FONT_DIR / f"Inter-{weight}.ttf"), size, layout_engine=ImageFont.Layout.BASIC)


# --------------------------------------------------------------------------- #
# Text-Zwischenspeicher
#
# Auf dem Pi Zero kostet jedes ``draw.text`` 20–40 ms (FreeType-Rendering), ``getlength``
# ~3 ms. Fast alle Texte wiederholen sich aber (Ziele, Linien, Quelle, Minutenwerte).
# Deshalb wird jeder Text einmal als Graustufen-Maske gerendert und danach nur noch
# eingefärbt aufgeklebt (``Image.paste`` mit Maske — reines C, < 1 ms).
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=2048)
def tlen(f: ImageFont.FreeTypeFont, text: str) -> float:
    return f.getlength(text)


@lru_cache(maxsize=1024)
def _glyphs(text: str, f: ImageFont.FreeTypeFont, anchor: str) -> tuple[Image.Image | None, int, int]:
    x0, y0, x1, y1 = f.getbbox(text, anchor=anchor)
    if x1 <= x0 or y1 <= y0:
        return None, 0, 0
    m = Image.new("L", (x1 - x0, y1 - y0), 0)
    ImageDraw.Draw(m).text((-x0, -y0), text, font=f, fill=255, anchor=anchor)
    return m, x0, y0


@lru_cache(maxsize=64)
def _rgb(color: str) -> tuple[int, int, int]:
    return ImageColor.getrgb(color)[:3]


class CachedDraw:
    """Wie ``ImageDraw.Draw``, aber ``text`` nutzt den Masken-Zwischenspeicher."""

    def __init__(self, img: Image.Image):
        self.img = img
        self._d = ImageDraw.Draw(img)

    def __getattr__(self, name: str):
        return getattr(self._d, name)

    def text(self, xy, text: str, font: ImageFont.FreeTypeFont, fill: str, anchor: str = "la") -> None:
        m, x0, y0 = _glyphs(text, font, anchor)
        if m is not None:
            self.img.paste(_rgb(fill), (round(xy[0]) + x0, round(xy[1]) + y0), m)


F_HEAD = ("SemiBold", 16)
F_CLOCK = ("Bold", 16)
F_BADGE = ("Bold", 15)
F_DIR = ("Regular", 17)
F_MIN = ("Bold", 18)
F_FOOT = ("Regular", 13)
F_SRC = ("Regular", 10)


def hhmm(t: datetime) -> str:
    return t.astimezone().strftime("%H:%M") if t.tzinfo else t.strftime("%H:%M")


def ellipsize(text: str, f: ImageFont.FreeTypeFont, max_w: float) -> str:
    if tlen(f, text) <= max_w:
        return text
    while text and tlen(f, text + "…") > max_w:
        text = text[:-1]
    return text.rstrip() + "…"


def fit_keep_tail(text: str, f: ImageFont.FreeTypeFont, max_w: float, sep: str = " ca. ") -> str:
    """Wie ``ellipsize``, kürzt aber den Teil vor ``sep`` — die Uhrzeit dahinter bleibt stehen."""
    if tlen(f, text) <= max_w or sep not in text:
        return ellipsize(text, f, max_w)
    head, _, tail = text.rpartition(sep)
    tail = sep + tail
    return ellipsize(head, f, max_w - tlen(f, tail)) + tail


def minutes_label(row: Row, now: datetime) -> str:
    m = int((row.departure - now).total_seconds() // 60)
    tilde = "" if row.realtime else "~"
    if m <= 0:
        return "jetzt"
    if m >= 60:
        return tilde + hhmm(row.departure)
    return f"{tilde}{m}'"


class Renderer:
    def __init__(self, theme: str = "dark"):
        self.t = THEMES.get(theme, THEMES["dark"])
        self._strip_cache: tuple[str, int, Image.Image, int] | None = None

    def _strip(self, text: str, height: int, cy: int) -> tuple[Image.Image, int]:
        """Lauftext einmal vorzeichnen (Text doppelt hintereinander); pro Schritt nur ausschneiden."""
        if self._strip_cache and self._strip_cache[:2] == (text, height):
            return self._strip_cache[2], self._strip_cache[3]
        ff = font(*F_FOOT)
        period = int(tlen(ff, text)) + 40
        img = Image.new("RGB", (2 * period + W, height), self.t.bg)
        d = CachedDraw(img)
        for k in range(3):
            d.text((8 + k * period, cy), text, font=ff, fill=self.t.muted, anchor="lm")
        self._strip_cache = (text, height, img, period)
        return img, period

    # ------------------------------------------------------------------ #
    # Bausteine
    # ------------------------------------------------------------------ #

    def _canvas(self) -> tuple[Image.Image, CachedDraw]:
        img = Image.new("RGB", (W, H), self.t.bg)
        return img, CachedDraw(img)

    def _header(self, d: CachedDraw, title: str, now: datetime, bell: datetime | None = None) -> None:
        clock = now.strftime("%H:%M")
        fc = font(*F_CLOCK)
        cw = tlen(fc, clock)
        right = W - 8 - cw
        d.text((W - 8, HEAD_H // 2), clock, font=fc, fill=self.t.head, anchor="rm")
        if bell is not None:
            fb = font("SemiBold", 13)
            txt = hhmm(bell)
            tw = tlen(fb, txt)
            x = right - 10 - tw
            d.text((x, HEAD_H // 2), txt, font=fb, fill=self.t.bell, anchor="lm")
            self._bell(d, x - 16, 7, self.t.bell)
            right = x - 20
        fh = font(*F_HEAD)
        d.text((8, HEAD_H // 2), ellipsize(title, fh, right - 16), font=fh, fill=self.t.head, anchor="lm")
        d.line((0, HEAD_H - 1, W, HEAD_H - 1), fill=self.t.rule)

    @staticmethod
    def _bell(d: CachedDraw, x: int, y: int, color: str) -> None:
        """Kleine Glocke (12×14) — Emoji gibt es in der Schrift nicht."""
        d.pieslice((x + 1, y, x + 11, y + 10), 180, 360, fill=color)
        d.rectangle((x + 1, y + 5, x + 11, y + 10), fill=color)
        d.rectangle((x - 1, y + 10, x + 13, y + 11), fill=color)
        d.ellipse((x + 4, y + 12, x + 8, y + 15), fill=color)

    def _badge(
        self,
        d: CachedDraw,
        x: int,
        cy: int,
        row_line: str,
        product: str,
        color: str,
        size: tuple[int, int] = (46, 24),
        f: ImageFont.FreeTypeFont | None = None,
    ) -> int:
        f = f or font(*F_BADGE)
        w = max(size[0], int(tlen(f, row_line)) + 10)
        h = size[1]
        box = (x, cy - h // 2, x + w, cy + h // 2)
        radius = h // 2 if product == "S" else 4  # S-Bahn rund, wie am Bahnsteig
        d.rounded_rectangle(box, radius=radius, fill=color)
        d.text(((box[0] + box[2]) / 2, cy), row_line, font=f, fill=self.t.badge_ink, anchor="mm")
        return w

    def _late_color(self, delay: int) -> str:
        return self.t.very_late if delay >= 5 else self.t.late if delay >= 2 else self.t.ink

    # ------------------------------------------------------------------ #
    # Fußzeile (eigener Bereich, Lauftext per Offset)
    # ------------------------------------------------------------------ #

    def footer_text(self, board: Board, now: datetime, view: str) -> tuple[str, bool]:
        """(Text, läuft?) — Meldungen laufen, sonst Ankunft der nächsten Bahn."""
        if board.messages:
            return "   ·   ".join(board.messages), True
        if view == "list":
            r = board.focus_row(now)
            if r and r.arrival:
                dest = f" {board.destination}" if board.destination else ""
                return f"Ankunft{dest} ca. {hhmm(r.arrival)}", False
        return "", False

    def footer(self, board: Board | None, now: datetime, view: str, offset: int = 0) -> Image.Image:
        img = Image.new("RGB", (W, H - FOOT_Y), self.t.bg)
        d = CachedDraw(img)
        d.line((0, 0, W, 0), fill=self.t.rule)
        if board is None:
            return img
        fs = font(*F_SRC)
        src = board.source
        sw = int(tlen(fs, src)) + 8 if src else 0
        cy = (H - FOOT_Y) // 2 + 1
        text, scrolling = self.footer_text(board, now, view)
        ff = font(*F_FOOT)
        area_w = W - sw - 8
        if board.stale and board.stale_since:  # Offline-Hinweis hat Vorrang vor Lauftext
            mins = max(1, int((now - board.stale_since).total_seconds() // 60))
            tag = f"offline seit {mins} min"
            fo = font("SemiBold", 12)
            tw = tlen(fo, tag) + 12
            d.rounded_rectangle((5, 4, 5 + tw, H - FOOT_Y - 4), radius=3, fill="#b3261e")
            d.text((5 + tw / 2, cy), tag, font=fo, fill="#ffffff", anchor="mm")
        elif scrolling:
            strip, period = self._strip(text, H - FOOT_Y - 1, cy - 1)
            x = offset % period
            img.paste(strip.crop((x, 0, x + area_w, strip.height)), (0, 1))
        elif text:
            d.text((8, cy), fit_keep_tail(text, ff, area_w - 8), font=ff, fill=self.t.muted, anchor="lm")
        if src:
            d.text((W - 6, cy), src, font=fs, fill=self.t.muted, anchor="rm")
        return img

    @staticmethod
    def footer_scrolls(text_scrolling: bool) -> bool:
        return text_scrolling

    # ------------------------------------------------------------------ #
    # Ansichten
    # ------------------------------------------------------------------ #

    def list_view(self, board: Board, now: datetime, footer_offset: int = 0) -> Image.Image:
        img, d = self._canvas()
        self._header(d, board.title, now, board.reminder.leave_at if board.reminder else None)
        rows = board.visible_rows(now)[:6]
        if not rows:
            msg = "keine Abfahrt, die du noch schaffst" if board.profile else "Im Web-UI eine Vorlage anlegen"
            d.text((W // 2, 120), msg, font=font("Regular", 15), fill=self.t.muted, anchor="mm")
        fd, fm = font(*F_DIR), font(*F_MIN)
        for i, r in enumerate(rows):
            y0 = HEAD_H + i * ROW_H
            cy = y0 + ROW_H // 2
            if board.reminder and r.line == board.reminder.line and r.departure == board.reminder.departure:
                d.rectangle((0, y0, W, y0 + ROW_H - 1), fill=self.t.highlight)
            bw = self._badge(d, 6, cy, r.line, r.product, r.color)
            label = "fällt aus" if r.cancelled else minutes_label(r, now)
            mw = tlen(fm, label)
            color = self.t.muted if r.cancelled else self._late_color(r.delay_min)
            d.text((W - 7, cy), label, font=fm, fill=color, anchor="rm")
            x = 6 + bw + 8
            text = ellipsize(r.direction, fd, W - 7 - mw - 8 - x)
            d.text((x, cy), text, font=fd, fill=self.t.muted if r.cancelled else self.t.ink, anchor="lm")
            if r.cancelled:
                tw = tlen(fd, text)
                d.line((x, cy, x + tw, cy), fill=self.t.muted, width=2)
        img.paste(self.footer(board, now, "list", footer_offset), (0, FOOT_Y))
        return img

    def detail_view(self, board: Board, now: datetime, footer_offset: int = 0) -> Image.Image:
        img, d = self._canvas()
        self._header(d, board.title, now, board.reminder.leave_at if board.reminder else None)
        r = board.focus_row(now)
        if r is None:
            d.text(
                (W // 2, 120),
                "keine Abfahrt, die du noch schaffst",
                font=font("Regular", 15),
                fill=self.t.muted,
                anchor="mm",
            )
        else:
            y = HEAD_H + 22
            fb = font("Bold", 18)
            bw = self._badge(d, 10, y, r.line, r.product, r.color, size=(54, 28), f=fb)
            d.text((10 + bw + 10, y), ellipsize(r.direction, fb, W - bw - 40), font=fb, fill=self.t.ink, anchor="lm")
            y += 24
            sub = f"ab {r.stop}" + (f" · Gleis {r.platform}" if r.platform else "")
            d.text(
                (10, y),
                ellipsize(sub, font("Regular", 14), W - 20),
                font=font("Regular", 14),
                fill=self.t.muted,
                anchor="lm",
            )
            y += 34
            mins = int((r.departure - now).total_seconds() // 60)
            big = "jetzt" if mins <= 0 else f"in {mins} min"
            fbig = font("Bold", 38)
            d.text((10, y), big, font=fbig, fill=self._late_color(r.delay_min), anchor="lm")
            t = ("" if r.realtime else "~") + hhmm(r.departure)
            fx = 10 + tlen(fbig, big) + 14
            d.text((fx, y + 4), t, font=font("SemiBold", 18), fill=self.t.ink, anchor="lm")
            if r.delay_min > 0:
                d.text(
                    (fx + tlen(font("SemiBold", 18), t) + 6, y + 4),
                    f"+{r.delay_min}",
                    font=font("SemiBold", 18),
                    fill=self._late_color(r.delay_min),
                    anchor="lm",
                )
            y += 34
            fl, fv = font("Regular", 14), font("SemiBold", 14)
            leave = r.leave_at or (r.departure - timedelta(minutes=r.walk_minutes))
            self._kv(d, y, "Losgehen spätestens", hhmm(leave), fl, fv)
            y += 22
            if r.arrival:
                dest = board.destination or "Ziel"
                extra = "" if r.transfers is None else (" · direkt" if r.transfers == 0 else f" · {r.transfers}× um")
                self._kv(d, y, f"Ankunft {dest}", f"ca. {hhmm(r.arrival)}{extra}", fl, fv)
                y += 22
            if not r.realtime:
                d.text(
                    (10, y),
                    "~ nur Fahrplan, keine Live-Daten",
                    font=font("Regular", 12),
                    fill=self.t.muted,
                    anchor="lm",
                )
        img.paste(self.footer(board, now, "detail", footer_offset), (0, FOOT_Y))
        return img

    def _kv(
        self, d: CachedDraw, y: int, k: str, v: str, fk: ImageFont.FreeTypeFont, fv: ImageFont.FreeTypeFont
    ) -> None:
        vw = tlen(fv, v)
        d.text((10, y), ellipsize(k, fk, W - 30 - vw), font=fk, fill=self.t.muted, anchor="lm")
        d.text((W - 10, y), v, font=fv, fill=self.t.ink, anchor="rm")

    def info_view(self, info: list[tuple[str, str]], now: datetime) -> Image.Image:
        img, d = self._canvas()
        self._header(d, "Info", now)
        fk, fv = font("Regular", 14), font("SemiBold", 14)
        for i, (k, v) in enumerate(info[:8]):
            y = HEAD_H + 18 + i * 23
            d.text((10, y), k, font=fk, fill=self.t.muted, anchor="lm")
            d.text((110, y), ellipsize(v, fv, W - 120), font=fv, fill=self.t.ink, anchor="lm")
        d.text((W // 2, H - 12), "Y halten = zurück", font=font("Regular", 11), fill=self.t.muted, anchor="mm")
        return img

    def message(self, title: str, text: str = "", now: datetime | None = None, error: bool = False) -> Image.Image:
        """Start-, Fehler- und Hinweisbildschirm mit Haltestellen-„H"."""
        img, d = self._canvas()
        cx, cy = W // 2, 82
        d.ellipse((cx - 34, cy - 34, cx + 34, cy + 34), fill="#b3261e" if error else "#1f8a3c")
        d.text((cx, cy + 1), "!" if error else "H", font=font("Bold", 44), fill="#f6d200", anchor="mm")
        d.text((cx, 146), title, font=font("SemiBold", 18), fill=self.t.ink, anchor="mm")
        fy = 172
        for line in _wrap(text, font("Regular", 13), W - 30)[:3]:
            d.text((cx, fy), line, font=font("Regular", 13), fill=self.t.muted, anchor="mm")
            fy += 18
        if now is not None:
            d.text((W - 8, 14), now.strftime("%H:%M"), font=font(*F_CLOCK), fill=self.t.head, anchor="rm")
        return img


def _wrap(text: str, f: ImageFont.FreeTypeFont, max_w: float) -> list[str]:
    lines: list[str] = []
    cur = ""
    for word in text.split():
        trial = f"{cur} {word}".strip()
        if tlen(f, trial) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines
