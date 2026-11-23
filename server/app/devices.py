"""Gerätezustand: manuelle Profil- und Nachtwahl per Taster, persistiert als JSON.

Manuelle Wahl gilt bis zum nächsten Wechsel des Zeitplans: Wer morgens per Taster auf
„abends" schaltet, ist ab dem nächsten Zeitplan-Wechsel wieder automatisch unterwegs.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel

from . import board
from .config import AppConfig

log = logging.getLogger(__name__)

ACTIVE_FOR = timedelta(minutes=15)  # so lange gilt ein Gerät nach dem letzten Abruf als aktiv


class DeviceState(BaseModel):
    profile: str | None = None
    profile_until: datetime | None = None
    night: bool | None = None  # None = automatisch
    night_until: datetime | None = None
    last_seen: datetime | None = None


class DeviceStore:
    def __init__(self, path: Path):
        self.path = path
        self.devices: dict[str, DeviceState] = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                self.devices = {k: DeviceState.model_validate(v) for k, v in raw.items()}
            except (OSError, ValueError):
                log.warning("Gerätezustand %s unlesbar, starte leer", path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps({k: v.model_dump(mode="json") for k, v in self.devices.items()}, indent=1),
            encoding="utf-8",
        )
        tmp.replace(self.path)

    def get(self, device: str) -> DeviceState:
        return self.devices.setdefault(device, DeviceState())

    def touch(self, device: str, now: datetime) -> None:
        self.get(device).last_seen = now.astimezone(UTC)

    def active_devices(self, now: datetime) -> list[str]:
        return [k for k, v in self.devices.items() if v.last_seen and now - v.last_seen < ACTIVE_FOR]

    # ------------------------------------------------------------------ #

    def profile(self, cfg: AppConfig, device: str, now: datetime) -> str | None:
        st = self.get(device)
        if st.profile in cfg.profiles and st.profile_until and now < st.profile_until:
            return st.profile
        return board.scheduled_profile(cfg, now)

    def set_profile(self, cfg: AppConfig, device: str, profile: str, now: datetime) -> None:
        if profile not in cfg.profiles:
            raise KeyError(profile)
        st = self.get(device)
        st.profile = profile
        st.profile_until = board.next_change(lambda t: board.scheduled_profile(cfg, t), now)
        self.save()

    def step_profile(self, cfg: AppConfig, device: str, step: int, now: datetime) -> str | None:
        names = list(cfg.profiles)
        if not names:
            return None
        cur = self.profile(cfg, device, now)
        new = names[(names.index(cur) + step) % len(names)] if cur in names else names[0]
        self.set_profile(cfg, device, new, now)
        return new

    def night(self, cfg: AppConfig, device: str, now: datetime) -> bool:
        st = self.get(device)
        if st.night is not None and st.night_until and now < st.night_until:
            return st.night
        return board.is_night(cfg, now)

    def toggle_night(self, cfg: AppConfig, device: str, now: datetime) -> bool:
        st = self.get(device)
        st.night = not self.night(cfg, device, now)
        st.night_until = board.next_change(lambda t: board.is_night(cfg, t), now)
        self.save()
        return st.night

    def reset(self, device: str) -> None:
        self.devices[device] = DeviceState(last_seen=self.get(device).last_seen)
        self.save()
