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

"""Fail-closed catalog for a pinned LongMemEval-V2 dataset layout."""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal, TypeAlias

from powercontext_eval.errors import PowerContextEvalError

UPSTREAM_REPOSITORY = "https://github.com/xiaowu0162/LongMemEval-V2"
UPSTREAM_HARNESS_COMMIT = "2cc8c540bdb87fe6761629b585e727e1c4704520"
SMOKE_MANIFEST_SCHEMA = "powercontext.longmemeval-v2-smoke.v1"
DATASET_LOCK_SCHEMA = "powercontext.longmemeval-v2-dataset-lock.v1"
RUN_INPUT_MANIFEST_SCHEMA = "powercontext.longmemeval-v2-run-input.v1"

Tier: TypeAlias = Literal["small", "medium"]
Ability: TypeAlias = Literal[
    "static_state",
    "dynamic_state",
    "workflow_knowledge",
    "environment_gotchas",
    "premise_awareness",
]

_ABILITIES: frozenset[Ability] = frozenset(
    {
        "static_state",
        "dynamic_state",
        "workflow_knowledge",
        "environment_gotchas",
        "premise_awareness",
    }
)
_QUESTION_TYPE_ABILITIES: Mapping[str, Ability] = MappingProxyType(
    {
        "static-environment": "static_state",
        "static-environment-abs": "premise_awareness",
        "dynamic-environment": "dynamic_state",
        "dynamic-environment-abs": "premise_awareness",
        "procedure": "workflow_knowledge",
        "procedure-abs": "premise_awareness",
        "errors-gotchas": "environment_gotchas",
    }
)


class LongMemEvalV2CatalogError(PowerContextEvalError):
    """The pinned LongMemEval-V2 inputs cannot be trusted."""


@dataclass(frozen=True)
class Question:
    """The query-visible fields required to validate one upstream question."""

    question_id: str
    domain: Literal["web", "enterprise"]
    question_type: str

    @property
    def ability(self) -> Ability:
        return _QUESTION_TYPE_ABILITIES[self.question_type]


@dataclass(frozen=True)
class SmokeCase:
    """One fixed question selected for the bounded smoke workload."""

    question_id: str
    ability: Ability


@dataclass(frozen=True)
class DatasetLock:
    """The immutable identity and file digests for one upstream data tier."""

    tier: Tier
    dataset_revision: str
    file_digests: Mapping[str, str]

    def as_json(self) -> dict[str, object]:
        return {
            "schema": DATASET_LOCK_SCHEMA,
            "upstream": {
                "harness_commit": UPSTREAM_HARNESS_COMMIT,
                "repository": UPSTREAM_REPOSITORY,
            },
            "dataset_revision": self.dataset_revision,
            "tier": self.tier,
            "files": dict(self.file_digests),
        }


@dataclass(frozen=True)
class SmokeSelection:
    """A validated fixed subset for one LongMemEval-V2 tier."""

    tier: Tier
    cases: tuple[SmokeCase, ...]

    def as_json(self) -> dict[str, object]:
        return {
            "schema": SMOKE_MANIFEST_SCHEMA,
            "tier": self.tier,
            "cases": [{"question_id": case.question_id, "ability": case.ability} for case in self.cases],
        }


