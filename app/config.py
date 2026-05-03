from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJECT_ROOT / "config.yaml"


class Settings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8138
    data_root: str = "data/profiles"
    screenshots_root: str = "data/screenshots"
    state_file: str = "data/state.json"
    logs_root: str = "logs"
    max_concurrent_launches: int = 4
    camoufox_macos_app: str = "~/Library/Caches/camoufox/Camoufox.app"
    default_start_url: str = "https://ipwho.is/"
    command_timeout_seconds: int = 45
    log_level: str = "INFO"

    def _path(self, value: str) -> Path:
        path = Path(value).expanduser()
        return path if path.is_absolute() else PROJECT_ROOT / path

    @property
    def data_root_abs(self) -> Path:
        return self._path(self.data_root)

    @property
    def screenshots_root_abs(self) -> Path:
        return self._path(self.screenshots_root)

    @property
    def state_file_abs(self) -> Path:
        return self._path(self.state_file)

    @property
    def logs_root_abs(self) -> Path:
        return self._path(self.logs_root)

    @property
    def camoufox_macos_app_abs(self) -> Path:
        return self._path(self.camoufox_macos_app)


def load_settings() -> Settings:
    if not CONFIG_FILE.exists():
        return Settings()
    raw: dict[str, Any] = yaml.safe_load(CONFIG_FILE.read_text(encoding="utf-8")) or {}
    return Settings(**raw)


settings = load_settings()


def ensure_directories() -> None:
    settings.data_root_abs.mkdir(parents=True, exist_ok=True)
    settings.screenshots_root_abs.mkdir(parents=True, exist_ok=True)
    settings.logs_root_abs.mkdir(parents=True, exist_ok=True)
    settings.state_file_abs.parent.mkdir(parents=True, exist_ok=True)
