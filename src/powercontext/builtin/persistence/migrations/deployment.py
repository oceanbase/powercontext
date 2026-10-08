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

"""Packaged acceptance bundle, isolated from side-effectful runtime factories.

This release does not register full Server baselines or remote executors. A
complete deployment must not be optimistically stamped from a four-table test.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy.engine import make_url

from powercontext.builtin.persistence.sqlite import SQLiteConfig

from .bundle import MigrationBundle
from .models import MigrationError
from .sqlite import SQLiteMigrationRunner

if TYPE_CHECKING:
    from powercontext.server.settings import ServerSettings


def production_bundle() -> MigrationBundle:
    """Load installed resources; readiness is scoped to this acceptance bundle."""
    return MigrationBundle(Path(__file__).parent / "resources")


def deployment_runner(settings: ServerSettings) -> SQLiteMigrationRunner:
    """Use the real configured identity without opening a business profile."""
    database = settings.database
    if not isinstance(database, SQLiteConfig):
        raise MigrationError(
            "unsupported_backend", "Remote and seekdb deployment migration awaits full-backend acceptance."
        )
    url = make_url(database.url)
    if database.is_in_memory or url.query or not url.database or url.username or url.password or url.host or url.port:
        raise MigrationError(
            "unsupported_target", "Use a regular SQLite file URL without credentials, host, port or URI options."
        )
    # Match SQLite's URL normalization before the runner expands standalone Path inputs.
    target = Path(os.path.abspath(url.database))
    return SQLiteMigrationRunner(
        target, production_bundle(), configuration=database.model_dump(exclude={"url", "echo"})
    )
