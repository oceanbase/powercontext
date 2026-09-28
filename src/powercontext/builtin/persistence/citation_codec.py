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

"""Canonical storage of exact Memory entry citations."""

from pydantic import RootModel

from powercontext.artifacts import MemoryCitation
from powercontext.builtin.persistence.codec import dump_model, load_model, stored_bytes


class _MemoryCitations(RootModel[tuple[MemoryCitation, ...]]):
    pass


def dump_memory_citations(values: tuple[MemoryCitation, ...]) -> bytes:
    return dump_model(_MemoryCitations(values), kind="lineage", name="memory-citations")


def load_memory_citations(value: object) -> tuple[MemoryCitation, ...]:
    if value is None:
        return ()
    return load_model(
        _MemoryCitations,
        stored_bytes(value, column="memory_citations"),
        kind="lineage",
        name="memory-citations",
    ).root
