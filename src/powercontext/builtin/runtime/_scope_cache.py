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

"""Bounded lifecycle for scope-local Runtime resources."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field

DEFAULT_SCOPE_CACHE_SIZE = 128

ScopeEvictor = Callable[[str], None]
ScopeCacheObserver = Callable[[int, int], None]


@dataclass(frozen=True, slots=True)
class ScopeCacheCounts:
    """Low-cardinality snapshot of inactive cached and currently active scopes."""

    cached: int
    active: int


@dataclass(slots=True)
class _ScopeEntry:
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    leases: int = 0


class ScopeCache:
    """Retain a bounded LRU of inactive scopes without evicting in-flight work."""

    def __init__(
        self,
        capacity: int,
        *,
        evictor: ScopeEvictor | None = None,
        observer: ScopeCacheObserver | None = None,
    ) -> None:
        if capacity < 1:
            raise ValueError("scope cache capacity must be positive")  # noqa: TRY003
        self.capacity = capacity
        self._evictor = evictor
        self._observer = observer
        self._entries: OrderedDict[str, _ScopeEntry] = OrderedDict()
        self._observe()

    @contextmanager
    def lease(self, scope_id: str, /) -> Iterator[None]:
        """Keep one scope and its serialization lock alive for an operation."""

        entry = self._entries.get(scope_id)
        if entry is None:
            self._make_room()
            entry = _ScopeEntry()
            self._entries[scope_id] = entry
        entry.leases += 1
        self._entries.move_to_end(scope_id)
        self._observe()
        try:
            yield
        finally:
            current = self._entries.get(scope_id)
            if current is not entry or entry.leases < 1:
                raise RuntimeError("scope cache lease invariant violated")  # noqa: TRY003
            entry.leases -= 1
            self._entries.move_to_end(scope_id)
            self._trim()
            self._observe()

    def lock(self, scope_id: str, /) -> asyncio.Lock:
        """Return the serialization lock protected by the caller's current lease."""

        entry = self._entries.get(scope_id)
        if entry is None or entry.leases < 1:
            raise RuntimeError("scope cache entry is not leased")  # noqa: TRY003
        return entry.lock

    @property
    def counts(self) -> ScopeCacheCounts:
        """Return inactive cached and active scope counts without scope identifiers."""

        inactive = sum(entry.leases == 0 for entry in self._entries.values())
        active = sum(entry.leases > 0 for entry in self._entries.values())
        return ScopeCacheCounts(cached=inactive, active=active)

    def clear(self) -> None:
        """Evict all entries after the Runtime has drained its operations."""

        if any(entry.leases > 0 for entry in self._entries.values()):
            raise RuntimeError("cannot clear active scope cache entries")  # noqa: TRY003
        for scope_id in tuple(self._entries):
            self._evict(scope_id)
        self._observe()

    def _make_room(self) -> None:
        while len(self._entries) >= self.capacity:
            scope_id = self._oldest_inactive_scope()
            if scope_id is None:
                return
            self._evict(scope_id)

    def _trim(self) -> None:
        while len(self._entries) > self.capacity:
            scope_id = self._oldest_inactive_scope()
            if scope_id is None:
                return
            self._evict(scope_id)

    def _oldest_inactive_scope(self) -> str | None:
        return next((scope_id for scope_id, entry in self._entries.items() if entry.leases == 0), None)

    def _evict(self, scope_id: str) -> None:
        entry = self._entries.pop(scope_id)
        if entry.leases > 0:
            raise RuntimeError("cannot evict an active scope cache entry")  # noqa: TRY003
        if self._evictor is not None:
            self._evictor(scope_id)

    def _observe(self) -> None:
        if self._observer is None:
            return
        counts = self.counts
        with suppress(Exception):
            self._observer(counts.cached, counts.active)


__all__ = [
    "DEFAULT_SCOPE_CACHE_SIZE",
    "ScopeCache",
    "ScopeCacheCounts",
    "ScopeCacheObserver",
    "ScopeEvictor",
]
