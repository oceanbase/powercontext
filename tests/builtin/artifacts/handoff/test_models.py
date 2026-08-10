from __future__ import annotations

import pytest
from pydantic import ValidationError

from powercontext.builtin.artifacts.handoff import (
    ActivateHandoff,
    HandoffContent,
    HandoffDraft,
    HandoffEvidenceCheck,
    HandoffResolution,
    HandoffSourceCitation,
    HandoffStatement,
    PreparedHandoff,
    PrepareHandoff,
)
from powercontext.sources import SourceRef


def _statement(text: str = "Implementation is ready.") -> HandoffStatement:
    return HandoffStatement(
        text=text,
        citations=(
            HandoffSourceCitation(
                source_ref=SourceRef(source_type="content", source_id="turn-1"),
            ),
        ),
    )


def test_draft_preserves_inspected_content_during_finalization() -> None:
    draft = HandoffDraft(
        objective="Complete parser error handling.",
        state=(_statement(),),
        disposition="continuable",
        next_action=_statement("Run regression tests."),
    )

    content = draft.as_content()

    assert content == HandoffContent(
        objective=draft.objective,
        state=draft.state,
        disposition=draft.disposition,
        next_action=draft.next_action,
    )
    assert content.schema_version == "powercontext.handoff.v1"
    assert content.model_dump(mode="json", by_alias=True)["schema"] == "powercontext.handoff.v1"


@pytest.mark.parametrize(
    "value",
    [
        {"objective": "", "state": (_statement(),), "disposition": "continuable"},
        {"objective": "   ", "state": (_statement(),), "disposition": "continuable"},
        {"objective": "objective", "state": (), "disposition": "continuable"},
        {"objective": "objective", "state": (_statement(),), "disposition": "unknown"},
        {
            "objective": "objective",
            "state": ({"text": "claim", "citations": ()},),
            "disposition": "continuable",
        },
    ],
)
def test_content_contract_rejects_incomplete_handoffs(value: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        HandoffDraft.model_validate(value)


def test_handoff_values_are_frozen_after_validation() -> None:
    content = HandoffDraft(
        objective="objective",
        state=(_statement(),),
        disposition="complete",
    ).as_content()

    with pytest.raises(ValidationError):
        content.objective = "rewritten"


def test_unknown_content_and_envelope_versions_are_rejected() -> None:
    content = HandoffDraft(
        objective="objective",
        state=(_statement(),),
        disposition="complete",
    ).as_content()
    prepared = PreparedHandoff(scope_id="project", base=None, content=content)
    content_payload = content.model_dump(mode="json", by_alias=True)
    prepared_payload = prepared.model_dump(mode="json", by_alias=True)
    content_payload["schema"] = "powercontext.handoff.v2"
    prepared_payload["schema"] = "powercontext.prepared-handoff.v2"

    with pytest.raises(ValidationError):
        HandoffContent.model_validate(content_payload)
    with pytest.raises(ValidationError):
        PreparedHandoff.model_validate(prepared_payload)


def test_handoff_content_is_bounded() -> None:
    with pytest.raises(ValidationError):
        HandoffDraft(
            objective="x" * 8193,
            state=(_statement(),),
            disposition="continuable",
        )
    with pytest.raises(ValidationError):
        HandoffDraft(
            objective="objective",
            state=tuple(_statement(str(index)) for index in range(65)),
            disposition="continuable",
        )


def test_prepare_action_requires_unique_bounded_evidence() -> None:
    citation = _statement().citations[0]

    with pytest.raises(ValidationError):
        PrepareHandoff(
            objective="Complete parser error handling.",
            evidence=(citation, citation),
        )
    with pytest.raises(ValidationError):
        PrepareHandoff(
            objective="Complete parser error handling.",
            evidence=(citation,),
            max_bytes=32_769,
        )


def test_activation_adds_the_boundary_source_once_and_rejects_duplicate_evidence() -> None:
    citation = _statement().citations[0]
    assert isinstance(citation, HandoffSourceCitation)

    activation = ActivateHandoff(
        boundary_source=citation.source_ref,
        objective="Complete parser error handling.",
        evidence=(citation,),
    )

    assert activation.action_evidence() == (citation,)
    with pytest.raises(ValidationError):
        ActivateHandoff(
            boundary_source=SourceRef(source_type="content", source_id="boundary"),
            objective="Complete parser error handling.",
            evidence=(citation, citation),
        )


def test_resolution_requires_one_evidence_check_per_statement() -> None:
    content = HandoffDraft(
        objective="objective",
        state=(_statement(),),
        disposition="continuable",
    ).as_content()

    with pytest.raises(ValidationError):
        HandoffResolution(
            status="resolved",
            scope_id="project",
            content=content,
            selection="prepared",
        )

    resolution = HandoffResolution(
        status="resolved",
        scope_id="project",
        content=content,
        selection="prepared",
        evidence_checks=(
            HandoffEvidenceCheck(
                claim="state",
                state_index=0,
                status="available",
            ),
        ),
    )

    assert resolution.status == "resolved"

    with pytest.raises(ValidationError):
        HandoffResolution(
            status="resolved",
            scope_id="project",
            content=content,
            selection="prepared",
            evidence_checks=(
                HandoffEvidenceCheck(
                    claim="state",
                    state_index=0,
                    status="unavailable",
                    unavailable_evidence=(
                        HandoffSourceCitation(
                            source_ref=SourceRef(
                                source_type="content",
                                source_id="unrelated",
                            )
                        ),
                    ),
                ),
            ),
        )
