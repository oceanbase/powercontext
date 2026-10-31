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

"""In-memory target snapshots and submission tracking; no execution journal."""

from __future__ import annotations

from builtins import list as BuiltinList
from datetime import UTC, datetime
from typing import Any

from powercontext.source_management.models import ManagementError, SourceTarget


class ManagedTargets:
    def __init__(self, *, instance_id: str, report) -> None:
        self.instance_id = instance_id
        self.report = report
        self._targets: dict[str, SourceTarget] = {}
        self._synchronized = False
        self._current: BatchObserver | None = None
        self._stopping: set[str] = set()

    def synchronize(self, targets: BuiltinList[dict[str, Any]], stopping: BuiltinList[dict[str, Any]]) -> None:
        self._targets = {t.target_id: t for v in targets if (t := SourceTarget.model_validate(v))}
        self._stopping = {v["target_id"] for v in stopping}
        self._synchronized = True

    async def list(self) -> tuple[SourceTarget, ...]:
        if not self._synchronized:
            raise ManagementError("targets-unavailable", "Targets have not yet been synchronized", 503)
        return tuple(t for t in self._targets.values() if t.target_id not in self._stopping)

    def begin(self, binding, selected) -> BatchObserver:
        if self._current is not None:
            raise ManagementError("batch-running", "A batch is already running", 409)
        if not self._synchronized:
            raise ManagementError("targets-unavailable", "Targets have not yet been synchronized", 503)
        seen = set()
        for item in selected:
            known = self._targets.get(item.target_id)
            if (
                item.target_id in seen
                or known is None
                or known != item
                or item.instance_id != self.instance_id
                or item.scope_id != binding.scope_id
                or item.target_id in self._stopping
            ):
                raise ManagementError("stale-target", "The batch contains a stopped, stale or foreign item", 409)
            seen.add(item.target_id)
        self._current = BatchObserver(self, binding, selected)
        return self._current

    def end(self) -> None:
        self._current = None

    def active_target_ids(self) -> set[str]:
        return set() if self._current is None else set(self._current.selected)

    def batch(self, target, session):
        current = self._current
        if current is None or current.session is not session or current.selected.get(target.target_id) != target:
            raise ManagementError("invalid-batch", "The item does not belong to this batch")
        return current

    async def submit(self, target, *, session, item_id, definition_name, value):
        batch = self.batch(target, session)
        if item_id in batch.items:
            raise ManagementError("duplicate-item", "Each batch item needs a distinct item_id")
        batch.items[item_id] = target.target_id
        return await session.submit(item_id, definition_name, value)


class BatchObserver:
    def __init__(self, targets, binding, selected):
        self.targets, self.binding, self.session = targets, binding, None
        self.selected = {t.target_id: t for t in selected}
        self.items: dict[str, str] = {}
        self.refs: dict[str, list[Any]] = {}

    async def started(self, session):
        self.session = session

    async def materialized(self, binding, item_id, observation):
        if binding != self.binding or (self.selected and item_id not in self.items):
            raise ManagementError("unassociated-source", "Use targets.submit for managed collect batches")

    async def accepted(self, item_id, source_ref):
        if item_id not in self.items:
            return
        identifier = self.items[item_id]
        self.refs.setdefault(identifier, []).append(source_ref)
        if isinstance(self.selected[identifier], SourceTarget):
            # A best-effort report cannot revoke the Source that the Server has already accepted.
            await self.targets.report({
                "target_id": identifier,
                "source_refs": [source_ref.model_dump(mode="json")],
                "submitted_at": datetime.now(UTC).isoformat(),
            })

    async def prepared(self, session, completion):
        pass

    async def completed(self, result):
        pass

    async def interrupted(self, error):
        pass
