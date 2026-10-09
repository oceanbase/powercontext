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

The RFC lists content that finalisation "should reject or flag" and content that
"is invalid or should require correction". Both lists are stated as properties of
the Handoff content model, not as judgements about model output, so they can be
evaluated without a model.

Every finding names the requirement or invalid case it comes from. A finding is a
``violation`` when the RFC calls the content invalid, and an ``advisory`` when the
RFC only asks for flagging or when the text merely resembles a forbidden phrase.
The two severities are kept apart so a caller can gate on violations without
losing the weaker signal.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

DISPOSITIONS = frozenset({"continuable", "blocked", "complete"})

# RFC 1783 "Quality requirements": content that is invalid or requires correction.
CONTINUE_PREVIOUS_REQUIREMENT = "IV-continue-previous"
CONVERSATION_ABOVE_REQUIREMENT = "IV-conversation-above"
UNVERIFIED_TEST_REQUIREMENT = "IV-unverified-test-claim"
REWRITTEN_OBJECTIVE_REQUIREMENT = "IV-rewritten-objective"
NEXT_ACTION_EVIDENCE_REQUIREMENT = "IV-next-action-without-evidence"

# RFC 1783 "Quality requirements": properties the content must have.
OBJECTIVE_REQUIREMENT = "QR-objective"
STATE_REQUIREMENT = "QR-state"
DISPOSITION_REQUIREMENT = "QR-disposition"
NEXT_ACTION_REQUIREMENT = "QR-next-action"
EVIDENCE_REQUIREMENT = "QR-evidence"
OMISSIONS_REQUIREMENT = "QR-omissions"

_FORBIDDEN_PHRASES: Mapping[str, str] = MappingProxyType(
    {
        "continue the previous work": CONTINUE_PREVIOUS_REQUIREMENT,
        "continue previous work": CONTINUE_PREVIOUS_REQUIREMENT,
        "see the conversation above": CONVERSATION_ABOVE_REQUIREMENT,
        "see above": CONVERSATION_ABOVE_REQUIREMENT,
    }
)

# A statement that reports a test or verification outcome without carrying a
# pointer to the result is the case RFC 1783 calls invalid.
_TEST_OUTCOME_PATTERN = re.compile(
    r"\b(tests?|suite|checks?|build)\b[^.]{0,40}\b(pass|passes|passed|green|succeed|succeeds|succeeded|clean)\b",
    re.IGNORECASE,
)

_WHITESPACE = re.compile(r"\s+")


class RolloverQualityError(Exception):
    """A quality check could not be evaluated for the supplied content."""


@dataclass(frozen=True)
class HandoffStatement:
    """One state statement or next action, with the evidence that backs it."""

    text: str
    evidence: tuple[str, ...] = ()
    evidence_unavailable_reason: str | None = None


@dataclass(frozen=True)
class HandoffContent:
    """The RFC 0048 Handoff content fields a rollover draft must fill in."""

    objective: str
    state_statements: tuple[HandoffStatement, ...]
    disposition: str | None
    next_action: HandoffStatement | None
    omissions: tuple[str, ...]


@dataclass(frozen=True)
class QualityFinding:
    """One requirement outcome for one field of the content."""

    requirement: str
    severity: str
    field: str
    detail: str


