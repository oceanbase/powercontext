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

"""Read-only ports consumed by the optional Handoff Report feature."""

from __future__ import annotations

from typing import Protocol

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.handoff import Handoff, HandoffEvidenceCheck
from powercontext.builtin.work import WorkContinuity


class HandoffReadAdapter(Protocol):
    """Read committed Handoffs without extending their persistence protocol."""

    async def latest(self, scope_id: str, /) -> Handoff | None:
        """Return one scope's current committed Handoff, if it exists."""

        ...

    async def get(self, scope_id: str, reference: ArtifactRef, /) -> Handoff:
        """Return the exact committed Handoff addressed by ``reference``."""

        ...

    async def revisions(self, scope_id: str, /) -> tuple[Handoff, ...]:
        """Return one scope's committed Handoffs in ascending Revision order."""

        ...

    async def check_evidence(
        self,
        scope_id: str,
        reference: ArtifactRef,
        /,
    ) -> tuple[HandoffEvidenceCheck, ...]:
        """Recheck evidence readability for one exact committed Handoff."""

        ...


class WorkContinuityReadAdapter(Protocol):
    """Read the high-level Work loop projection for one scope."""

    async def get(self, scope_id: str, reference: ArtifactRef | None, /) -> WorkContinuity: ...


__all__ = ["HandoffReadAdapter", "WorkContinuityReadAdapter"]
