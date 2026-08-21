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

"""Memory test suite."""

from powercontext.builtin.artifacts.memory import Memory, MemoryEntryVersion, MemoryService


async def current_entry(
    service: MemoryService,
    memory: Memory,
    entry_id: str | None = None,
) -> MemoryEntryVersion:
    """Select a current entry object for an object-oriented public API call."""

    entries = await service.entries(memory)
    if entry_id is None:
        return entries[0]
    return next(entry for entry in entries if entry.entry_id == entry_id)