@dataclass(frozen=True)
class QualityReport:
    """The complete set of findings for one rollover draft."""

    findings: tuple[QualityFinding, ...]

    @property
    def violations(self) -> tuple[QualityFinding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == "violation")

    @property
    def advisories(self) -> tuple[QualityFinding, ...]:
        return tuple(finding for finding in self.findings if finding.severity == "advisory")

    @property
    def violations_by_requirement(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in self.violations:
            counts[finding.requirement] = counts.get(finding.requirement, 0) + 1
        return counts

    @property
    def satisfied(self) -> bool:
        """Return whether the content may be published without correction."""

        return not self.violations


def check_rollover_quality(content: HandoffContent, *, caller_objective: str) -> QualityReport:
    """Evaluate the RFC 1783 quality requirements against one rollover draft.

    ``caller_objective`` is the objective the caller supplied. RFC 1783 makes an
    objective rewritten by generation invalid, so the draft is compared against it
    rather than trusted on its own.
    """

    findings: list[QualityFinding] = []
    findings.extend(_objective_findings(content, caller_objective))
    findings.extend(_state_findings(content.state_statements))
    findings.extend(_disposition_findings(content))
    findings.extend(_next_action_findings(content))
    findings.extend(_omission_findings(content.omissions))
    if not content.omissions:
        findings.append(
            QualityFinding(
                requirement=OMISSIONS_REQUIREMENT,
                severity="violation",
                field="omissions",
                detail="content declares no omission, so no unverified check or missing host state is disclosed",
            )
        )
    return QualityReport(findings=tuple(findings))


def _objective_findings(content: HandoffContent, caller_objective: str) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    objective = content.objective
    if not objective.strip():
        findings.append(
            QualityFinding(
                requirement=OBJECTIVE_REQUIREMENT,
                severity="violation",
                field="objective",
                detail="objective is empty",
            )
        )
        return findings
    if _normalize(objective) != _normalize(caller_objective):
        findings.append(
            QualityFinding(
                requirement=REWRITTEN_OBJECTIVE_REQUIREMENT,
                severity="violation",
                field="objective",
                detail="objective differs from the objective the caller supplied",
            )
        )
    findings.extend(_forbidden_phrase_findings(objective, "objective"))
    return findings


def _state_findings(statements: Iterable[HandoffStatement]) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    materialised = tuple(statements)
    if not materialised:
        findings.append(
            QualityFinding(
                requirement=STATE_REQUIREMENT,
                severity="violation",
                field="state_statements",
                detail="content carries no current state statement",
            )
        )
        return findings
    for index, statement in enumerate(materialised):
        field = f"state_statements[{index}]"
        findings.extend(_forbidden_phrase_findings(statement.text, field))
        findings.extend(_evidence_findings(statement, field, EVIDENCE_REQUIREMENT))
    return findings


def _disposition_findings(content: HandoffContent) -> list[QualityFinding]:
    disposition = content.disposition
    if disposition is None or not disposition.strip():
        return [
            QualityFinding(
                requirement=DISPOSITION_REQUIREMENT,
                severity="violation",
                field="disposition",
                detail="content carries no disposition",
            )
        ]
    if disposition not in DISPOSITIONS:
        return [
            QualityFinding(
                requirement=DISPOSITION_REQUIREMENT,
                severity="violation",
                field="disposition",
                detail=f"disposition must be one of {', '.join(sorted(DISPOSITIONS))}",
            )
        ]
    return []


def _next_action_findings(content: HandoffContent) -> list[QualityFinding]:
    action = content.next_action
    if action is None:
        # RFC 1783: a next action is required unless the disposition explains why
        # none exists. Only "continuable" promises that work continues.
        if content.disposition == "continuable":
            return [
                QualityFinding(
                    requirement=NEXT_ACTION_REQUIREMENT,
                    severity="violation",
                    field="next_action",
                    detail="continuable content carries no next action",
                )
            ]
        return []
    findings = _forbidden_phrase_findings(action.text, "next_action")
    findings.extend(_evidence_findings(action, "next_action", NEXT_ACTION_EVIDENCE_REQUIREMENT))
    return findings


def _evidence_findings(statement: HandoffStatement, field: str, requirement: str) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    if _looks_like_test_outcome(statement.text) and not statement.evidence:
        findings.append(
            QualityFinding(
                requirement=UNVERIFIED_TEST_REQUIREMENT,
                severity="violation",
                field=field,
                detail="content reports a test or verification outcome without a cited result",
            )
        )
    if not statement.evidence and not _has_text(statement.evidence_unavailable_reason):
        findings.append(
            QualityFinding(
                requirement=requirement,
                severity="violation",
                field=field,
                detail="statement carries no evidence and no omission explaining why evidence is unavailable",
            )
        )
    return findings


def _omission_findings(omissions: Iterable[str]) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    for index, omission in enumerate(omissions):
        if not omission.strip():
            findings.append(
                QualityFinding(
                    requirement=OMISSIONS_REQUIREMENT,
                    severity="violation",
                    field=f"omissions[{index}]",
                    detail="omission is empty",
                )
            )
        else:
            findings.extend(_forbidden_phrase_findings(omission, f"omissions[{index}]"))
    return findings


def _forbidden_phrase_findings(text: str, field: str) -> list[QualityFinding]:
    findings: list[QualityFinding] = []
    normalized = _normalize(text)
    for phrase, requirement in _FORBIDDEN_PHRASES.items():
        if phrase not in normalized:
            continue
        # An exact match is the content RFC 1783 calls invalid. A longer field that
        # merely contains the phrase is flagged, because a caller still needs to
        # read it before publishing.
        severity = "violation" if normalized == phrase else "advisory"
        findings.append(
            QualityFinding(
                requirement=requirement,
                severity=severity,
                field=field,
                detail=f"field contains the forbidden phrase {phrase!r}",
            )
        )
    return findings


def _looks_like_test_outcome(text: str) -> bool:
    return _TEST_OUTCOME_PATTERN.search(text) is not None


def _normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text.strip().lower()).strip(" .;:,!")


def _has_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())