@dataclass(frozen=True)
class LongMemEvalV2Catalog:
    """Validated LongMemEval-V2 input identities for one upstream data root."""

    data_root: Path
    tier: Tier
    input_digests: Mapping[str, str]
    questions: Mapping[str, Question]
    source_question_ids: tuple[str, ...]

    @classmethod
    def load(
        cls,
        data_root: Path,
        *,
        tier: Tier,
        expected_digests: Mapping[str, str] | None = None,
    ) -> LongMemEvalV2Catalog:
        """Load and validate the three upstream files required by one tier."""

        if tier not in {"small", "medium"}:
            raise LongMemEvalV2CatalogError(f"Unsupported LongMemEval-V2 tier: {tier}")
        root = data_root.resolve()
        paths = {
            "questions.jsonl": root / "questions.jsonl",
            "trajectories.jsonl": root / "trajectories.jsonl",
            f"haystacks/lme_v2_{tier}.json": root / "haystacks" / f"lme_v2_{tier}.json",
        }
        digests = MappingProxyType({name: _file_digest(path, name) for name, path in paths.items()})
        _validate_expected_digests(digests, expected_digests)

        questions, source_question_ids = _questions(paths["questions.jsonl"])
        trajectories = _trajectories(paths["trajectories.jsonl"])
        haystack = _haystack(paths[f"haystacks/lme_v2_{tier}.json"])
        _validate_haystack(questions, trajectories, haystack)
        return cls(
            data_root=root,
            tier=tier,
            input_digests=digests,
            questions=MappingProxyType(questions),
            source_question_ids=source_question_ids,
        )

    def select_smoke(self, cases: Sequence[SmokeCase]) -> SmokeSelection:
        """Validate a fixed smoke subset without deriving one from benchmark metadata."""

        if not cases:
            raise LongMemEvalV2CatalogError("Smoke subset must contain at least one question")
        selected: list[SmokeCase] = []
        seen: set[str] = set()
        source_positions = {question_id: index for index, question_id in enumerate(self.source_question_ids)}
        for case in cases:
            if case.question_id in seen:
                raise LongMemEvalV2CatalogError(f"Smoke subset contains duplicate question id: {case.question_id}")
            seen.add(case.question_id)
            question = self.questions.get(case.question_id)
            if question is None:
                raise LongMemEvalV2CatalogError(f"Smoke subset references an unknown question id: {case.question_id}")
            if question.ability != case.ability:
                raise LongMemEvalV2CatalogError(
                    f"Smoke subset ability mismatch for {case.question_id}: "
                    f"expected {question.ability}, got {case.ability}"
                )
            selected.append(case)
        if [source_positions[case.question_id] for case in selected] != sorted(
            source_positions[case.question_id] for case in selected
        ):
            raise LongMemEvalV2CatalogError("Smoke subset question order must match the upstream source order")
        selected_abilities = {case.ability for case in selected}
        missing = sorted(_ABILITIES - selected_abilities)
        if missing:
            raise LongMemEvalV2CatalogError(f"Smoke subset is missing published abilities: {', '.join(missing)}")
        return SmokeSelection(tier=self.tier, cases=tuple(selected))


