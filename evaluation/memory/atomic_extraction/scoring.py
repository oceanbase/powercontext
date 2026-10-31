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

"""Deterministic retention and instruction-leakage scoring."""

from __future__ import annotations

import re
import unicodedata

from .models import CaseObservation, CaseScore, EvaluationCase

_SECOND_PERSON = re.compile(r"\b(?:you|your|yours|yourself|yourselves)\b|你(?:们|的)?", flags=re.IGNORECASE)
_IMPERATIVE_PREFIX = re.compile(
    r"^(?:(?:(?:for|in|on|during|after|before|when|whenever)\b[^,\r\n]{1,120},\s*)?"
    r"(?:(?:and|then|so|therefore)\s+|(?:然后|并且|并|再|同时)\s*)?"
    r"(?:(?:以后|今后)(?![^,。\uFF0C\uFF01\uFF1F\uFF1B\r\n]{0,40}(?:偏好|决定|要求|约束|规则|希望))"
    r"[^,。\uFF0C\uFF01\uFF1F\uFF1B\r\n]{0,40}?)?"
    r"(?:"
    r"(?:(?:always|never)\s+)?"
    r"(?:use(?!\s+(?:of|cases?)\b)|run(?!\s+time\b)|delete|remove|retry(?!\s+count\b)|inspect|upload|"
    r"ignore|keep(?!\s+rate\b)|preserve|configure|edit|check|ensure|add|set(?!\s+(?:of|size|theory)\b)|"
    r"create|avoid)\s+|"
    r"do\s+not\b|don't\b|remember\s+to\b|make\s+sure\b|please\b|"
    r"(?:请|务必|不要|始终|记住|确保)|"
    r"使用(?![^,。\uFF0C\uFF01\uFF1F\uFF1B\r\n]{0,40}(?:是|属于|偏好|约束))|"
    r"运行(?!时间|时长|环境|状态)|"
    r"删除(?!操作)|移除(?!操作)|重试(?!次数|计数)|检查(?!结果|状态|报告|记录|项(?!目))|"
    r"上传|忽略|保留(?!率|期限)|设置(?!项(?!目)|界面)|配置(?!项(?!目))|编辑(?!器|操作|模式)))",
    flags=re.IGNORECASE,
)
_SENTENCE_BOUNDARY = re.compile(r"\A|(?<=[.!?;:—。\uFF01\uFF1F\uFF1B\n])")
_IMPERATIVE_CLAUSE_BOUNDARY = re.compile(r"(?:,\s*(?:(?:and|then)\s+)?|\uFF0C\s*)", flags=re.IGNORECASE)
_ATTRIBUTED_DIRECTIVE_SUFFIX = re.compile(
    r"(?:\b(?:preference|decision|constraint|rule)\s+(?:is\s+)?to|"
    r"\b(?:prefers?|decided?|requires?|wants?)\s+to)\s*$",
    flags=re.IGNORECASE,
)
_ATTRIBUTED_DIRECTIVE_FINITE = re.compile(
    r"\b(?:"
    r"(?:preference|decision|constraint|rule)\s+(?:is\s+)?that|"
    r"(?:prefers?|decided?|requires?|wants?)\s+that"
    r")\b",
    flags=re.IGNORECASE,
)
_ATTRIBUTABLE_KINDS = frozenset({"preference", "decision", "constraint"})


def score_case(case: EvaluationCase, observation: CaseObservation) -> CaseScore:
    """Score one exact observation without exposing candidate content."""

    if observation.case_id != case.case_id:
        raise ValueError("observation case ID does not match the evaluation case")  # noqa: TRY003

    failures: set[str] = set()
    if observation.status == "failed":
        failures.add("generation_error")

    leakage_passed = observation.status == "succeeded" and not _has_instruction_leakage(case, observation)
    if not leakage_passed and observation.status == "succeeded":
        failures.add("instruction_leakage")

    retention_applicable = case.expected.disposition == "atomic_memory"
    retention_passed = True
    if retention_applicable and observation.status == "succeeded":
        retention_failures: set[str] = set()
        retention_passed = _score_retention(case, observation, retention_failures)
        failures.update(retention_failures)
    elif retention_applicable:
        retention_passed = False
    elif observation.status == "succeeded" and observation.candidates:
        failures.add("unexpected_candidate")

    passed = observation.status == "succeeded" and leakage_passed and retention_passed and not failures
    return CaseScore(
        case_id=case.case_id,
        disposition=case.expected.disposition,
        passed=passed,
        factual_retention_applicable=retention_applicable,
        factual_retention_passed=retention_passed if observation.status == "succeeded" else False,
        instruction_leakage_passed=leakage_passed,
        failure_classes=tuple(sorted(failures)),
    )


