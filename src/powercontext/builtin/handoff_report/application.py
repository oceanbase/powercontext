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

"""Read-only Handoff reports over a resolved Scope selection."""

from __future__ import annotations

from datetime import UTC, datetime

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.handoff_report.canonical import finalize_digests
from powercontext.builtin.handoff_report.errors import HandoffReportInconsistentError
from powercontext.builtin.handoff_report.protocols import HandoffReadAdapter, ScopeSelectionResolver
from powercontext.builtin.handoff_report.report import HandoffReport, HandoffReportSummary, ScopeHandoffReport
from powercontext.builtin.scope.models import ScopeSelection


class HandoffReportApplication:
    """Build exact Handoff state without introducing another organization model."""

    def __init__(self, scopes: ScopeSelectionResolver, handoffs: HandoffReadAdapter, /) -> None:
        self._scopes = scopes
        self._handoffs = handoffs

    async def get_report(
        self,
        selection: ScopeSelection,
        /,
        *,
        generated_at: datetime | None = None,
    ) -> HandoffReport:
        """Resolve one common selection and freeze each Scope at an exact Handoff revision."""

        scopes = await self._scopes.resolve_selection(selection)
        projected: list[ScopeHandoffReport] = []
        for scope in scopes:
            latest = await self._handoffs.latest(scope.scope_id)
            if latest is None:
                projected.append(ScopeHandoffReport(scope=scope, status="no_handoff"))
                continue

            reference = latest.as_ref()
            frozen = await self._handoffs.get(scope.scope_id, reference)
            if frozen.as_ref() != reference:
                raise HandoffReportInconsistentError(scope.scope_id)
            projected.append(
                ScopeHandoffReport(
                    scope=scope,
                    status=frozen.content.disposition,
                    handoff=ArtifactAddress(scope_id=scope.scope_id, artifact=reference),
                    content=frozen.content,
                )
            )

        entries = tuple(projected)
        report = HandoffReport(
            selection=selection,
            scope_ids=tuple(scope.scope_id for scope in scopes),
            generated_at=datetime.now(UTC) if generated_at is None else generated_at,
            summary=HandoffReportSummary(
                continuable_count=sum(entry.status == "continuable" for entry in entries),
                blocked_count=sum(entry.status == "blocked" for entry in entries),
                complete_count=sum(entry.status == "complete" for entry in entries),
                no_handoff_count=sum(entry.status == "no_handoff" for entry in entries),
            ),
            scopes=entries,
        )
        return finalize_digests(report)


__all__ = ["HandoffReportApplication"]
