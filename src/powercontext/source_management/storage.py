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

"""Small protected transactional store for the sole management authority."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from powercontext.source_management.models import ManagementError


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


class Store:
    def __init__(self, directory: Path) -> None:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink():
            raise ManagementError("invalid-storage", "State directory must not be a symlink")
        os.chmod(directory, 0o700)
        path = directory / "management.sqlite3"
        if path.is_symlink():
            raise ManagementError("invalid-storage", "State database must not be a symlink")
        self.connection = sqlite3.connect(path, isolation_level=None)
        os.chmod(path, 0o600)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute("CREATE TABLE IF NOT EXISTS records(kind TEXT,id TEXT,value TEXT,PRIMARY KEY(kind,id))")
        self.connection.execute("CREATE TABLE IF NOT EXISTS follow_identity(identity TEXT PRIMARY KEY,record_id TEXT)")

    def claim_identity(self, identity: str, identifier: str) -> None:
        existing = self.connection.execute(
            "SELECT record_id FROM follow_identity WHERE identity=?", (identity,)
        ).fetchone()
        if existing is not None and existing[0] != identifier:
            raise ManagementError("target-identity-conflict", "This follow identity is already reserved", 409)
        self.connection.execute("INSERT OR IGNORE INTO follow_identity VALUES(?,?)", (identity, identifier))

    def retire_identity(self, identity: str, identifier: str) -> None:
        self.connection.execute("DELETE FROM follow_identity WHERE identity=? AND record_id=?", (identity, identifier))

    def close(self) -> None:
        self.connection.close()

    def get(self, kind: str, identifier: str) -> dict[str, Any] | None:
        row = self.connection.execute("SELECT value FROM records WHERE kind=? AND id=?", (kind, identifier)).fetchone()
        return None if row is None else json.loads(row[0])

    def put(self, kind: str, identifier: str, value: dict[str, Any]) -> None:
        self.connection.execute(
            "INSERT INTO records VALUES(?,?,?) ON CONFLICT(kind,id) DO UPDATE SET value=excluded.value",
            (kind, identifier, canonical(value)),
        )

    def all(self, kind: str) -> list[dict[str, Any]]:
        return [
            json.loads(row[0])
            for row in self.connection.execute("SELECT value FROM records WHERE kind=? ORDER BY id", (kind,))
        ]

    @contextmanager
    def transaction(self) -> Iterator[None]:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise
