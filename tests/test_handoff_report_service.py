from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
import yaml
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.handoff import (
    Handoff,
    HandoffContent,
    HandoffEvidenceCheck,
    HandoffSourceCitation,
    HandoffStatement,
)
from powercontext.builtin.handoff_report.errors import (
    HandoffReportEvidenceCheckUnavailableError,
    HandoffReportInconsistentError,
)
from powercontext.builtin.handoff_report.models import (
    ActivityAgent,
    ActivityVcsContext,
    ExternalReference,
    ProjectDescriptor,
    ReportActivityEvent,
    WorkstreamDescriptor,
)
from powercontext.builtin.handoff_report.rendering import render_markdown
from powercontext.builtin.handoff_report.report import HandoffReport
from powercontext.builtin.handoff_report.service import HandoffReportService
from powercontext.sources import SourceRef


def _project() -> ProjectDescriptor:
    return ProjectDescriptor(
        project_id="prj-1",
        project_key="powercontext",
        title="PowerContext <script>",
        timezone="Asia/Shanghai",
        version=1,
    )


def _workstream(scope_id: str, title: str) -> WorkstreamDescriptor:
    return WorkstreamDescriptor(
        scope_id=scope_id,
        project_id="prj-1",
        title=title,
        kind="feature",
        version=1,
    )


def _handoff(
    revision: int = 1,
    *,
    objective: str = "Implement the report.",
    state: str = "The model exists.",
    next_action: str = "Add the API.",
) -> Handoff:
    citation = HandoffSourceCitation(source_ref=SourceRef(source_type="content", source_id="source-1"))
    return Handoff(
        artifact_id="handoff",
        revision=revision,
        content=HandoffContent(
            objective=objective,
            state=(HandoffStatement(text=state, citations=(citation,)),),
            disposition="continuable",
            next_action=HandoffStatement(text=next_action, citations=(citation,)),
        ),
    )


def _activity(event_id: str, scope_id: str | None, **changes) -> ReportActivityEvent:
    values = {
        "event_id": event_id,
        "project_id": "prj-1",
        "scope_id": scope_id,
        "source": "coding_session",
        "source_event_id": f"source-{event_id}",
        "occurred_at": datetime(2026, 8, 5, 1, tzinfo=UTC),
        "observed_at": datetime(2026, 8, 5, 2, tzinfo=UTC),
        "time_basis": "source_reported",
    }
    values.update(changes)
    return ReportActivityEvent.model_validate(values)


def _report_with_activities() -> HandoffReport:
    async def scenario() -> HandoffReport:
        return await HandoffReportService(_Adapter({"scope-a": _handoff()})).generate(
            _project(),
            (_workstream("scope-a", "Report"),),
            activities=(
                _activity("evt-assigned", "scope-a", title="Assigned event"),
                _activity("evt-unassigned", None, title="Unassigned event"),
            ),
            activity_cursor=7,
            activity_coverage="captured",
            generated_at=datetime(2026, 8, 5, tzinfo=UTC),
        )

    return asyncio.run(scenario())


def _revalidate(report: HandoffReport) -> HandoffReport:
    return HandoffReport.model_validate(report.model_dump(by_alias=True))


class _Adapter:
    def __init__(self, values: dict[str, Handoff | None]) -> None:
        self.values = values
        self.exact_reads: list[tuple[str, ArtifactRef]] = []

    async def latest(self, scope_id: str, /) -> Handoff | None:
        return self.values[scope_id]

    async def get(self, scope_id: str, reference: ArtifactRef, /) -> Handoff:
        self.exact_reads.append((scope_id, reference))
        value = self.values[scope_id]
        assert value is not None and value.as_ref() == reference
        return value

    async def revisions(self, scope_id: str, /) -> tuple[Handoff, ...]:
        value = self.values[scope_id]
        return () if value is None else (value,)

    async def check_evidence(
        self,
        scope_id: str,
        reference: ArtifactRef,
        /,
    ) -> tuple[HandoffEvidenceCheck, ...]:
        del scope_id, reference
        return (
            HandoffEvidenceCheck(claim="state", state_index=0, status="available"),
            HandoffEvidenceCheck(claim="next_action", status="available"),
        )


