from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.handoff_report import (
    ActivityAgent,
    ActivityVcsContext,
    ExternalReference,
    ProjectDescriptor,
    ReportActivityEvent,
    ReportSelectionEntry,
    WorkstreamDescriptor,
    activity_sort_key,
    normalized_sort_text,
    selection_sort_key,
    workstream_sort_key,
)

NOW = datetime(2026, 8, 5, 4, 0, tzinfo=UTC)


def _project(**changes) -> ProjectDescriptor:
    values = {
        "project_id": "prj_01K",
        "project_key": "powercontext",
        "title": "PowerContext",
        "timezone": "Asia/Shanghai",
        "version": 1,
    }
    values.update(changes)
    return ProjectDescriptor.model_validate(values)


def _workstream(**changes) -> WorkstreamDescriptor:
    values = {
        "scope_id": "scope-report",
        "project_id": "prj_01K",
        "title": "Handoff Report",
        "kind": "feature",
        "version": 1,
    }
    values.update(changes)
    return WorkstreamDescriptor.model_validate(values)


def _activity(**changes) -> ReportActivityEvent:
    values = {
        "event_id": "evt-1",
        "project_id": "prj_01K",
        "source": "git_commit",
        "source_event_id": "commit-abc",
        "occurred_at": NOW - timedelta(hours=1),
        "observed_at": NOW,
        "time_basis": "source_reported",
    }
    values.update(changes)
    return ReportActivityEvent.model_validate(values)


def test_project_descriptor_is_versioned_frozen_and_serializes_schema_alias() -> None:
    project = _project(description="A durable project handoff", default_locale="en")

    assert project.model_dump(by_alias=True)["schema"] == "powercontext.project.v1"
    assert project.default_locale == "en"
    with pytest.raises(ValidationError):
        project.title = "Changed"


@pytest.mark.parametrize("timezone_name", ["", " Asia/Shanghai", "Not/A_Timezone"])
def test_project_descriptor_rejects_invalid_timezone(timezone_name: str) -> None:
    with pytest.raises(ValidationError):
        _project(timezone=timezone_name)


def test_workstream_descriptor_reuses_scope_identity_and_bounds_metadata() -> None:
    issue = ExternalReference(kind="issue", provider="github", external_id="42")
    workstream = _workstream(key="handoff-report", external_refs=(issue,), labels=("report", "v1"))

    assert workstream.scope_id == "scope-report"
    assert workstream.external_refs == (issue,)

    with pytest.raises(ValidationError, match="labels must be unique"):
        _workstream(labels=("report", "report"))
    with pytest.raises(ValidationError, match="external references must be unique"):
        _workstream(external_refs=(issue, issue))


def test_activity_event_keeps_observation_untrusted_and_explicitly_timed() -> None:
    event = _activity(
        scope_id="scope-report",
        agent=ActivityAgent(provider="codex", label="agent-1"),
        session_id="session-1",
        vcs_context=ActivityVcsContext(branch="handoff_report", head_revision="abc123"),
    )

    assert event.trust == "untrusted_observation"
    assert event.effective_period_time() == NOW - timedelta(hours=1)
    assert event.model_dump(by_alias=True)["schema"] == "powercontext.handoff-report-activity.v1"


def test_activity_event_does_not_invent_period_time() -> None:
    current = _activity(
        source="git_worktree",
        source_event_id="worktree-current",
        occurred_at=None,
        time_basis="current_only",
    )
    unknown = _activity(
        source="coding_session",
        source_event_id="unknown-session",
        occurred_at=None,
        time_basis="unknown",
    )

    assert current.effective_period_time() is None
    assert unknown.effective_period_time() is None


def test_activity_event_validates_timestamp_basis_and_utc_observation() -> None:
    with pytest.raises(ValidationError, match="must contain occurred_at"):
        _activity(occurred_at=None)
    with pytest.raises(ValidationError, match="only valid for source-reported"):
        _activity(time_basis="first_seen")
    with pytest.raises(ValidationError, match="observed_at must be UTC"):
        _activity(observed_at=NOW.astimezone(timezone(timedelta(hours=8))))
    with pytest.raises(ValidationError, match="occurred_at must include a UTC offset"):
        _activity(occurred_at=datetime(2026, 8, 5, 3, 0))


def test_report_selection_entry_requires_exact_handoff_or_explicit_absence() -> None:
    handoff_ref = ArtifactRef(family="handoff", artifact_id="handoff-1", revision=2)
    selected = ReportSelectionEntry(
        scope_id="scope-report",
        workstream_revision=3,
        status="selected",
        handoff_ref=handoff_ref,
    )
    absent = ReportSelectionEntry(scope_id="scope-empty", workstream_revision=1, status="no_handoff")

    assert selected.handoff_ref == handoff_ref
    assert absent.handoff_ref is None
    with pytest.raises(ValidationError, match="must contain an exact Handoff"):
        ReportSelectionEntry(scope_id="scope-report", workstream_revision=3, status="selected")
    with pytest.raises(ValidationError, match="cannot contain"):
        ReportSelectionEntry(
            scope_id="scope-report",
            workstream_revision=3,
            status="no_handoff",
            handoff_ref=handoff_ref,
        )
    with pytest.raises(ValidationError, match="Handoff family"):
        ReportSelectionEntry(
            scope_id="scope-report",
            workstream_revision=3,
            status="selected",
            handoff_ref=ArtifactRef(family="memory", artifact_id="memory-1", revision=1),
        )


def test_stable_sort_helpers_use_normalized_title_scope_and_effective_time() -> None:
    composed = _workstream(scope_id="scope-2", title="Café")
    decomposed = _workstream(scope_id="scope-1", title="Cafe\u0301")
    late = _activity(event_id="evt-late", source_event_id="late", occurred_at=NOW)
    early = _activity(event_id="evt-early", source_event_id="early", occurred_at=NOW - timedelta(days=1))
    current = _activity(
        event_id="evt-current",
        source="git_worktree",
        source_event_id="current",
        occurred_at=None,
        time_basis="current_only",
    )
    selected = ReportSelectionEntry(
        scope_id="scope-2",
        workstream_revision=1,
        status="selected",
        handoff_ref=ArtifactRef(family="handoff", artifact_id="handoff-1", revision=1),
    )
    absent = ReportSelectionEntry(scope_id="scope-1", workstream_revision=1, status="no_handoff")

    assert normalized_sort_text(composed.title) == normalized_sort_text(decomposed.title)
    assert sorted((composed, decomposed), key=workstream_sort_key) == [decomposed, composed]
    assert sorted((current, late, early), key=activity_sort_key) == [early, late, current]
    assert sorted((selected, absent), key=selection_sort_key) == [absent, selected]
