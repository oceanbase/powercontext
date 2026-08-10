from __future__ import annotations

import asyncio
from collections import deque

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.handoff import (
    Handoff,
    HandoffContent,
    HandoffEvidenceCheck,
    HandoffSourceCitation,
    HandoffStatement,
)
from powercontext.builtin.handoff_report.adapters import RuntimeHandoffReadAdapter
from powercontext.builtin.handoff_report.errors import (
    HandoffReportBusyError,
    HandoffReportEvidenceCheckUnavailableError,
)
from powercontext.builtin.handoff_report.models import WorkstreamDescriptor
from powercontext.builtin.handoff_report.selection import select_optimistic_stable_handoffs
from powercontext.sources import SourceRef


def _workstream(scope_id: str, *, version: int = 1) -> WorkstreamDescriptor:
    return WorkstreamDescriptor(
        scope_id=scope_id,
        project_id="prj-1",
        title=f"Workstream {scope_id}",
        kind="feature",
        version=version,
    )


def _handoff(revision: int, *, artifact_id: str = "handoff") -> Handoff:
    citation = HandoffSourceCitation(
        source_ref=SourceRef(source_type="content", source_id="source-1"),
    )
    return Handoff(
        artifact_id=artifact_id,
        revision=revision,
        content=HandoffContent(
            objective="Implement Handoff Report.",
            state=(
                HandoffStatement(
                    text=f"Handoff revision {revision} is committed.",
                    citations=(citation,),
                ),
            ),
            disposition="continuable",
        ),
    )


class _FakeReadAdapter:
    def __init__(self, values: dict[str, tuple[Handoff | None, ...]]) -> None:
        self._values = {scope_id: deque(items) for scope_id, items in values.items()}
        self.calls: list[str] = []

    async def latest(self, scope_id: str, /) -> Handoff | None:
        self.calls.append(scope_id)
        values = self._values[scope_id]
        return values.popleft() if len(values) > 1 else values[0]

    async def get(self, scope_id: str, reference: ArtifactRef, /) -> Handoff:
        del scope_id, reference
        raise NotImplementedError

    async def revisions(self, scope_id: str, /) -> tuple[Handoff, ...]:
        del scope_id
        raise NotImplementedError

    async def check_evidence(
        self,
        scope_id: str,
        reference: ArtifactRef,
        /,
    ) -> tuple[HandoffEvidenceCheck, ...]:
        del scope_id, reference
        raise NotImplementedError


def test_optimistic_selection_freezes_stable_heads_in_scope_order() -> None:
    async def scenario() -> None:
        adapter = _FakeReadAdapter({"scope-a": (_handoff(2),), "scope-b": (_handoff(4),)})

        selected = await select_optimistic_stable_handoffs(
            adapter,
            (_workstream("scope-b", version=3), _workstream("scope-a", version=2)),
        )

        assert adapter.calls == ["scope-a", "scope-b", "scope-a", "scope-b"]
        assert tuple(entry.scope_id for entry in selected) == ("scope-a", "scope-b")
        assert tuple(entry.workstream_revision for entry in selected) == (2, 3)
        assert tuple(entry.handoff_ref.revision for entry in selected if entry.handoff_ref is not None) == (2, 4)

    asyncio.run(scenario())


def test_optimistic_selection_preserves_explicit_no_handoff() -> None:
    async def scenario() -> None:
        selected = await select_optimistic_stable_handoffs(
            _FakeReadAdapter({"scope-empty": (None,)}),
            (_workstream("scope-empty"),),
        )

        assert selected[0].status == "no_handoff"
        assert selected[0].handoff_ref is None

    asyncio.run(scenario())


def test_optimistic_selection_retries_a_change_then_freezes_stable_heads() -> None:
    async def scenario() -> None:
        adapter = _FakeReadAdapter({"scope-a": (_handoff(1), _handoff(2), _handoff(2), _handoff(2))})

        selected = await select_optimistic_stable_handoffs(adapter, (_workstream("scope-a"),))

        assert adapter.calls == ["scope-a"] * 4
        assert selected[0].handoff_ref == ArtifactRef(family="handoff", artifact_id="handoff", revision=2)

    asyncio.run(scenario())


def test_optimistic_selection_reports_busy_after_bounded_instability() -> None:
    async def scenario() -> None:
        adapter = _FakeReadAdapter({"scope-a": tuple(_handoff(revision) for revision in range(1, 7))})

        with pytest.raises(HandoffReportBusyError) as captured:
            await select_optimistic_stable_handoffs(adapter, (_workstream("scope-a"),))

        assert captured.value.attempts == 3
        assert adapter.calls == ["scope-a"] * 6

    asyncio.run(scenario())


@pytest.mark.parametrize("attempts", [True, 1.5, "2", 0, 6])
def test_optimistic_selection_requires_a_strict_bounded_attempt_count(attempts: object) -> None:
    async def scenario() -> None:
        with pytest.raises(ValueError, match="attempts must be between"):
            await select_optimistic_stable_handoffs(
                _FakeReadAdapter({"scope-a": (_handoff(1),)}),
                (_workstream("scope-a"),),
                attempts=attempts,  # ty: ignore[invalid-argument-type]
            )

    asyncio.run(scenario())


class _FakeScopedApplication:
    def __init__(self, handoff: Handoff) -> None:
        self.handoff = handoff
        self.references: list[ArtifactRef] = []

    async def latest(self) -> Handoff | None:
        return self.handoff

    async def revision(self, reference: ArtifactRef, /) -> Handoff:
        self.references.append(reference)
        return self.handoff

    async def revisions(self) -> tuple[Handoff, ...]:
        return (self.handoff,)


class _FakeApplication:
    def __init__(self, scoped: _FakeScopedApplication) -> None:
        self.scoped = scoped
        self.scopes: list[str] = []

    def for_scope(self, scope_id: str, /) -> _FakeScopedApplication:
        self.scopes.append(scope_id)
        return self.scoped


def test_runtime_adapter_maps_only_existing_read_behaviors() -> None:
    async def scenario() -> None:
        handoff = _handoff(2)
        scoped = _FakeScopedApplication(handoff)
        application = _FakeApplication(scoped)
        adapter = RuntimeHandoffReadAdapter(application)
        reference = handoff.as_ref()

        assert await adapter.latest("scope-a") == handoff
        assert await adapter.get("scope-a", reference) == handoff
        assert await adapter.revisions("scope-a") == (handoff,)
        with pytest.raises(HandoffReportEvidenceCheckUnavailableError):
            await adapter.check_evidence("scope-a", reference)
        assert application.scopes == ["scope-a"] * 3
        assert scoped.references == [reference]

    asyncio.run(scenario())
