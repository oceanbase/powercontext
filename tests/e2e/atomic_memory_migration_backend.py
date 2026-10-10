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

"""Backend-owned maintenance connections for clean Atomic Memory migration acceptance."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from pathlib import Path

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndex
from powercontext.builtin.persistence.oceanbase import OceanBaseProfile
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.sqlite.atomic_memory_index import SQLiteAtomicMemoryIndex
from powercontext.builtin.runtime.config import DatabaseConfig


@dataclass(frozen=True)
class MigrationBackend:
    config: DatabaseConfig
    directory: Path


def migration_profile(
    config: DatabaseConfig,
    *,
    load_vector_extension: bool = False,
) -> AbstractAsyncContextManager[SQLiteProfile | SeekDBProfile | OceanBaseProfile]:
    if isinstance(config, SQLiteConfig):
        return SQLiteProfile.open(config, tables=(), load_vector_extension=load_vector_extension)
    if isinstance(config, SeekDBConfig):
        return SeekDBProfile.open(config, tables=())
    return OceanBaseProfile.open(config, tables=())


def migration_index(config: DatabaseConfig, profile: EmbeddingProfile | None = None) -> AtomicMemoryIndex:
    return SQLiteAtomicMemoryIndex(profile) if isinstance(config, SQLiteConfig) else OceanBaseAtomicMemoryIndex(profile)