class _InconsistentAdapter(_Adapter):
    async def get(self, scope_id: str, reference: ArtifactRef, /) -> Handoff:
        del scope_id, reference
        return _handoff(2)


class _UnavailableEvidenceAdapter(_Adapter):
    async def check_evidence(
        self,
        scope_id: str,
        reference: ArtifactRef,
        /,
    ) -> tuple[HandoffEvidenceCheck, ...]:
        del scope_id, reference
        raise HandoffReportEvidenceCheckUnavailableError


def test_service_assembles_exact_read_only_report_and_bilingual_markdown() -> None:
    async def scenario() -> None:
        adapter = _Adapter({"scope-a": _handoff(), "scope-b": None})
        report = await HandoffReportService(adapter).generate(
            _project(),
            (_workstream("scope-b", "Missing"), _workstream("scope-a", "Report")),
            generated_at=datetime(2026, 8, 5, tzinfo=UTC),
        )

        assert tuple(entry.scope_id for entry in report.end_selection) == ("scope-a", "scope-b")
        assert report.workstreams[0].content is not None
        assert report.workstreams[1].reporting_status == "no_handoff"
        assert report.coverage.activity_coverage == "not_configured"
        assert adapter.exact_reads == [("scope-a", report.end_selection[0].handoff_ref)]

        chinese = render_markdown(report)
        english = render_markdown(report.model_copy(update={"locale": "en"}))
        assert "# PowerContext 项目交接报告" in chinese
        assert "# PowerContext Project Handoff Report" in english
        assert "Activity Adapter 未配置" in chinese
        assert "PowerContext &lt;script&gt;" in chinese
        assert "Add the API\\." in chinese

    asyncio.run(scenario())


def test_service_can_skip_evidence_checks_without_claiming_availability() -> None:
    async def scenario() -> None:
        report = await HandoffReportService(_Adapter({"scope-a": _handoff()})).generate(
            _project(),
            (_workstream("scope-a", "Report"),),
            include_evidence_checks=False,
        )

        assert report.workstreams[0].evidence_checks == "not_checked"

    asyncio.run(scenario())


def test_service_degrades_unavailable_evidence_checks_per_workstream() -> None:
    async def scenario() -> None:
        report = await HandoffReportService(_UnavailableEvidenceAdapter({"scope-a": _handoff()})).generate(
            _project(),
            (_workstream("scope-a", "Report"),),
            include_evidence_checks=True,
        )

        assert report.workstreams[0].reporting_status == "evidence_unavailable"
        assert report.workstreams[0].evidence_checks == "not_checked"
        assert report.workstreams[0].evidence_unavailable is True
        assert report.coverage.unchecked_evidence_workstreams == 1
        assert report.coverage.unavailable_evidence_workstreams == 1

    asyncio.run(scenario())


def test_service_fails_closed_when_exact_read_does_not_match_frozen_selection() -> None:
    async def scenario() -> None:
        adapter = _InconsistentAdapter({"scope-a": _handoff(1)})

        with pytest.raises(HandoffReportInconsistentError, match="scope-a"):
            await HandoffReportService(adapter).generate(
                _project(),
                (_workstream("scope-a", "Report"),),
            )

    asyncio.run(scenario())


def test_service_enforces_report_selection_limits_before_reading_handoffs() -> None:
    async def scenario() -> None:
        service = HandoffReportService(_Adapter({}))
        too_many_workstreams = tuple(_workstream(f"scope-{index}", f"Workstream {index}") for index in range(101))
        with pytest.raises(ValueError, match="at most 100 Workstreams"):
            await service.generate(_project(), too_many_workstreams)

        event = _activity("event", None)
        with pytest.raises(ValueError, match="at most 5000 Activity Events"):
            await service.generate(_project(), (), activities=(event,) * 5_001)

        with pytest.raises(ValueError, match="activity_cursor"):
            await service.generate(_project(), (), activity_cursor=True)

    asyncio.run(scenario())


