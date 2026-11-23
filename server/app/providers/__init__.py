from __future__ import annotations

from ..config import AppConfig, Secrets, resolve_provider
from .base import DepartureProvider, ProviderError
from .rmv import RmvProvider
from .transitous import TransitousProvider

__all__ = ["DepartureProvider", "ProviderError", "RmvProvider", "TransitousProvider", "make_provider"]


def make_provider(cfg: AppConfig, secrets: Secrets) -> DepartureProvider:
    name = resolve_provider(cfg, secrets)
    if name == "rmv":
        if not secrets.rmv_api_key:
            raise ValueError("provider: rmv gesetzt, aber RMV_API_KEY fehlt")
        return RmvProvider(secrets.rmv_api_key, timeout=cfg.poll.timeout_seconds)
    return TransitousProvider(secrets.transitous_contact, timeout=cfg.poll.timeout_seconds)