def load_smoke_manifest(path: Path) -> SmokeSelection:
    """Load one exact smoke-subset declaration without selecting questions dynamically."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LongMemEvalV2CatalogError(f"Cannot read LongMemEval-V2 smoke manifest: {path}") from error
    if not isinstance(value, dict) or set(value) != {"schema", "tier", "cases"}:
        raise LongMemEvalV2CatalogError("Smoke manifest must contain only schema, tier, and cases")
    if value["schema"] != SMOKE_MANIFEST_SCHEMA:
        raise LongMemEvalV2CatalogError("Smoke manifest schema is unsupported")
    tier = value["tier"]
    if tier not in {"small", "medium"}:
        raise LongMemEvalV2CatalogError("Smoke manifest tier must be small or medium")
    raw_cases = value["cases"]
    if not isinstance(raw_cases, list):
        raise LongMemEvalV2CatalogError("Smoke manifest cases must be an array")
    cases: list[SmokeCase] = []
    for index, raw_case in enumerate(raw_cases):
        if not isinstance(raw_case, dict) or set(raw_case) != {"question_id", "ability"}:
            raise LongMemEvalV2CatalogError(f"Smoke manifest case {index} has an invalid shape")
        question_id = raw_case["question_id"]
        ability = raw_case["ability"]
        if not isinstance(question_id, str) or not question_id.strip():
            raise LongMemEvalV2CatalogError(f"Smoke manifest case {index} has an invalid question id")
        if ability not in _ABILITIES:
            raise LongMemEvalV2CatalogError(f"Smoke manifest case {index} has an invalid ability")
        cases.append(SmokeCase(question_id=question_id, ability=ability))
    return SmokeSelection(tier=tier, cases=tuple(cases))


def load_dataset_lock(path: Path) -> DatasetLock:
    """Load a fixed input identity before reading any benchmark data."""

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LongMemEvalV2CatalogError(f"Cannot read LongMemEval-V2 dataset lock: {path}") from error
    if not isinstance(value, dict) or set(value) != {"schema", "upstream", "dataset_revision", "tier", "files"}:
        raise LongMemEvalV2CatalogError(
            "Dataset lock must contain only schema, upstream, dataset_revision, tier, and files"
        )
    if value["schema"] != DATASET_LOCK_SCHEMA:
        raise LongMemEvalV2CatalogError("Dataset lock schema is unsupported")
    upstream = value["upstream"]
    if not isinstance(upstream, dict) or set(upstream) != {"repository", "harness_commit"}:
        raise LongMemEvalV2CatalogError("Dataset lock upstream identity is invalid")
    if upstream["repository"] != UPSTREAM_REPOSITORY or upstream["harness_commit"] != UPSTREAM_HARNESS_COMMIT:
        raise LongMemEvalV2CatalogError("Dataset lock does not match the pinned LongMemEval-V2 harness")
    dataset_revision = value["dataset_revision"]
    if not isinstance(dataset_revision, str) or not dataset_revision.strip():
        raise LongMemEvalV2CatalogError("Dataset lock dataset revision must be non-empty")
    tier = value["tier"]
    if tier not in {"small", "medium"}:
        raise LongMemEvalV2CatalogError("Dataset lock tier must be small or medium")
    files = value["files"]
    expected_names = {
        "questions.jsonl",
        "trajectories.jsonl",
        f"haystacks/lme_v2_{tier}.json",
    }
    if not isinstance(files, dict) or set(files) != expected_names:
        raise LongMemEvalV2CatalogError("Dataset lock must provide digests for exactly the required files")
    if any(not isinstance(digest, str) or len(digest) != 64 or not _is_lower_hex(digest) for digest in files.values()):
        raise LongMemEvalV2CatalogError("Dataset lock file digests must be lowercase SHA-256 values")
    return DatasetLock(tier=tier, dataset_revision=dataset_revision, file_digests=MappingProxyType(dict(files)))


def validate_harness_checkout(harness_root: Path) -> None:
    """Require the configured upstream checkout to be exactly the pinned harness commit."""

    harness = harness_root.resolve()
    if not (harness / "evaluation" / "harness.py").is_file():
        raise LongMemEvalV2CatalogError(f"LongMemEval-V2 harness is missing evaluation/harness.py: {harness}")
    try:
        completed = subprocess.run(
            ["git", "-C", str(harness), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise LongMemEvalV2CatalogError(f"Cannot inspect LongMemEval-V2 harness checkout: {harness}") from error
    revision = completed.stdout.strip()
    if completed.returncode != 0 or revision != UPSTREAM_HARNESS_COMMIT:
        raise LongMemEvalV2CatalogError(
            f"LongMemEval-V2 harness checkout must be {UPSTREAM_HARNESS_COMMIT}, got {revision or 'unknown'}"
        )


def _file_digest(path: Path, label: str) -> str:
    """Hash one input without materializing large trajectory files in memory."""

    hasher = hashlib.sha256()
    bytes_read = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                hasher.update(chunk)
                bytes_read += len(chunk)
    except OSError as error:
        raise LongMemEvalV2CatalogError(f"Missing LongMemEval-V2 input {label}: {path}") from error
    if bytes_read == 0:
        raise LongMemEvalV2CatalogError(f"LongMemEval-V2 input {label} is blank")
    return hasher.hexdigest()


def _validate_expected_digests(actual: Mapping[str, str], expected: Mapping[str, str] | None) -> None:
    if expected is None:
        return
    if set(expected) != set(actual):
        raise LongMemEvalV2CatalogError("Expected input digests must name exactly the required files")
    for name, digest in actual.items():
        if expected[name] != digest:
            raise LongMemEvalV2CatalogError(f"LongMemEval-V2 SHA-256 mismatch for {name}")


def _questions(path: Path) -> tuple[dict[str, Question], tuple[str, ...]]:
    questions: dict[str, Question] = {}
    source_order: list[str] = []
    for index, row in enumerate(_jsonl(path, "questions.jsonl")):
        question_id = _nonblank(row.get("id"), f"question {index} id")
        if question_id in questions:
            raise LongMemEvalV2CatalogError(f"Duplicate LongMemEval-V2 question id: {question_id}")
        domain = row.get("domain")
        if domain not in {"web", "enterprise"}:
            raise LongMemEvalV2CatalogError(f"Invalid question domain for {question_id}")
        _nonblank(row.get("question"), f"question text for {question_id}")
        question_type = row.get("question_type")
        if question_type not in _QUESTION_TYPE_ABILITIES:
            raise LongMemEvalV2CatalogError(f"Unsupported question type for {question_id}")
        questions[question_id] = Question(question_id=question_id, domain=domain, question_type=question_type)
        source_order.append(question_id)
    return questions, tuple(source_order)


def _trajectories(path: Path) -> Mapping[str, Literal["web", "enterprise"]]:
    trajectories: dict[str, Literal["web", "enterprise"]] = {}
    for index, row in enumerate(_jsonl(path, "trajectories.jsonl")):
        trajectory_id = _nonblank(row.get("id"), f"trajectory {index} id")
        if trajectory_id in trajectories:
            raise LongMemEvalV2CatalogError(f"Duplicate LongMemEval-V2 trajectory id: {trajectory_id}")
        domain = row.get("domain")
        if domain not in {"web", "enterprise"}:
            raise LongMemEvalV2CatalogError(f"Invalid trajectory domain for {trajectory_id}")
        trajectories[trajectory_id] = domain
    return MappingProxyType(trajectories)


def _haystack(path: Path) -> Mapping[str, tuple[str, ...]]:
    try:
        with path.open(encoding="utf-8") as source:
            value = json.load(source)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise LongMemEvalV2CatalogError("LongMemEval-V2 haystack is not valid JSON") from error
    if not isinstance(value, dict):
        raise LongMemEvalV2CatalogError("LongMemEval-V2 haystack must be a JSON object")
    haystack: dict[str, tuple[str, ...]] = {}
    for question_id, trajectory_ids in value.items():
        if not isinstance(question_id, str) or not question_id:
            raise LongMemEvalV2CatalogError("LongMemEval-V2 haystack contains an invalid question id")
        if not isinstance(trajectory_ids, list) or not trajectory_ids:
            raise LongMemEvalV2CatalogError(f"LongMemEval-V2 haystack is empty for {question_id}")
        if not all(isinstance(trajectory_id, str) and trajectory_id for trajectory_id in trajectory_ids):
            raise LongMemEvalV2CatalogError(
                f"LongMemEval-V2 haystack contains an invalid trajectory id for {question_id}"
            )
        if len(trajectory_ids) != len(set(trajectory_ids)):
            raise LongMemEvalV2CatalogError(
                f"LongMemEval-V2 haystack contains duplicate trajectories for {question_id}"
            )
        haystack[question_id] = tuple(trajectory_ids)
    return MappingProxyType(haystack)


def _validate_haystack(
    questions: Mapping[str, Question],
    trajectories: Mapping[str, Literal["web", "enterprise"]],
    haystack: Mapping[str, tuple[str, ...]],
) -> None:
    if set(haystack) != set(questions):
        raise LongMemEvalV2CatalogError("LongMemEval-V2 questions and haystack ids must match exactly")
    for question_id, trajectory_ids in haystack.items():
        question = questions[question_id]
        for trajectory_id in trajectory_ids:
            trajectory_domain = trajectories.get(trajectory_id)
            if trajectory_domain is None:
                raise LongMemEvalV2CatalogError(
                    f"LongMemEval-V2 haystack references an unknown trajectory: {trajectory_id}"
                )
            if trajectory_domain != question.domain:
                raise LongMemEvalV2CatalogError(f"LongMemEval-V2 haystack crosses domains for question {question_id}")


def _jsonl(path: Path, label: str) -> Iterator[dict[str, object]]:
    """Parse one JSONL input incrementally to support multi-gigabyte trajectories."""

    found = False
    try:
        with path.open(encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    continue
                found = True
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LongMemEvalV2CatalogError(
                        f"LongMemEval-V2 input {label} has invalid JSON at {line_number}"
                    ) from error
                if not isinstance(value, dict):
                    raise LongMemEvalV2CatalogError(
                        f"LongMemEval-V2 input {label} has a non-object row at {line_number}"
                    )
                yield value
    except (OSError, UnicodeDecodeError) as error:
        raise LongMemEvalV2CatalogError(f"Cannot read LongMemEval-V2 input {label}: {path}") from error
    if not found:
        raise LongMemEvalV2CatalogError(f"LongMemEval-V2 input {label} is blank")


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LongMemEvalV2CatalogError(f"Invalid LongMemEval-V2 {label}")
    return value


def _is_lower_hex(value: str) -> bool:
    return all(character in "0123456789abcdef" for character in value)