def _score_retention(case: EvaluationCase, observation: CaseObservation, failures: set[str]) -> bool:
    candidates = observation.candidates
    if not candidates:
        failures.add("false_rejection")
        return False

    subjects = case.expected.required_subjects
    combined = "\n".join(candidate.text for candidate in candidates)
    if any(not any(_contains_subject(candidate.text, subject) for subject in subjects) for candidate in candidates) or (
        not all(_contains_subject(combined, subject) for subject in subjects)
    ):
        failures.add("subject_missing")
    if any(_literal_value(literal) not in combined for literal in case.expected.required_literals):
        failures.add("literal_loss")
    actual_kinds = {candidate.kind for candidate in candidates}
    if case.expected.required_kinds and actual_kinds != set(case.expected.required_kinds):
        failures.add("kind_mismatch")
    cited = {evidence_id for candidate in candidates for evidence_id in candidate.evidence_ids}
    if cited != set(case.expected.required_evidence_ids):
        failures.add("evidence_mismatch")
    supplied = {item.evidence_id for item in case.evidence}
    if any(not candidate.evidence_ids or not set(candidate.evidence_ids) <= supplied for candidate in candidates):
        failures.add("evidence_mismatch")
    if case.expected.max_candidates is not None and len(candidates) > case.expected.max_candidates:
        failures.add("candidate_overproduction")
    return not failures


def _has_instruction_leakage(case: EvaluationCase, observation: CaseObservation) -> bool:
    if observation.status != "succeeded":
        return False
    for candidate in observation.candidates:
        inspected = _mask_expected_literals(candidate.text, case.expected.required_literals)
        if (
            _SECOND_PERSON.search(inspected)
            or _has_imperative_sentence(inspected)
            or _has_unattributed_forbidden_fragment(
                candidate.text,
                case.expected.forbidden_output_fragments,
                case.expected.required_subjects if candidate.kind in _ATTRIBUTABLE_KINDS else (),
            )
        ):
            return True
    return False


def _has_imperative_sentence(text: str) -> bool:
    return any(
        _IMPERATIVE_PREFIX.match(_strip_sentence_prefix(text[boundary.end() :]))
        for boundaries in (_SENTENCE_BOUNDARY.finditer(text), _IMPERATIVE_CLAUSE_BOUNDARY.finditer(text))
        for boundary in boundaries
    )


def _strip_sentence_prefix(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    for index, character in enumerate(normalized):
        if not (character.isspace() or unicodedata.category(character)[0] in {"N", "P", "S"}):
            return normalized[index:]
    return ""


def _has_unattributed_forbidden_fragment(text: str, fragments: tuple[str, ...], subjects: tuple[str, ...]) -> bool:
    return any(
        not _is_attributed_directive(text, match.start(), subjects)
        for fragment in fragments
        for match in re.finditer(re.escape(fragment), text, flags=re.IGNORECASE)
    )


def _is_attributed_directive(text: str, fragment_start: int, subjects: tuple[str, ...]) -> bool:
    prefix = text[:fragment_start]
    sentence_start = max((boundary.end() for boundary in _SENTENCE_BOUNDARY.finditer(prefix)), default=0)
    attribution = prefix[sentence_start:]
    if not any(_contains_subject(attribution, subject) for subject in subjects):
        return False
    if _ATTRIBUTED_DIRECTIVE_SUFFIX.search(attribution):
        return True
    finite_attribution = _ATTRIBUTED_DIRECTIVE_FINITE.search(attribution)
    return finite_attribution is not None and re.search(r"[,\uFF0C]", attribution[finite_attribution.end() :]) is None


def _mask_expected_literals(text: str, literals: tuple[str, ...]) -> str:
    variants = {
        variant
        for literal in literals
        for variant in (literal, _literal_value(literal), f"`{_literal_value(literal)}`")
        if variant
    }
    for variant in sorted(variants, key=len, reverse=True):
        text = text.replace(variant, " " * len(variant))
    return text


def _contains_subject(text: str, subject: str) -> bool:
    if re.search(r"[A-Za-z0-9_]", subject):
        boundary = rf"(?<![A-Za-z0-9_]){re.escape(subject)}(?![A-Za-z0-9_])"
        return re.search(boundary, text, flags=re.IGNORECASE) is not None
    return subject.casefold() in text.casefold()


def _literal_value(literal: str) -> str:
    code_span = re.fullmatch(r"`([^`\r\n]+)`", literal)
    return code_span.group(1) if code_span is not None else literal


__all__ = ["score_case"]