def test_markdown_carries_exact_selection_cursor_and_all_activity() -> None:
    report = _report_with_activities()

    markdown = render_markdown(report)
    metadata = yaml.safe_load(markdown.split("---", 2)[1])

    assert metadata["project_id"] == "prj-1"
    assert metadata["project_version"] == 1
    assert metadata["activity_cursor"] == 7
    assert metadata["end_selection"] == [
        {
            "scope_id": "scope-a",
            "workstream_revision": 1,
            "status": "selected",
            "handoff_ref": {"family": "handoff", "artifact_id": "handoff", "revision": 1},
        }
    ]
    assert metadata["activity_selection"] == ["evt-assigned", "evt-unassigned"]
    assert "end_selection:" in markdown
    assert 'scope_id: "scope-a"' in markdown
    assert "workstream_revision: 1" in markdown
    assert 'family: "handoff"' in markdown
    assert 'artifact_id: "handoff"' in markdown
    assert "revision: 1" in markdown
    assert "activity_selection:" in markdown
    assert '  - "evt-assigned"' in markdown
    assert '  - "evt-unassigned"' in markdown
    assert "#### 观察到的 Activity" in markdown
    assert "Assigned event" in markdown
    assert "## 未分配 Activity" in markdown
    assert "Unassigned event" in markdown
    assert "来源事件 ID" in markdown
    assert "信任标记" in markdown
    assert render_markdown(report) == markdown

    english = render_markdown(report.model_copy(update={"locale": "en"}))
    assert "#### Observed Activity" in english
    assert "## Unassigned Activity" in english
    assert '  - "evt-assigned"' in english


def test_markdown_escapes_user_text_and_uses_safe_backtick_delimiters() -> None:
    async def scenario() -> HandoffReport:
        project = _project().model_copy(
            update={"title": "Project [link](https://evil)\n![image](x)<script>alert(1)</script>"}
        )
        workstream = _workstream("scope`tick", "Feature # heading\n[click](javascript:evil)")
        handoff = _handoff(
            objective="Objective **bold**\n![objective](https://evil)",
            state="State <img src=x onerror=alert(1)>\nsecond line",
            next_action="Next [link](https://evil)",
        )
        reference = ExternalReference(
            kind="issue",
            provider="host`provider",
            external_id="issue](javascript:evil)",
            url="https://evil/`](x)",
        )
        activity = _activity(
            "evt`assigned",
            "scope`tick",
            title="Activity ![image](x)\n<title>",
            summary="Summary [link](javascript:evil)\nnext line",
            source_ref=reference,
            evidence_refs=(reference,),
            agent=ActivityAgent(provider="codex`host", label="agent [admin]"),
            session_id="session`id",
            vcs_context=ActivityVcsContext(branch="feature/[link]", head_revision="abc`123"),
        )
        return await HandoffReportService(_Adapter({"scope`tick": handoff})).generate(
            project,
            (workstream,),
            activities=(activity,),
            activity_cursor=1,
            activity_coverage="captured",
            generated_at=datetime(2026, 8, 5, tzinfo=UTC),
        )

    markdown = render_markdown(asyncio.run(scenario()))

    assert "<script>" not in markdown
    assert "<img" not in markdown
    assert "![image](x)" not in markdown
    assert "[link](https://evil)" not in markdown
    assert "&lt;script&gt;" in markdown
    assert r"\!\[image\]\(x\)" in markdown
    assert r"Project \[link\]\(https://evil\) \!\[image\]\(x\)&lt;script&gt;" in markdown
    assert r"State &lt;img src=x onerror=alert\(1\)&gt; second line" in markdown
    assert r"Summary \[link\]\(javascript:evil\) next line" in markdown
    assert "`` scope`tick ``" in markdown
    assert "`` evt`assigned ``" in markdown
    assert "`` session`id ``" in markdown


