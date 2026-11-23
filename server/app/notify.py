"""„Jetzt los"-Push als generischer Webhook (z. B. an n8n → Discord).

Entprellung: höchstens ein Push pro Fahrt, egal wie viele Geräte das Board abrufen.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

import httpx

from .config import StopConfig
from .models import Departure

log = logging.getLogger(__name__)


class LeaveNotifier:
    def __init__(self, url: str | None, client: httpx.AsyncClient | None = None):
        self.url = url
        self._client = client or httpx.AsyncClient(timeout=5)
        self._sent: dict[str, datetime] = {}

    def should_send(self, dep: Departure, now: datetime) -> bool:
        for k, t in list(self._sent.items()):
            if now - t > timedelta(hours=3):
                del self._sent[k]
        return bool(self.url) and dep.key not in self._sent

    def payload(self, profile: str, title: str, dep: Departure, stop: StopConfig, now: datetime) -> dict:
        minutes = (dep.departure - now).total_seconds() / 60
        return {
            "event": "leave_now",
            "profile": profile,
            "title": title,
            "line": dep.line,
            "direction": dep.direction,
            "stop": stop.name,
            "departure": dep.departure.isoformat(),
            "delay_min": dep.delay_min,
            "minutes_until_departure": round(minutes, 1),
            "walk_minutes": stop.walk_minutes,
            "text": f"Jetzt los: {dep.line} → {dep.direction} ab {stop.name} "
            f"um {dep.departure.astimezone(now.tzinfo):%H:%M} "
            f"({round(minutes)} min, {stop.walk_minutes:g} min Fußweg)",
        }

    async def maybe_send(
        self, profile: str, title: str, dep: Departure, stop: StopConfig, now: datetime
    ) -> bool:
        if not self.should_send(dep, now):
            return False
        self._sent[dep.key] = now  # vor dem Senden markieren: lieber ein Push zu wenig als doppelt
        try:
            r = await self._client.post(self.url, json=self.payload(profile, title, dep, stop, now))  # type: ignore[arg-type]
            r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("Webhook fehlgeschlagen: %s", e)
            return False
        return True

    async def aclose(self) -> None:
        await self._client.aclose()
