"""HTTP zum Server. Nur ``requests`` (auf dem Pi: python3-requests)."""

from __future__ import annotations

from typing import Any

import requests

from . import __version__
from .model import Board


class ApiError(Exception):
    pass


class ServerApi:
    def __init__(self, base_url: str, device: str, timeout: float = 6.0):
        self.base = base_url.rstrip("/")
        self.device = device
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers["User-Agent"] = f"mini-haltestelle-client/{__version__}"

    def _req(self, method: str, path: str, **kw: Any) -> Any:
        try:
            r = self.session.request(method, self.base + path, timeout=self.timeout, **kw)
        except requests.RequestException as e:
            raise ApiError(f"Server nicht erreichbar: {e.__class__.__name__}") from e
        if r.status_code >= 400:
            try:
                detail = r.json().get("detail", r.text)
            except ValueError:
                detail = r.text
            raise ApiError(str(detail)[:120])
        return r.json()

    def board(self) -> Board:
        return Board.from_json(self._req("GET", "/api/board", params={"device": self.device}))

    def step_profile(self, step: int) -> None:
        self._req("POST", f"/api/device/{self.device}/profile", json={"step": step})

    def toggle_night(self) -> None:
        self._req("POST", f"/api/device/{self.device}/night")

    def remind(self) -> None:
        self._req("POST", f"/api/device/{self.device}/remind")

    def health(self) -> dict[str, Any]:
        return self._req("GET", "/healthz")
