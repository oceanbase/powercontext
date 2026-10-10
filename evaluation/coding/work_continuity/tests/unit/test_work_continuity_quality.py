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

"""RFC 1783 Rollover Handoff quality requirements as deterministic checks.

The RFC states its requirements as properties of the Handoff content model, so
each one is exercised here as a content field that is either present, missing,
or stated in a form the RFC calls invalid.
"""

from __future__ import annotations

import pytest

from powercontext_eval_work_continuity.quality import (
    CONTINUE_PREVIOUS_REQUIREMENT,
    CONVERSATION_ABOVE_REQUIREMENT,
    DISPOSITION_REQUIREMENT,
    EVIDENCE_REQUIREMENT,
    NEXT_ACTION_EVIDENCE_REQUIREMENT,
    NEXT_ACTION_REQUIREMENT,
    OBJECTIVE_REQUIREMENT,
    OMISSIONS_REQUIREMENT,
    REWRITTEN_OBJECTIVE_REQUIREMENT,
    STATE_REQUIREMENT,
    UNVERIFIED_TEST_REQUIREMENT,
    HandoffContent,
    HandoffStatement,
    QualityReport,
    check_rollover_quality,
)

OBJECTIVE = "Fix the retry base."


def state(text: str = "the retry base is 1 second", *, evidence: tuple[str, ...] = ("turn:4",)) -> HandoffStatement:
    return HandoffStatement(text=text, evidence=evidence)


def content(**overrides: object) -> HandoffContent:
    payload: dict[str, object] = {
        "objective": OBJECTIVE,
        "state_statements": (state(),),
        "disposition": "continuable",
        "next_action": HandoffStatement(text="call run_with_backoff", evidence=("fact:f1",)),
        "omissions": ("The maximum attempt count was never agreed.",),
    }
    payload.update(overrides)
    return HandoffContent(**payload)  # type: ignore[arg-type]


def check(**overrides: object) -> QualityReport:
    return check_rollover_quality(content(**overrides), caller_objective=OBJECTIVE)


def requirements(report: QualityReport) -> list[str]:
    return [finding.requirement for finding in report.findings]


def test_a_complete_draft_satisfies_every_requirement() -> None:
    report = check()

    assert report.violations == ()
    assert report.advisories == ()
    assert report.satisfied is True
    assert report.violations_by_requirement == {}


def test_an_empty_objective_is_a_violation() -> None:
    report = check(objective="   ")

    assert report.satisfied is False
    assert requirements(report) == [OBJECTIVE_REQUIREMENT]


def test_an_objective_the_generation_rewrote_is_a_violation() -> None:
    report = check(objective="Refactor the retry helper.")

    assert REWRITTEN_OBJECTIVE_REQUIREMENT in requirements(report)
    assert report.satisfied is False


@pytest.mark.parametrize(
    "objective",
    ["continue the previous work", "Continue previous work.", "please continue previous work"],
)
def test_a_call_to_continue_the_previous_work_is_flagged(objective: str) -> None:
    report = check_rollover_quality(content(objective=objective), caller_objective=objective)

    assert CONTINUE_PREVIOUS_REQUIREMENT in requirements(report)


def test_an_exact_forbidden_phrase_is_a_violation_but_a_longer_field_is_only_an_advisory() -> None:
    exact = check_rollover_quality(
        content(objective="continue the previous work"), caller_objective="continue the previous work"
    )
    embedded = check_rollover_quality(
        content(objective="Continue the previous work now."),
        caller_objective="Continue the previous work now.",
    )

    assert exact.violations_by_requirement == {CONTINUE_PREVIOUS_REQUIREMENT: 1}
    assert exact.satisfied is False
    assert embedded.violations == ()
    assert [finding.requirement for finding in embedded.advisories] == [CONTINUE_PREVIOUS_REQUIREMENT]
    assert embedded.satisfied is True


def test_pointing_at_the_conversation_above_is_flagged() -> None:
    report = check(omissions=("everything else: see the conversation above",))

    assert CONVERSATION_ABOVE_REQUIREMENT in requirements(report)
    assert report.violations == ()
    assert [finding.requirement for finding in report.advisories] == [CONVERSATION_ABOVE_REQUIREMENT]


