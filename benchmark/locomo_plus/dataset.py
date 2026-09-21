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

"""Pinned LoCoMo-Plus data and reproducible, timestamp-preserving histories."""

from __future__ import annotations

import json
import random
import re
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from itertools import zip_longest
from pathlib import Path
from typing import Any, Literal
from urllib.request import urlopen

from pydantic import TypeAdapter

from benchmark.locomo.dataset import LoCoMoConversation, LoCoMoSession, LoCoMoTurn, load_locomo

DEFAULT_DATA_DIR = Path(".cache/locomo_plus")
DEFAULT_SMOKE_PATH = Path(__file__).with_name("dataset") / "locomo_plus_smoke10.json"
CATEGORY_NAMES = {1: "multi-hop", 2: "temporal", 3: "common-sense", 4: "single-hop", 5: "adversarial", 6: "Cognitive"}
RELATION_TYPES = ("causal", "state", "goal", "value")
# Keep pinned provenance beside the loader so it has no separate metadata-file dependency.
_MANIFEST: dict[str, Any] = {
    "repository": "https://github.com/xjtuleeyf/Locomo-Plus",
    "commit": "059f4e3d38f7f1f96765e8e2cb7de3097551bffb",
    "files": ["locomo10.json", "locomo_plus.json"],
    "license": "No dataset or code license is present at the pinned upstream commit.",
    "adapter_version": "powercontext-locomo-plus-v1",
    "conversation_assignment": "random.Random(seed).randrange, once for each original cognitive index before exclusions",
    "time_policy": (
        "Upstream: query is seven days after the final session; week=7, month=30, year=365 days. "
        "Unrecognized gaps use zero days and are flagged. Dates have no declared timezone."
    ),
    "malformed_cue_policy": (
        "Exclude records without exactly two nonempty lines, A: followed by B:, and report original indices. No repairs."
    ),
    "smoke_case_ids": ["cognitive:0000", "cognitive:0101", "cognitive:0188", "cognitive:0301"],
    "smoke_dataset": {
        "file": "locomo_plus_smoke10.json",
        "seed": 42,
        "count": 10,
    },
}
SMOKE_CASE_IDS = tuple(_MANIFEST["smoke_case_ids"])
_NUMBER_WORDS = dict(
    zip(
        ["one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"],
        range(1, 13),
        strict=True,
    )
)
_GAP = re.compile(
    r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|a|an)\b\s*"
    r"(week|weeks|month|months|year|years)\b"
)


@dataclass(frozen=True, slots=True)
class LoCoMoPlusCase:
    """A question and separately held evidence; renderers never consume gold metadata."""

    case_id: str
    sample_id: str
    category: int
    relation_type: str | None
    question: str
    answer: str
    evidence: tuple[str, ...]
    evidence_text: str
    conversation: LoCoMoConversation
    cue_session_id: str | None
    query_time: str | None
    metadata: dict[str, Any]

    @property
    def sessions(self) -> tuple[LoCoMoSession, ...]:
        return self.conversation.sessions


@dataclass(frozen=True, slots=True)
class LoCoMoPlusDataset:
    """Validated inputs, explicit exclusions and JSON-serializable provenance."""

    cases: tuple[LoCoMoPlusCase, ...]
    exclusions: tuple[dict[str, Any], ...]
    manifest: dict[str, Any]

    def selected_cases(
        self,
        *,
        mode: Literal["smoke", "full"] = "smoke",
        categories: tuple[int, ...] = (1, 2, 3, 4, 5, 6),
        limit: int | None = None,
    ) -> tuple[LoCoMoPlusCase, ...]:
        """Select the fixed smoke anchors, optionally extending them by cognitive relation."""

        if mode not in {"smoke", "full"}:
            raise ValueError(f"Unknown LoCoMo-Plus selection mode: {mode}")  # noqa: TRY003
        if limit is not None and limit < 1:
            raise ValueError("LoCoMo-Plus limit must be positive")  # noqa: TRY003
        if not categories or set(categories) - CATEGORY_NAMES.keys():
            raise ValueError("LoCoMo-Plus categories must be selected from 1 through 6")  # noqa: TRY003
        candidates = self.cases
        if mode == "smoke":
            by_id = {case.case_id: case for case in candidates}
            missing = set(SMOKE_CASE_IDS) - by_id.keys()
            if missing:
                raise ValueError(f"Dataset is missing fixed smoke cases: {sorted(missing)}")  # noqa: TRY003
            candidates = tuple(by_id[case_id] for case_id in SMOKE_CASE_IDS)
            if limit is not None and limit > len(SMOKE_CASE_IDS):
                groups = (
                    tuple(
                        case
                        for case in self.cases
                        if case.relation_type == relation and case.case_id not in SMOKE_CASE_IDS
                    )
                    for relation in RELATION_TYPES
                )
                candidates += tuple(case for group in zip_longest(*groups) for case in group if case is not None)
        selected = tuple(case for case in candidates if case.category in categories)
        if mode == "smoke" and limit is not None and limit > len(selected):
            raise ValueError(f"Smoke requested {limit} cases but only {len(selected)} are available")  # noqa: TRY003
        return selected if limit is None else selected[:limit]


