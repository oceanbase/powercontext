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

"""Load the frozen budget manifest and turn each case into production Builder inputs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import UTC, datetime
from itertools import cycle
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import ExperienceContent, ExperienceSearchHit
from powercontext.builtin.artifacts.profile.models import Profile, ProfileContent, ProfileGeneration
from powercontext.builtin.artifacts.topic_memory import TopicMemorySearchHit
from powercontext.builtin.persistence.atomic_memory_index import AtomicMemoryIndexHit
from powercontext.builtin.runtime import PrepareContextRequest
from powercontext.builtin.runtime.atomic_memory import AtomicMemorySearchHit
from powercontext.builtin.runtime.prepared_context import (
    PreparedExperienceCandidates,
    PreparedMemoryCandidates,
    PreparedProfileCandidate,
)

SCHEMA_VERSION = "powercontext.prepared-context-budget-experiment.v1"
# One fixed query: the Builder never reads it, and the experiment neither varies nor records queries.
_QUERY = "synthetic prepared-context budget fixture"
_REVISION = 1
_PROFILE_CREATED_AT = datetime(2026, 11, 6, tzinfo=UTC)


class ManifestError(ValueError):
    """The manifest is not a valid frozen budget experiment."""

    def __init__(self, where: str, problem: str) -> None:
        super().__init__(f"invalid prepared-context budget manifest ({where}): {problem}")


@dataclass(frozen=True)
class Case:
    """One fixed case: production Builder inputs plus the facts declared for each candidate."""

    id: str
    role: Literal["primary", "sensitivity"]
    critical: bool
    sensitivity_of: str | None
    path: Literal["default", "assembly"]
    request: PrepareContextRequest
    current_scope_id: str
    memory: tuple[PreparedMemoryCandidates, ...]
    topic_memory: tuple[TopicMemorySearchHit, ...]
    experience: tuple[PreparedExperienceCandidates, ...]
    profile: tuple[PreparedProfileCandidate, ...]
    #: ``(artifact family, artifact id) -> facts`` that must occur literally in the candidate body.
    facts: dict[tuple[str, str], tuple[str, ...]]

    @property
    def input_sha256(self) -> str:
        """SHA-256 of the canonical JSON of every materialized Builder input of this case."""

        inputs = {
            "request": self.request,
            "current_scope_id": self.current_scope_id,
            "memory_candidates": self.memory,
            "topic_memory_hits": self.topic_memory,
            "experience_candidates": self.experience,
            "profile_candidates": self.profile,
        }
        canonical = json.dumps(_canonical(inputs), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Manifest:
    path: Path
    sha256: str
    #: The preregistered materialization and ``waterfill-v1`` definitions published beside the manifest.
    definitions_path: Path
    definitions_sha256: str
    schema_version: str
    dataset_version: str
    contract_version: str
    arms: dict[str, str]
    scoring: dict[str, Any]
    decision: dict[str, Any]
    limitations: tuple[str, ...]
    cases: tuple[Case, ...]

    def case(self, case_id: str) -> Case:
        return next(case for case in self.cases if case.id == case_id)


def load_manifest(path: Path) -> Manifest:
    raw = path.read_bytes()
    data = json.loads(raw)
    if data.get("schema_version") != SCHEMA_VERSION:
        raise ManifestError(str(path), f"schema_version must be {SCHEMA_VERSION}")
    cases = tuple(_case(item) for item in data["cases"])
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ManifestError(str(path), "case ids must be unique")
    for case in cases:
        if case.role == "sensitivity" and case.sensitivity_of not in ids:
            raise ManifestError(case.id, "sensitivity_of must name a case in the manifest")
    definitions = path.with_name(f"{path.stem}-definitions.md")
    if not definitions.is_file():
        raise ManifestError(str(path), f"preregistered definitions {definitions.name} are missing")
    return Manifest(
        path=path,
        sha256=hashlib.sha256(raw).hexdigest(),
        definitions_path=definitions,
        definitions_sha256=hashlib.sha256(definitions.read_bytes()).hexdigest(),
        schema_version=data["schema_version"],
        dataset_version=data["dataset_version"],
        contract_version=data["contract_version"],
        arms=dict(data["arms"]),
        scoring=dict(data["scoring"]),
        decision=dict(data["decision"]),
        limitations=tuple(data["limitations"]),
        cases=cases,
    )


def materialize(spec: dict[str, Any], where: str) -> str:
    """Build one synthetic body: a literal ``text``, or ``head`` + repeated ``fill`` + ``tail`` of exact size.

    Padding uses ``.`` only when a multibyte ``fill`` cannot reach the declared UTF-8 size exactly.
    """

    if "text" in spec:
        return cast(str, spec["text"])
    head, fill, tail, size = spec["head"], spec["fill"], spec.get("tail", ""), spec["bytes"]
    budget = size - _utf8(head) - _utf8(tail)
    if not fill or budget < 0:
        raise ManifestError(where, "body needs a non-empty fill and room for head and tail")
    parts: list[str] = []
    used = 0
    for char in cycle(fill):
        if used + _utf8(char) > budget:
            break
        parts.append(char)
        used += _utf8(char)
    body = head + "".join(parts) + "." * (budget - used) + tail
    if _utf8(body) != size:
        raise ManifestError(where, "body size does not match bytes")
    return body


def _case(item: dict[str, Any]) -> Case:
    case_id = item["id"]
    path = item["path"]
    if path not in {"default", "assembly"} or ("assembly" in item) != (path == "assembly"):
        raise ManifestError(case_id, "path must be default without assembly, or assembly with one")
    if item["role"] not in {"primary", "sensitivity"}:
        raise ManifestError(case_id, "role must be primary or sensitivity")
    facts: dict[tuple[str, str], tuple[str, ...]] = {}
    memory = tuple(
        PreparedMemoryCandidates(
            scope_id=group["scope_id"],
            hits=tuple(_memory_hit(hit, case_id, facts) for hit in group["hits"]),
        )
        for group in item.get("memory", ())
    )
    topic_memory = tuple(_topic_hit(hit, case_id, facts) for hit in item.get("topic_memory", ()))
    experience = tuple(
        PreparedExperienceCandidates(
            scope_id=group["scope_id"],
            hits=tuple(_experience_hit(hit, case_id, facts) for hit in group["hits"]),
        )
        for group in item.get("experience", ())
    )
    profile = tuple(_profile(candidate, case_id, facts) for candidate in item.get("profile", ()))
    request: dict[str, Any] = {"query": _QUERY, "max_bytes": item["max_bytes"], "include_receipt": True}
    if path == "assembly":
        request["assembly"] = item["assembly"]
    return Case(
        id=case_id,
        role=item["role"],
        critical=bool(item["critical"]),
        sensitivity_of=item.get("sensitivity_of"),
        path=path,
        request=PrepareContextRequest.model_validate(request),
        current_scope_id=item["current_scope_id"],
        memory=memory,
        topic_memory=topic_memory,
        experience=experience,
        profile=profile,
        facts=facts,
    )


def _declare(
    facts: dict[tuple[str, str], tuple[str, ...]], family: str, item: dict[str, Any], body: str, where: str
) -> None:
    key = (family, item["id"])
    if key in facts:
        raise ManifestError(where, f"duplicate candidate {family}/{item['id']}")
    declared = tuple(item["facts"])
    if not declared or any(fact not in body for fact in declared):
        raise ManifestError(where, f"every declared fact must occur in {family}/{item['id']}")
    facts[key] = declared


def _memory_hit(
    item: dict[str, Any], where: str, facts: dict[tuple[str, str], tuple[str, ...]]
) -> AtomicMemorySearchHit:
    text = materialize(item["body"], where)
    if not text.strip():
        raise ManifestError(where, "blank bodies are not budget candidates")
    _declare(facts, "atomic-memory", item, text, where)
    return AtomicMemorySearchHit(
        hit=AtomicMemoryIndexHit(
            artifact_ref=ArtifactRef(family="atomic-memory", artifact_id=item["id"], revision=_REVISION),
            state_version=0,
            kind="fact",
            text=text,
            score=1.0,
        ),
        matched_by=("fts",),
    )


def _topic_hit(item: dict[str, Any], where: str, facts: dict[tuple[str, str], tuple[str, ...]]) -> TopicMemorySearchHit:
    summary = materialize(item["summary"], where)
    _declare(facts, "topic-memory", item, item["title"] + summary + item.get("snippet", ""), where)
    return TopicMemorySearchHit(
        artifact_ref=ArtifactRef(family="topic-memory", artifact_id=item["id"], revision=_REVISION),
        title=item["title"],
        summary=summary,
        snippet=item.get("snippet"),
        score=1.0,
        matched_by=("topic_fts",),
    )


def _experience_hit(
    item: dict[str, Any], where: str, facts: dict[tuple[str, str], tuple[str, ...]]
) -> ExperienceSearchHit:
    fields = {name: item[name] for name in ("situation", "action", "outcome", "lesson")}
    _declare(facts, "experience", item, "".join(fields.values()), where)
    return ExperienceSearchHit(
        artifact_ref=ArtifactRef(family="experience", artifact_id=item["id"], revision=_REVISION),
        content=ExperienceContent(**fields),
    )


def _profile(
    item: dict[str, Any], where: str, facts: dict[tuple[str, str], tuple[str, ...]]
) -> PreparedProfileCandidate:
    content = materialize(item["body"], where)
    _declare(facts, "profile", item, content, where)
    return PreparedProfileCandidate(
        scope_id=item["scope_id"],
        profile=Profile(
            artifact_id=item["id"],
            revision=item["revision"],
            content=ProfileContent(
                content=content,
                generation=ProfileGeneration(mode="manual_replace", created_at=_PROFILE_CREATED_AT),
            ),
        ),
    )


def _utf8(text: str) -> int:
    return len(text.encode("utf-8"))


def _canonical(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _canonical(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {key: _canonical(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_canonical(item) for item in value]
    return value
