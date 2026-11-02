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

"""Recognize complete installed-release schema shapes, independently of readiness."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import Connection

from .bundle import sqlite_inventory
from .models import MigrationError, digest

_BASELINES = Path(__file__).parents[1] / "migration_resources/server_baselines"


@dataclass(frozen=True)
class ServerBaseline:
    """A recognized schema source, not a validated data state or executable path."""

    release: str
    schema_fingerprint: str


def recognize_sqlite_server_baseline(connection: Connection) -> ServerBaseline | None:
    """Match the complete schema, including FTS shadows and optional-feature objects.

    No objects are excluded: in particular, an existing control table cannot be
    ignored to stamp a partially migrated database as a released source.
    """
    return _recognize(digest(sqlite_inventory(connection)), backend="sqlite")


def recognize_mysql_server_baseline(
    inventory: dict[str, Any], *, backend: Literal["seekdb", "oceanbase"]
) -> ServerBaseline | None:
    """Match a complete native inventory against captures of the same backend.

    FULLTEXT parser configuration participates in the fingerprint. A capture
    from embedded seekDB does not establish an OceanBase source baseline.
    """
    return _recognize(digest(inventory), backend=backend)


def _recognize(fingerprint: str, *, backend: Literal["sqlite", "seekdb", "oceanbase"]) -> ServerBaseline | None:
    matches: list[ServerBaseline] = []
    for path in sorted(_BASELINES.glob(f"*/{backend}.json")):
        source = json.loads(path.read_text(encoding="utf-8"))
        if (
            source["format"] != "powercontext.server-baseline.v1"
            or source["backend"] != backend
            or source["release"] != path.parent.name
            or digest(source["inventory"]) != source["schema_fingerprint"]
        ):
            raise MigrationError("invalid_bundle", "The frozen Server baseline resource is inconsistent.")
        if fingerprint == source["schema_fingerprint"]:
            matches.append(ServerBaseline(release=source["release"], schema_fingerprint=fingerprint))
    if len(matches) > 1:
        raise MigrationError("invalid_bundle", "Multiple releases claim the same complete Server baseline.")
    return matches[0] if matches else None