def ensure_dataset(data_dir: Path = DEFAULT_DATA_DIR) -> None:
    """Download missing files from the fixed upstream revision, preserving existing local data."""

    data_dir.mkdir(parents=True, exist_ok=True)
    for name in _MANIFEST["files"]:
        path = data_dir / name
        if path.exists():
            continue
        url = f"https://raw.githubusercontent.com/xjtuleeyf/Locomo-Plus/{_MANIFEST['commit']}/data/{name}"
        with urlopen(url, timeout=60) as response:  # noqa: S310 - fixed HTTPS upstream URL
            payload = response.read()
        path.write_bytes(payload)


def load_locomo_plus(
    data_dir: Path = DEFAULT_DATA_DIR,
    *,
    seed: int = 42,
) -> LoCoMoPlusDataset:
    """Load all five factual categories plus valid cognitive records without downloading."""

    locomo = load_locomo(data_dir / "locomo10.json")
    raw = json.loads((data_dir / "locomo_plus.json").read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("LoCoMo-Plus data must be a non-empty JSON array")  # noqa: TRY003
    cases = [case for conversation in locomo.conversations for case in _factual_cases(conversation)]
    exclusions: list[dict[str, Any]] = []
    relation_counts: Counter[str] = Counter()
    randomizer = random.Random(seed)  # noqa: S311 - reproducible benchmark selection, not cryptography
    for index, raw_item in enumerate(raw):
        conversation = locomo.conversations[randomizer.randrange(len(locomo.conversations))]
        item = _validate_cognitive_item(raw_item, index)
        relation_counts[item["relation_type"]] += 1
        cue = _cue_turns(item["cue_dialogue"])
        if cue is None:
            exclusions.append({
                "case_id": f"cognitive:{index:04d}",
                "source_index": index,
                "relation_type": item["relation_type"],
                "reason": "cue_dialogue must contain exactly two nonempty lines: A: then B:",
            })
            continue
        cases.append(_cognitive_case(item, index, conversation, cue))
    cognitive = [case for case in cases if case.category == 6]
    manifest = {
        **_MANIFEST,
        "data_directory": str(data_dir.resolve()),
        "seed": seed,
        "raw_cognitive_count": len(raw),
        "raw_relation_counts": dict(relation_counts),
        "eligible_count": len(cases),
        "category_counts": dict(Counter(CATEGORY_NAMES[case.category] for case in cases)),
        "eligible_relation_counts": dict(Counter(case.relation_type for case in cognitive)),
        "excluded_count": len(exclusions),
        "exclusions": exclusions,
        "unparsed_time_gap_case_ids": [case.case_id for case in cognitive if not case.metadata["time_gap_parsed"]],
    }
    return LoCoMoPlusDataset(cases=tuple(cases), exclusions=tuple(exclusions), manifest=manifest)


def load_smoke_dataset(path: Path = DEFAULT_SMOKE_PATH, *, seed: int = 42) -> LoCoMoPlusDataset:
    """Load the pinned ten-case snapshot, including complete histories, without downloading."""

    expected = _MANIFEST["smoke_dataset"]
    if seed != expected["seed"]:
        raise ValueError(  # noqa: TRY003
            f"Bundled smoke histories use seed {expected['seed']}; use --data-directory for a different seed"
        )
    payload = path.read_bytes()
    dataset = TypeAdapter(LoCoMoPlusDataset).validate_json(payload)
    return replace(
        dataset,
        manifest={
            **dataset.manifest,
            "data_directory": str(path.parent.resolve()),
            "smoke_dataset": {**expected, "path": str(path.resolve())},
        },
    )


def render_case_session(case: LoCoMoPlusCase, session: LoCoMoSession) -> str:
    """Render dated dialogue alone, with no question, labels, rubric or answer."""

    lines = [
        f"Conversation {case.conversation.sample_id}, session {session.session_id}",
        f"Date and time: {session.date_time}",
        f"Speakers: {case.conversation.speaker_a} and {case.conversation.speaker_b}",
        "Dialogue:",
    ]
    for turn in session.turns:
        lines.append(f"[{turn.dialogue_id}] {turn.speaker}: {turn.text}")
        if turn.image_caption:
            lines.append(f"[{turn.dialogue_id}] Image caption: {turn.image_caption}")
    return "\n".join(lines)


def _factual_cases(conversation: LoCoMoConversation) -> tuple[LoCoMoPlusCase, ...]:
    turns = {turn.dialogue_id: turn for session in conversation.sessions for turn in session.turns}
    return tuple(
        LoCoMoPlusCase(
            case_id=f"factual:{question.question_id}",
            sample_id=conversation.sample_id,
            category=question.category,
            relation_type=None,
            question=question.question,
            answer=question.answer,
            evidence=question.evidence,
            evidence_text="\n".join(
                f"[{reference}] {turns[reference].speaker}: {turns[reference].text}"
                for reference in question.evidence
                if reference in turns
            ),
            conversation=conversation,
            cue_session_id=None,
            query_time=None,
            metadata={
                "question_id": question.question_id,
                "evidence_raw": question.evidence_raw,
                "adversarial_answer": question.adversarial_answer,
                "unresolved_evidence": tuple(reference for reference in question.evidence if reference not in turns),
            },
        )
        for question in conversation.questions
    )


def _cognitive_case(
    item: dict[str, Any],
    index: int,
    conversation: LoCoMoConversation,
    cue: tuple[str, str],
) -> LoCoMoPlusCase:
    session_dates = [_session_date(session.date_time) for session in conversation.sessions]
    query_date = max(session_dates) + timedelta(days=7)
    gap_days, gap_parsed = _time_gap(item["time_gap"])
    cue_date = query_date - timedelta(days=gap_days)
    number = max(session.session_number for session in conversation.sessions) + 1
    cue_session_id = f"D{number}"
    cue_session = LoCoMoSession(
        session_id=cue_session_id,
        session_number=number,
        date_time=cue_date.strftime("%Y-%m-%d %H:%M"),
        turns=(
            LoCoMoTurn(f"{cue_session_id}:1", conversation.speaker_a, cue[0]),
            LoCoMoTurn(f"{cue_session_id}:2", conversation.speaker_b, cue[1]),
        ),
    )
    events = [*zip(session_dates, conversation.sessions, strict=True), (cue_date, cue_session)]
    events.sort(key=lambda event: event[0])
    trigger = _text(item["trigger_query"], f"cognitive {index} trigger_query")
    if not trigger.startswith("A:") or len(trigger.splitlines()) != 1 or not trigger[2:].strip():
        raise ValueError(f"LoCoMo-Plus cognitive {index} trigger_query must be one A: turn")  # noqa: TRY003
    return LoCoMoPlusCase(
        case_id=f"cognitive:{index:04d}",
        sample_id=f"{conversation.sample_id}:cognitive:{index:04d}",
        category=6,
        relation_type=item["relation_type"],
        question=f"{conversation.speaker_a}: {trigger[2:].strip()}",
        answer="",
        evidence=tuple(turn.dialogue_id for turn in cue_session.turns),
        evidence_text="\n".join(f"[{turn.dialogue_id}] {turn.speaker}: {turn.text}" for turn in cue_session.turns),
        conversation=replace(conversation, sessions=tuple(session for _, session in events), questions=()),
        cue_session_id=cue_session_id,
        query_time=query_date.strftime("%Y-%m-%d %H:%M"),
        metadata={
            "source_index": index,
            "host_sample_id": conversation.sample_id,
            "time_gap": item["time_gap"],
            "time_gap_days": gap_days,
            "time_gap_parsed": gap_parsed,
            "cue_time": cue_session.date_time,
            "upstream": item,
        },
    )


def _validate_cognitive_item(item: Any, index: int) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise TypeError(f"LoCoMo-Plus cognitive {index} must be an object")  # noqa: TRY003
    for field in ("relation_type", "cue_dialogue", "trigger_query", "time_gap"):
        _text(item.get(field), f"cognitive {index} {field}")
    if item["relation_type"] not in RELATION_TYPES:
        raise ValueError(f"Unknown LoCoMo-Plus relation_type at cognitive {index}: {item['relation_type']}")  # noqa: TRY003
    return item


def _cue_turns(value: str) -> tuple[str, str] | None:
    lines = value.strip().splitlines()
    if len(lines) != 2 or not lines[0].startswith("A:") or not lines[1].startswith("B:"):
        return None
    first, second = lines[0][2:].strip(), lines[1][2:].strip()
    return (first, second) if first and second else None


def _time_gap(value: str) -> tuple[int, bool]:
    match = _GAP.search(value.lower().strip())
    if match is None:
        return 0, False
    amount, unit = match.groups()
    count = int(amount) if amount.isdigit() else _NUMBER_WORDS.get(amount, 1)
    days = 7 if unit.startswith("week") else 30 if unit.startswith("month") else 365
    return count * days, True


def _session_date(value: str) -> datetime:
    # Upstream provides local wall-clock values without a timezone; do not invent one.
    return datetime.strptime(value, "%I:%M %p on %d %B, %Y")  # noqa: DTZ007


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"LoCoMo-Plus {field} must be nonempty text")  # noqa: TRY003
    return value.strip()


__all__ = [
    "CATEGORY_NAMES",
    "DEFAULT_DATA_DIR",
    "DEFAULT_SMOKE_PATH",
    "RELATION_TYPES",
    "SMOKE_CASE_IDS",
    "LoCoMoPlusCase",
    "LoCoMoPlusDataset",
    "ensure_dataset",
    "load_locomo_plus",
    "load_smoke_dataset",
    "render_case_session",
]
