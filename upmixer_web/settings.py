"""Runtime settings for the web application."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    """Environment-backed web application settings."""

    data_dir: Path
    database_url: str
    worker_count: int = 1
    root_path: str = ""
    allowed_origins: tuple[str, ...] = ()
    frontend_dir: Path | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        data_dir = Path(os.getenv("UPMIXER_DATA_DIR", "./data")).resolve()
        database_url = os.getenv(
            "UPMIXER_DATABASE_URL",
            f"sqlite:///{data_dir / 'upmixer.db'}",
        )
        origins = tuple(
            item.strip()
            for item in os.getenv("UPMIXER_ALLOWED_ORIGINS", "").split(",")
            if item.strip()
        )
        frontend = os.getenv("UPMIXER_FRONTEND_DIR")
        root_path = os.getenv("UPMIXER_ROOT_PATH", "").rstrip("/")
        if root_path and not re.fullmatch(r"/[A-Za-z0-9._~!$&'()*+,;=:@%/-]+", root_path):
            raise ValueError("UPMIXER_ROOT_PATH must be a URL path beginning with '/'")
        return cls(
            data_dir=data_dir,
            database_url=database_url,
            worker_count=max(1, int(os.getenv("UPMIXER_WORKERS", "1"))),
            root_path=root_path,
            allowed_origins=origins,
            frontend_dir=Path(frontend).resolve() if frontend else None,
        )

    def prepare(self) -> None:
        """Create local runtime directories."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for name in ("objects", "work", "stem-cache"):
            (self.data_dir / name).mkdir(exist_ok=True)
