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

"""Resolve frozen legacy logical identities before using Atomic Memory authority."""

from powercontext.builtin.artifacts.memory.models import Memory
from powercontext.builtin.persistence.atomic_memory_identity import legacy_entry_artifact_id
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.records import BaseValueNotFoundError


async def resolve_legacy_memory_target(connection, artifacts, scope_id: str, memory_id: str, entry_id: str) -> str:
    try:
        memory = await artifacts.latest(connection, scope_id, "memory", memory_id)
    except RepositoryNotFoundError:
        raise BaseValueNotFoundError("artifact", (scope_id, memory_id, entry_id)) from None
    if not isinstance(memory, Memory) or not any(item.entry_id == entry_id for item in memory.content.manifest.entries):
        raise BaseValueNotFoundError("artifact", (scope_id, memory_id, entry_id))
    return legacy_entry_artifact_id(scope_id, memory_id, entry_id)
