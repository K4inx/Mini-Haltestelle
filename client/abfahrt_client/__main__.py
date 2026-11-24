"""Start:  python -m abfahrt_client [--mock | --png DIR] [--demo] [--config PATH]

Ohne Schalter: echte Hardware (Display HAT Mini) mit /etc/abfahrtsanzeige/client.yaml.
  --mock          Fenster auf dem PC, Tasten A/B/X/Y (bzw. Z) auf der Tastatur
  --png DIR       schreibt das Bild nach DIR/frame.png (ohne Fenster, z. B. per SSH)
  --demo          Beispieldaten statt Server
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import __version__
from .config import DEFAULT_PATH, ClientConfig


class DemoApi:
    """Server-Ersatz für ``--demo``: Beispieldaten, Tasten wirken lokal."""

    def __init__(self) -> None:
        self.reminder = False
        self.night = False
        self.profile = 0

    def board(self):
        from .demo import demo_board_json
        from .model import Board

        d = demo_board_json(reminder=self.reminder)
        d["title"] = d["profile"] = d["profiles"][self.profile]
        if self.night:
            d["display"] = {"mode": "dim", "brightness": 0.05}
            d["leave_signal"] = "none"
        return Board.from_json(d)

    def step_profile(self, step: int) -> None:
        self.profile = (self.profile + step) % 2

    def toggle_night(self) -> None:
        self.night = not self.night

    def remind(self) -> None:
        self.reminder = not self.reminder


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="abfahrt_client", description="Mini-Haltestelle — Display-Client")
    p.add_argument("--config", type=Path, default=None, help=f"Standard: {DEFAULT_PATH}, falls vorhanden")
    p.add_argument("--mock", action="store_true", help="Fenster auf dem PC statt Hardware")
    p.add_argument("--png", type=Path, metavar="DIR", help="Bilder als PNG nach DIR schreiben")
    p.add_argument("--demo", action="store_true", help="Beispieldaten statt Server")
    p.add_argument("--server", help="Server-URL überschreiben, z. B. http://<server-ip>:8080")
    p.add_argument("--device", help="Geräte-ID überschreiben")
    p.add_argument("--theme", choices=["dark", "light"])
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("-v", "--verbose", action="store_true")
    a = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if a.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    cfg = ClientConfig.load(a.config or (DEFAULT_PATH if DEFAULT_PATH.exists() else None))
    if a.server:
        cfg.server_url = a.server.rstrip("/")
    if a.device:
        cfg.device_id = a.device
    if a.theme:
        cfg.theme = a.theme

    if a.mock:
        from .display.window import WindowDisplay

        display = WindowDisplay()
    elif a.png:
        from .display.png import PngDisplay

        display = PngDisplay(a.png)
    else:
        from .display.hat import HatDisplay

        display = HatDisplay(rotation=180 if cfg.rotation == 0 else 0)

    if a.demo:
        api = DemoApi()
    else:
        from .api import ServerApi

        api = ServerApi(cfg.server_url, cfg.device_id, cfg.request_timeout)

    from .app import App

    App(cfg, api, display).run()


if __name__ == "__main__":
    main()