def test_canonical_report_rejects_wrong_project_revision_and_handoff_projection() -> None:
    report = _report_with_activities()
    item = report.workstreams[0]
    selection = report.end_selection[0]

    wrong_project = item.model_copy(
        update={"workstream": item.workstream.model_copy(update={"project_id": "prj-other"})}
    )
    with pytest.raises(ValidationError, match="belong to the Report Project"):
        _revalidate(report.model_copy(update={"workstreams": (wrong_project,)}))

    wrong_revision = selection.model_copy(update={"workstream_revision": item.workstream.version + 1})
    with pytest.raises(ValidationError, match="Workstream revision"):
        _revalidate(report.model_copy(update={"end_selection": (wrong_revision,)}))

    wrong_ref = ArtifactRef(family="handoff", artifact_id="different", revision=1)
    wrong_handoff = item.model_copy(update={"handoff_ref": wrong_ref})
    with pytest.raises(ValidationError, match="Handoff reference"):
        _revalidate(report.model_copy(update={"workstreams": (wrong_handoff,)}))


def test_canonical_report_rejects_duplicate_scopes_and_activity_ids() -> None:
    report = _report_with_activities()
    duplicate_scope = report.model_copy(
        update={
            "end_selection": (report.end_selection[0], report.end_selection[0]),
            "workstreams": (report.workstreams[0], report.workstreams[0]),
            "coverage": report.coverage.model_copy(update={"selected_workstreams": 2}),
            "activity_selection": ("evt-assigned", "evt-assigned", "evt-unassigned"),
        }
    )
    with pytest.raises(ValidationError, match="selection scopes must be unique"):
        _revalidate(duplicate_scope)

    assigned = report.workstreams[0].activities[0]
    duplicate_event = report.unassigned_activity[0].model_copy(update={"event_id": assigned.event_id})
    duplicate_activity = report.model_copy(
        update={
            "unassigned_activity": (duplicate_event,),
            "activity_selection": (assigned.event_id, assigned.event_id),
        }
    )
    with pytest.raises(ValidationError, match="Activity Event ids must be unique"):
        _revalidate(duplicate_activity)


@pytest.mark.parametrize(
    ("updates", "message"),
    [
        ({"project_id": "prj-other"}, "belong to the Report Project"),
        ({"scope_id": "scope-other"}, "scope must match"),
    ],
)
def test_canonical_report_rejects_invalid_assigned_activity_ownership(updates, message: str) -> None:
    report = _report_with_activities()
    item = report.workstreams[0]
    event = item.activities[0].model_copy(update=updates)
    invalid_item = item.model_copy(update={"activities": (event,)})

    with pytest.raises(ValidationError, match=message):
        _revalidate(report.model_copy(update={"workstreams": (invalid_item,)}))


def test_canonical_report_rejects_invalid_unassigned_activity_and_selection() -> None:
    report = _report_with_activities()
    unassigned = report.unassigned_activity[0]

    wrong_project = unassigned.model_copy(update={"project_id": "prj-other"})
    with pytest.raises(ValidationError, match="belong to the Report Project"):
        _revalidate(report.model_copy(update={"unassigned_activity": (wrong_project,)}))

    selected_scope = unassigned.model_copy(update={"scope_id": "scope-a"})
    with pytest.raises(ValidationError, match="cannot be unassigned"):
        _revalidate(report.model_copy(update={"unassigned_activity": (selected_scope,)}))

    with pytest.raises(ValidationError, match="activity_selection"):
        _revalidate(report.model_copy(update={"activity_selection": ("evt-assigned",)}))

    wrong_count = report.coverage.model_copy(update={"unassigned_activity_count": 0})
    with pytest.raises(ValidationError, match="unassigned_activity_count"):
        _revalidate(report.model_copy(update={"coverage": wrong_count}))
