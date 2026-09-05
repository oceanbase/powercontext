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

"""Resolve a stable signing secret for stateless REST cursors."""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from sqlalchemy.engine import make_url

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import DatabaseConfig
from powercontext.paths import powercontext_data_dir

_CURSOR_SECRET_BYTES = 32


def resolve_cursor_secret(database: DatabaseConfig, configured_secret: str | None, /) -> bytes | None:
    """Use an explicit secret, or persist one in the local data location."""

    if configured_secret is not None:
        return configured_secret.encode()
    if isinstance(database, SQLiteConfig):
        if database.is_in_memory:
            return None
        database_name = make_url(database.url).database
        if database_name:
            database_path = Path(database_name).expanduser().resolve(strict=False)
            return _load_or_create(database_path.with_name(f".{database_path.name}.cursor-key"))
    return _load_or_create(powercontext_data_dir() / "cursor-signing.key")


def _load_or_create(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        return _read(path)
    except FileNotFoundError:
        pass

    generated = secrets.token_bytes(_CURSOR_SECRET_BYTES)
    try:
        # Windows text descriptors translate LF bytes even when using os.write.
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        return _read(path)
    try:
        written = os.write(descriptor, generated)
        if written != len(generated):
            raise OSError(f"failed to write complete cursor signing key: {path}")  # noqa: TRY003
    finally:
        os.close(descriptor)
    return generated


def _read(path: Path) -> bytes:
    secret = path.read_bytes()
    if len(secret) != _CURSOR_SECRET_BYTES:
        raise ValueError(f"cursor signing key must contain {_CURSOR_SECRET_BYTES} bytes: {path}")  # noqa: TRY003
    return secret
