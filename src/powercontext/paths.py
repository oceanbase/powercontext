# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

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
