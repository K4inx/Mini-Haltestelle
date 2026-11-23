"""Hintergrund-Poller mit In-Memory-Cache.

Nur der Poller ruft den Provider auf — egal wie viele Clients fragen. Pro Station gilt:
- Mindestabstand ``provider.min_interval_seconds`` (Transitous-Richtlinie: 30 s),
- konfiguriertes Intervall ``poll.interval_seconds``,
- bei RMV zusätzlich ein Tagesbudget: Intervall = 86400 · Stationen / Budget,
- Fehler → Backoff (verdoppelt, max. 10 min), letzte gültige Daten bleiben im Cache.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .config import AppConfig
from .models import Departure
from .providers import DepartureProvider, ProviderError

log = logging.getLogger(__name__)

MAX_BACKOFF = 600.0


@dataclass
class CacheEntry:
    departures: list[Departure] = field(default_factory=list)
    last_ok: datetime | None = None
    last_ok_mono: float | None = None
    last_attempt_mono: float | None = None
    last_error: str | None = None
    first_failure: datetime | None = None
    failures: int = 0
    next_due: float = 0.0
    requests: int = 0


class Poller:
    def __init__(
        self,
        provider: DepartureProvider,
        get_config: Callable[[], AppConfig],
        needed: Callable[[], set[str]],
        clock: Callable[[], float] = time.monotonic,
    ):
        self.provider = provider
        self._cfg = get_config
        self._needed = needed
        self._clock = clock
        self.cache: dict[str, CacheEntry] = {}
        self._inflight: dict[str, asyncio.Task[None]] = {}
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------ #

    def interval(self, n_stations: int) -> float:
        cfg = self._cfg()
        base = max(float(cfg.poll.interval_seconds), self.provider.min_interval_seconds)
        if self.provider.name == "rmv" and n_stations:
            base = max(base, 86400 * n_stations / max(1, cfg.poll.rmv_daily_budget))
        return base

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="poller")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        for t in list(self._inflight.values()):
            t.cancel()
        await asyncio.gather(
            *(t for t in [self._task, *self._inflight.values()] if t), return_exceptions=True
        )

    async def _run(self) -> None:
        while True:
            try:
                self.tick()
            except Exception:  # Poller darf nie sterben
                log.exception("Poller-Tick fehlgeschlagen")
            await asyncio.sleep(1)

    def tick(self) -> None:
        needed = self._needed()
        now = self._clock()
        for sid in needed:
            entry = self.cache.setdefault(sid, CacheEntry())
            if now >= entry.next_due and sid not in self._inflight:
                self._inflight[sid] = asyncio.create_task(self._fetch(sid, len(needed)))

    async def _fetch(self, sid: str, n_stations: int) -> None:
        entry = self.cache[sid]
        cfg = self._cfg()
        interval = self.interval(n_stations)
        try:
            entry.last_attempt_mono = self._clock()
            entry.requests += 1
            deps = await self.provider.get_departures(sid, cfg.poll.departures_per_station)
        except ProviderError as e:
            if entry.failures == 0:
                entry.first_failure = datetime.now(UTC)
            entry.failures += 1
            entry.last_error = str(e)
            entry.next_due = self._clock() + min(interval * 2 ** (entry.failures - 1), MAX_BACKOFF)
            log.warning("Abruf %s fehlgeschlagen (%d×): %s", sid, entry.failures, e)
        else:
            entry.departures = deps
            entry.last_ok = datetime.now(UTC)
            entry.last_ok_mono = self._clock()
            entry.failures = 0
            entry.first_failure = None
            entry.last_error = None
            entry.next_due = self._clock() + interval
        finally:
            self._inflight.pop(sid, None)

    # ------------------------------------------------------------------ #

    def request_refresh(self, ids: set[str]) -> None:
        """Taster Y: sofort neu laden — aber nie öfter als der Provider erlaubt."""
        now = self._clock()
        for sid in ids:
            e = self.cache.setdefault(sid, CacheEntry())
            earliest = (e.last_attempt_mono or -1e9) + self.provider.min_interval_seconds
            e.next_due = max(earliest, now) if e.failures == 0 else e.next_due
        self.tick()

    async def ensure_loaded(self, ids: set[str], timeout: float = 8.0) -> None:
        """Beim allerersten Abruf einer Station kurz auf Daten warten statt leer zu antworten."""
        missing = [s for s in ids if (e := self.cache.get(s)) is None or e.last_attempt_mono is None]
        if not missing:
            return
        self.tick()
        tasks = [self._inflight[s] for s in missing if s in self._inflight]
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)

    def departures(self, sid: str) -> list[Departure]:
        e = self.cache.get(sid)
        return e.departures if e else []

    def stale_since(self, ids: set[str]) -> datetime | None:
        """Ältester ``last_ok`` aller Stationen, deren letzter Abruf fehlschlug oder überfällig ist."""
        now = self._clock()
        stale: list[datetime] = []
        for sid in ids:
            e = self.cache.get(sid)
            if e is None or e.last_attempt_mono is None:
                continue  # noch nie abgefragt → „lädt", nicht „veraltet"
            overdue = e.last_ok_mono is None or now - e.last_ok_mono > 3 * self.interval(len(ids)) + 30
            if e.failures or overdue:
                stale.append(e.last_ok or e.first_failure or datetime.now(UTC))
        return min(stale) if stale else None
