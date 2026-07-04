"""Project settings.

Settings are intentionally lightweight and use pydantic without requiring the
optional pydantic-settings package. Environment variables can override the most
important paths and request options.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def configured_project_root() -> Path:
    """Return the runtime root for data, reports, logs, and local defaults."""

    return Path(os.getenv("TAIWAN_TRADING_ROOT", PROJECT_ROOT)).expanduser().resolve()


class ProjectSettings(BaseModel):
    """Runtime configuration for local research jobs."""

    project_root: Path = PROJECT_ROOT
    data_dir: Path = PROJECT_ROOT / "data"
    raw_data_dir: Path = PROJECT_ROOT / "data" / "raw"
    processed_data_dir: Path = PROJECT_ROOT / "data" / "processed"
    db_path: Path = PROJECT_ROOT / "data" / "taiwan_trading.duckdb"
    request_timeout_seconds: float = Field(default=20.0, gt=0)
    user_agent: str = "taiwan-short-term-trading-research/0.1"


@lru_cache(maxsize=1)
def get_settings() -> ProjectSettings:
    """Return settings with simple environment-variable overrides."""

    root = configured_project_root()
    data_dir = Path(os.getenv("TAIWAN_TRADING_DATA_DIR", root / "data")).expanduser()
    return ProjectSettings(
        project_root=root,
        data_dir=data_dir,
        raw_data_dir=Path(
            os.getenv("TAIWAN_TRADING_RAW_DATA_DIR", data_dir / "raw")
        ).expanduser(),
        processed_data_dir=Path(
            os.getenv(
                "TAIWAN_TRADING_PROCESSED_DATA_DIR",
                data_dir / "processed",
            )
        ).expanduser(),
        db_path=Path(
            os.getenv(
                "TAIWAN_TRADING_DB_PATH",
                data_dir / "taiwan_trading.duckdb",
            )
        ).expanduser(),
        request_timeout_seconds=float(os.getenv("TAIWAN_TRADING_TIMEOUT", "20")),
        user_agent=os.getenv(
            "TAIWAN_TRADING_USER_AGENT",
            "taiwan-short-term-trading-research/0.1",
        ),
    )
