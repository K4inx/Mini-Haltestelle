"""Client-Konfiguration: /etc/abfahrtsanzeige/client.yaml (oder --config)."""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("/etc/abfahrtsanzeige/client.yaml")


@dataclass
class ClientConfig:
    server_url: str = "http://localhost:8080"
    device_id: str = "pi"
    poll_seconds: float = 30.0
    theme: str = "dark"  # dark | light
    rotation: int = 0  # 0 oder 180 (Gehäuse kopfüber)
    long_press_seconds: float = 3.0
    wake_seconds: float = 30.0  # nachts: Display nach Tastendruck so lange hell
    offline_error_minutes: float = 10.0  # danach Fehler-Screen statt alter Daten
    led_brightness: float = 0.3  # LED ist hell — gedimmt reicht am Schreibtisch
    request_timeout: float = 6.0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path | None) -> ClientConfig:
        if path is None or not path.exists():
            return cls()
        import yaml  # nur hier nötig; auf dem Pi python3-yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        kwargs["extra"] = {k: v for k, v in data.items() if k not in known}
        cfg = cls(**kwargs)
        cfg.server_url = cfg.server_url.rstrip("/")
        return cfg
