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

"""Strict LoCoMo loading and Source rendering without answer leakage."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SESSION_KEY = re.compile(r"^session_(\d+)$")
_DIALOGUE_ID = re.compile(r"^D(\d+):(\d+)$")


@dataclass(frozen=True, slots=True)
class LoCoMoTurn:
    """One dialogue turn with the dataset's stable evidence identity."""

    dialogue_id: str
    speaker: str
    text: str
    image_caption: str | None = None


@dataclass(frozen=True, slots=True)
class LoCoMoSession:
    """One timestamped conversation session."""

    session_id: str
    session_number: int
    date_time: str
    turns: tuple[LoCoMoTurn, ...]


@dataclass(frozen=True, slots=True)
class LoCoMoQuestion:
    """One LoCoMo QA item and its exact evidence pointers."""

    question_id: str
    sample_id: str
    question: str
    answer: str
    category: int
    evidence_raw: tuple[str, ...]
    evidence: tuple[str, ...]
    adversarial_answer: str | None = None

    @property
    def evidence_sessions(self) -> tuple[str, ...]:
        """Return unique evidence session IDs in dataset order."""

        return tuple(dict.fromkeys(reference.split(":", maxsplit=1)[0] for reference in self.evidence))


@dataclass(frozen=True, slots=True)
class LoCoMoConversation:
    """One two-speaker LoCoMo sample."""

    sample_id: str
    speaker_a: str
    speaker_b: str
    sessions: tuple[LoCoMoSession, ...]
    questions: tuple[LoCoMoQuestion, ...]


@dataclass(frozen=True, slots=True)
class LoCoMoDataset:
    """Validated benchmark input plus its content fingerprint."""

    path: Path
    sha256: str
    conversations: tuple[LoCoMoConversation, ...]

    @property
    def sessions(self) -> tuple[LoCoMoSession, ...]:
        return tuple(session for conversation in self.conversations for session in conversation.sessions)

    @property
    def questions(self) -> tuple[LoCoMoQuestion, ...]:
        return tuple(question for conversation in self.conversations for question in conversation.questions)

    def selected_questions(
        self,
        *,
        categories: tuple[int, ...] = (1, 2, 3, 4),
        conversation_limit: int | None = None,
        question_limit: int | None = None,
    ) -> tuple[LoCoMoQuestion, ...]:
        """Select questions deterministically while preserving source order."""

        selected_conversations = (
            self.conversations if conversation_limit is None else self.conversations[:conversation_limit]
        )
        selected = tuple(
            question
            for conversation in selected_conversations
            for question in conversation.questions
            if question.category in categories
        )
        return selected if question_limit is None else selected[:question_limit]


def load_locomo(path: Path) -> LoCoMoDataset:
    """Load the canonical LoCoMo JSON and reject malformed evidence identities."""

    payload = path.read_bytes()
    raw = json.loads(payload)
    if not isinstance(raw, list) or not raw:
        raise ValueError("LoCoMo dataset must be a non-empty JSON array")  # noqa: TRY003
    conversations = tuple(_load_conversation(item, index) for index, item in enumerate(raw))
    sample_ids = tuple(conversation.sample_id for conversation in conversations)
    if len(set(sample_ids)) != len(sample_ids):
        raise ValueError("LoCoMo sample IDs must be unique")  # noqa: TRY003
    return LoCoMoDataset(
        path=path.resolve(),
        sha256=hashlib.sha256(payload).hexdigest(),
        conversations=conversations,
    )


def render_session(conversation: LoCoMoConversation, session: LoCoMoSession) -> str:
    """Render only timestamped dialogue evidence for Memory extraction."""

    lines = [
        f"LoCoMo conversation {conversation.sample_id}, session {session.session_id}",
        f"Date and time: {session.date_time}",
        f"Speakers: {conversation.speaker_a} and {conversation.speaker_b}",
        "Dialogue:",
    ]
    for turn in session.turns:
        lines.append(f"[{turn.dialogue_id}] {turn.speaker}: {turn.text}")
        if turn.image_caption:
            lines.append(f"[{turn.dialogue_id}] Image caption: {turn.image_caption}")
    return "\n".join(lines)


