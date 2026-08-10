"""User-owned paths for installed PowerContext processes."""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_path

POWERCONTEXT_HOME_ENV = "POWERCONTEXT_HOME"


def powercontext_data_dir() -> Path:
    """Return the user data directory without creating it."""

    configured = os.environ.get(POWERCONTEXT_HOME_ENV)
    if configured:
        return Path(configured).expanduser().resolve()
    return user_data_path("powercontext", appauthor=False)


def default_database_path() -> Path:
    """Return the installed Server's default SQLite database path."""

    return powercontext_data_dir() / "powercontext.db"


def default_scheduler_path() -> Path:
    """Return the installed Server's default scheduler database path."""

    return powercontext_data_dir() / "scheduler.db"


def sqlite_url(path: Path) -> str:
    """Render an absolute path as an async SQLAlchemy SQLite URL."""

    return f"sqlite+aiosqlite:///{path.expanduser().resolve().as_posix()}"


__all__ = [
    "POWERCONTEXT_HOME_ENV",
    "default_database_path",
    "default_scheduler_path",
    "powercontext_data_dir",
    "sqlite_url",
]
