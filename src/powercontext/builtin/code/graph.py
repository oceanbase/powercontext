# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Storage operations used by bounded code analysis, independent of SQL dialect."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Protocol


class GraphReader(Protocol):
    def node(self, node_id: str) -> dict[str, Any] | None: ...

    def search(self, query: str, prefix: str, limit: int) -> list[dict[str, Any]]: ...

    def nodes(
        self, *, prefix: str = "", path: str | None = None, kind: str | None = None, parent_id: str | None = None
    ) -> Iterator[dict[str, Any]]: ...

    def neighbors(
        self, node_id: str, prefix: str, *, reverse: bool, impact: bool, count_boundary: bool
    ) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], int]: ...


class GraphStore(Protocol):
    """Persist immutable graphs while the local cache owns publication and reader locks."""

    identity: str

    def create(
        self,
        directory: Path,
        nodes: list[dict[str, Any]],
        edges: list[dict[str, Any]],
        deadline: float,
        *,
        generation_id: str | None = None,
    ) -> dict[str, Any]:
        """Write one generation; the caller may name it so a recall can address it later."""
        ...

    def verify(self, directory: Path, manifest: dict[str, Any], deadline: float) -> None: ...

    def open(
        self, directory: Path, manifest: dict[str, Any], deadline: float
    ) -> AbstractContextManager[GraphReader]: ...

    def remove(self, directory: Path, deadline: float) -> None: ...

    def remove_generation(self, generation_id: str, binding: str, deadline: float) -> None:
        """Drop a commit by identifier, for directories that no longer exist.

        ``remove()`` takes a directory and reads the identifier out of it. When the
        directory is already gone, this is the only entry point that can still
        reach the rows.
        """
        ...

    def size(self, directory: Path, deadline: float) -> int: ...