def test_a_draft_without_a_state_statement_is_a_violation() -> None:
    report = check(state_statements=())

    assert requirements(report) == [STATE_REQUIREMENT]


def test_a_state_statement_without_evidence_or_a_reason_is_a_violation() -> None:
    report = check(state_statements=(state(evidence=()),))

    assert EVIDENCE_REQUIREMENT in requirements(report)
    assert report.satisfied is False


def test_a_state_statement_may_omit_evidence_when_it_says_why() -> None:
    statement = HandoffStatement(
        text="the docs live in docs/guide.md",
        evidence=(),
        evidence_unavailable_reason="the docs tree is not mounted here",
    )

    report = check(state_statements=(statement,))

    assert EVIDENCE_REQUIREMENT not in requirements(report)
    assert report.satisfied is True


def test_a_test_outcome_claim_stays_invalid_even_when_the_evidence_is_unavailable() -> None:
    """Explaining why evidence is missing does not make a pass claim publishable."""

    statement = HandoffStatement(
        text="the docs build passes",
        evidence=(),
        evidence_unavailable_reason="the build needs a mirror",
    )

    report = check(state_statements=(statement,))

    assert UNVERIFIED_TEST_REQUIREMENT in requirements(report)
    assert EVIDENCE_REQUIREMENT not in requirements(report)
    assert report.satisfied is False


def test_a_reported_test_outcome_without_a_cited_result_is_a_violation() -> None:
    report = check(state_statements=(state("the tests pass", evidence=()),))

    assert UNVERIFIED_TEST_REQUIREMENT in requirements(report)
    assert EVIDENCE_REQUIREMENT in requirements(report)


def test_a_reported_test_outcome_with_a_cited_result_is_allowed() -> None:
    report = check(state_statements=(state("the tests pass", evidence=("turn:9",)),))

    assert UNVERIFIED_TEST_REQUIREMENT not in requirements(report)
    assert report.satisfied is True


def test_a_missing_or_unknown_disposition_is_a_violation() -> None:
    missing = check(disposition=None)
    unknown = check(disposition="maybe")

    assert requirements(missing) == [DISPOSITION_REQUIREMENT]
    assert requirements(unknown) == [DISPOSITION_REQUIREMENT]
    assert "must be one of blocked, complete, continuable" in unknown.findings[0].detail


def test_a_continuable_draft_without_a_next_action_is_a_violation() -> None:
    report = check(next_action=None)

    assert NEXT_ACTION_REQUIREMENT in requirements(report)
    assert report.satisfied is False


def test_a_blocked_draft_without_a_next_action_is_allowed() -> None:
    report = check(disposition="blocked", next_action=None)

    assert NEXT_ACTION_REQUIREMENT not in requirements(report)
    assert report.satisfied is True


def test_a_next_action_without_evidence_is_a_violation() -> None:
    report = check(next_action=HandoffStatement(text="run the suite"))

    assert requirements(report) == [NEXT_ACTION_EVIDENCE_REQUIREMENT]


def test_a_draft_that_declares_no_omission_is_a_violation() -> None:
    report = check(omissions=())

    assert requirements(report) == [OMISSIONS_REQUIREMENT]
    assert "declares no omission" in report.findings[0].detail


def test_an_empty_omission_entry_is_a_violation() -> None:
    report = check(omissions=("The maximum attempt count was never agreed.", "  "))

    assert requirements(report) == [OMISSIONS_REQUIREMENT]
    assert report.findings[0].field == "omissions[1]"


def test_violations_by_requirement_counts_every_offending_field() -> None:
    report = check(
        state_statements=(state(evidence=()), state("the tests pass", evidence=())),
        next_action=None,
    )

    assert report.violations_by_requirement == {
        EVIDENCE_REQUIREMENT: 2,
        UNVERIFIED_TEST_REQUIREMENT: 1,
        NEXT_ACTION_REQUIREMENT: 1,
    }
    assert report.satisfied is False


def test_findings_name_the_field_they_came_from() -> None:
    report = check(state_statements=(state(evidence=()),), next_action=HandoffStatement(text="run the suite"))

    fields = {finding.requirement: finding.field for finding in report.findings}

    assert fields[EVIDENCE_REQUIREMENT] == "state_statements[0]"
    assert fields[NEXT_ACTION_EVIDENCE_REQUIREMENT] == "next_action"