def _load_conversation(raw: Any, index: int) -> LoCoMoConversation:
    if not isinstance(raw, dict):
        raise TypeError(f"LoCoMo item {index} must be an object")  # noqa: TRY003
    sample_id = _text(raw.get("sample_id"), f"item {index} sample_id")
    conversation = raw.get("conversation")
    if not isinstance(conversation, dict):
        raise TypeError(f"LoCoMo item {sample_id} has no conversation object")  # noqa: TRY003
    speaker_a = _text(conversation.get("speaker_a"), f"{sample_id} speaker_a")
    speaker_b = _text(conversation.get("speaker_b"), f"{sample_id} speaker_b")
    session_keys = sorted(
        (int(match.group(1)), key) for key in conversation if (match := _SESSION_KEY.fullmatch(key)) is not None
    )
    sessions = tuple(
        _load_session(conversation, key, session_number, sample_id) for session_number, key in session_keys
    )
    if not sessions:
        raise ValueError(f"LoCoMo item {sample_id} has no sessions")  # noqa: TRY003
    dialogue_ids = {turn.dialogue_id for session in sessions for turn in session.turns}
    questions_raw = raw.get("qa")
    if not isinstance(questions_raw, list):
        raise TypeError(f"LoCoMo item {sample_id} has no qa array")  # noqa: TRY003
    questions = tuple(
        _load_question(question, sample_id, question_index, dialogue_ids)
        for question_index, question in enumerate(questions_raw)
    )
    return LoCoMoConversation(
        sample_id=sample_id,
        speaker_a=speaker_a,
        speaker_b=speaker_b,
        sessions=sessions,
        questions=questions,
    )


def _load_session(raw: dict[str, Any], key: str, number: int, sample_id: str) -> LoCoMoSession:
    turns_raw = raw[key]
    if not isinstance(turns_raw, list) or not turns_raw:
        raise ValueError(f"LoCoMo {sample_id} {key} must be a non-empty array")  # noqa: TRY003
    session_id = f"D{number}"
    turns: list[LoCoMoTurn] = []
    for turn_index, item in enumerate(turns_raw):
        if not isinstance(item, dict):
            raise TypeError(f"LoCoMo {sample_id} {key} turn {turn_index} must be an object")  # noqa: TRY003
        dialogue_id = _text(item.get("dia_id"), f"{sample_id} {key} dialogue ID")
        match = _DIALOGUE_ID.fullmatch(dialogue_id)
        if match is None or int(match.group(1)) != number:
            raise ValueError(f"LoCoMo dialogue ID {dialogue_id} does not belong to {session_id}")  # noqa: TRY003
        caption = item.get("blip_caption")
        turns.append(
            LoCoMoTurn(
                dialogue_id=dialogue_id,
                speaker=_text(item.get("speaker"), f"{dialogue_id} speaker"),
                text=_text(item.get("text"), f"{dialogue_id} text"),
                image_caption=None if caption is None else _text(caption, f"{dialogue_id} image caption"),
            )
        )
    date_time = _text(raw.get(f"{key}_date_time"), f"{sample_id} {key} date_time")
    return LoCoMoSession(session_id=session_id, session_number=number, date_time=date_time, turns=tuple(turns))


def _load_question(raw: Any, sample_id: str, index: int, dialogue_ids: set[str]) -> LoCoMoQuestion:
    if not isinstance(raw, dict):
        raise TypeError(f"LoCoMo {sample_id} question {index} must be an object")  # noqa: TRY003
    evidence_value = raw.get("evidence", [])
    if not isinstance(evidence_value, list):
        raise TypeError(f"LoCoMo {sample_id} question {index} evidence must be an array")  # noqa: TRY003
    evidence_raw = tuple(_text(value, f"{sample_id} question {index} evidence") for value in evidence_value)
    evidence = _normalize_evidence(evidence_raw)
    known_sessions = {value.split(":", maxsplit=1)[0] for value in dialogue_ids}
    unknown_sessions = tuple(value for value in evidence if value.split(":", maxsplit=1)[0] not in known_sessions)
    if unknown_sessions:
        raise ValueError(  # noqa: TRY003
            f"LoCoMo {sample_id} question {index} cites unknown evidence sessions: {unknown_sessions}"
        )
    category = raw.get("category")
    if not isinstance(category, int) or category not in {1, 2, 3, 4, 5}:
        raise ValueError(f"LoCoMo {sample_id} question {index} has an invalid category")  # noqa: TRY003
    adversarial = raw.get("adversarial_answer")
    return LoCoMoQuestion(
        question_id=f"{sample_id}:q{index + 1:03d}",
        sample_id=sample_id,
        question=_text(raw.get("question"), f"{sample_id} question {index}"),
        answer=str(raw.get("answer", "")).strip(),
        category=category,
        evidence_raw=evidence_raw,
        evidence=evidence,
        adversarial_answer=None if adversarial is None else str(adversarial).strip(),
    )


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"LoCoMo {field} must be non-empty text")  # noqa: TRY003
    return value.strip()


def _normalize_evidence(values: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize known LoCoMo annotation quirks while retaining ``evidence_raw``."""

    normalized: list[str] = []
    for value in values:
        typo = re.fullmatch(r"D:(\d+):(\d+)", value)
        if typo is not None:
            normalized.append(f"D{int(typo.group(1))}:{int(typo.group(2))}")
            continue
        normalized.extend(f"D{int(session)}:{int(turn)}" for session, turn in re.findall(r"D(\d+):(\d+)", value))
    return tuple(dict.fromkeys(normalized))


__all__ = [
    "LoCoMoConversation",
    "LoCoMoDataset",
    "LoCoMoQuestion",
    "LoCoMoSession",
    "LoCoMoTurn",
    "load_locomo",
    "render_session",
]
