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

"""Bounded, content-free measurements over an immutable, Scope-local recall pool.

This module has no Runtime writes, model calls, or product ranking policy. Exact
evidence identity and lexical similarity are deliberately separate observations.
"""

from __future__ import annotations

import copy
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import combinations

from powercontext_eval_longmemeval_v2.errors import LongMemEvalError

GROUPING_VERSION = "exact-scoped-evidence-v1"
EVIDENCE_VERSION = "scoped-source-artifact-evidence-v1"
LEXICAL_VERSION = "unicode-character-trigram-jaccard-v1"
LEXICAL_THRESHOLD = 0.75
MANIFEST_SCHEMA = "powercontext.lineage-evaluation-manifest.v1"
ARMS = ("lineage-baseline-v1", "lineage-exact-collapse-measurement-v1", "lineage-lexical-annotations-v1")
MAX_POOL = 16


class LineageEvaluationError(LongMemEvalError):
    """An evaluation cannot preserve its evidence, isolation, or replay contract."""


@dataclass(frozen=True, order=True)
class EntryRef:
    scope: str
    artifact: str
    revision: int
    family: str = "atomic-memory"


@dataclass(frozen=True)
class Material:
    scope: str
    source_type: str
    source_id: str
    digest: str
    definition: str
    definition_version: str
    fingerprint: str | None
    declaration_version: str
    authority: str
    verification: str

    @property
    def identity(self) -> tuple[object, ...]:
        # Never coalesce different Source identities merely because bodies hash equally.
        return (
            self.scope,
            self.source_type,
            self.source_id,
            self.digest,
            self.definition,
            self.definition_version,
            self.fingerprint,
        )


@dataclass(frozen=True)
class Observation:
    ref: EntryRef
    kind: str
    text: str
    sources: tuple[Material, ...] = ()
    artifacts: tuple[EntryRef, ...] = ()
    facts: frozenset[str] = frozenset()


def validate_pool(pool: Sequence[Observation]) -> None:
    if len(pool) > MAX_POOL:
        raise LineageEvaluationError("candidate pool exceeds the fixed evaluation bound")
    if len({entry.ref for entry in pool}) != len(pool):
        raise LineageEvaluationError("candidate pool repeats an exact entry revision")
    scopes = {entry.ref.scope for entry in pool}
    if len(scopes) > 1:
        raise LineageEvaluationError("a recall pool must belong to one authorized Scope")
    for entry in pool:
        if entry.ref.revision < 1 or any(ref.scope != entry.ref.scope for ref in entry.artifacts):
            raise LineageEvaluationError("invalid or cross-Scope Artifact evidence")
        for source in entry.sources:
            if source.scope != entry.ref.scope or not source.digest or not source.declaration_version:
                raise LineageEvaluationError("unbound or cross-Scope Source materialization")


def exact_representatives(pool: Sequence[Observation]) -> tuple[int, ...]:
    """Stable equality projection for measurement; the original pool remains intact."""
    validate_pool(pool)
    seen: set[tuple[str, bytes]] = set()
    kept = []
    for index, entry in enumerate(pool):
        key = (entry.kind, entry.text.encode("utf-8"))
        if key not in seen:
            kept.append(index)
            seen.add(key)
    return tuple(kept)


def evidence_identities(pool: Sequence[Observation]) -> frozenset[tuple[object, ...]]:
    """Distinct direct evidence, with separate Source and exact Artifact identity domains.

    Artifact evidence is the cited historical revision, not its current head or
    recursively expanded lineage. Identities stay in-process; only counts leave.
    """
    validate_pool(pool)
    sources = frozenset(("source", *source.identity) for entry in pool for source in entry.sources)
    artifacts = frozenset(
        ("artifact", ref.scope, ref.family, ref.artifact, ref.revision) for entry in pool for ref in entry.artifacts
    )
    return sources | artifacts


def _shingles(text: str) -> frozenset[str]:
    value = " ".join(unicodedata.normalize("NFC", text).casefold().split())
    return frozenset(value[index : index + 3] for index in range(len(value) - 2))


def lexical_pairs(pool: Sequence[Observation]) -> tuple[tuple[int, int], ...]:
    """Annotate lexical neighbours; this is neither semantic truth nor lineage."""
    validate_pool(pool)
    shingles = tuple(_shingles(entry.text) for entry in pool)
    pairs = []
    for left, right in combinations(range(len(pool)), 2):
        if pool[left].text == pool[right].text or pool[left].kind != pool[right].kind:
            continue
        a, b = shingles[left], shingles[right]
        if a and b and len(a & b) / len(a | b) >= LEXICAL_THRESHOLD:
            pairs.append((left, right))
    return tuple(pairs)


def measure_pool(pool: Sequence[Observation], *, top_k: int = 8) -> dict[str, object]:
    """Export only counts and ratios, with unknown evidence kept explicit."""
    validate_pool(pool)
    top = pool[:top_k]
    material_counts: Counter[tuple[object, ...]] = Counter()
    artifact_counts: Counter[tuple[str, str]] = Counter()
    exact_counts: Counter[tuple[str, bytes]] = Counter()
    referenced_artifacts: Counter[EntryRef] = Counter()
    parent: dict[EntryRef, EntryRef] = {}

    def root(ref: EntryRef) -> EntryRef:
        while ref in parent:
            ref = parent[ref]
        return ref

    # Connect only exact declared predecessor/evidence references. These components
    # describe observed lineage, not an equivalence relation between factual claims.
    for entry in pool:
        for ref in entry.artifacts:
            left, right = root(entry.ref), root(ref)
            if left != right:
                parent[max(left, right)] = min(left, right)
    components = Counter(root(entry.ref) for entry in top)
    known = 0
    unknown_declarations = 0
    for entry in top:
        identities = {material.identity for material in entry.sources}
        material_counts.update(identities)
        known += bool(identities)
        unknown_declarations += sum(
            material.authority == "untrusted" or material.verification == "unknown" for material in entry.sources
        )
        artifact_counts[(entry.ref.scope, entry.ref.artifact)] += 1
        referenced_artifacts.update(set(entry.artifacts))
        exact_counts[(entry.kind, entry.text.encode("utf-8"))] += 1
    neighbours = lexical_pairs(pool)
    annotated = {index for pair in neighbours for index in pair}
    false_pairs = sum(
        bool(pool[a].facts and pool[b].facts) and not (pool[a].facts & pool[b].facts) for a, b in neighbours
    )
    unknown_pairs = sum(not pool[a].facts or not pool[b].facts for a, b in neighbours)
    facts = set().union(*(entry.facts for entry in pool)) if pool else set()
    evidence = evidence_identities(pool)
    return {
        "candidate_count": len(pool),
        "top_k_count": len(top),
        "same_source_top_k_count": max(material_counts.values(), default=0),
        "same_source_top_k_concentration": max(material_counts.values(), default=0) / len(top) if top else None,
        "same_entry_lineage_top_k_count": max(artifact_counts.values(), default=0),
        "exact_lineage_component_top_k_count": max(components.values(), default=0),
        "exact_lineage_top_k_concentration": max(components.values(), default=0) / len(top) if top else None,
        "shared_exact_artifact_top_k_count": max(referenced_artifacts.values(), default=0),
        "unknown_source_evidence_count": len(top) - known,
        "neutral_or_unknown_declaration_count": unknown_declarations,
        "exact_redundant_top_k_slots": sum(count - 1 for count in exact_counts.values()),
        "exact_redundant_top_k_occupancy": sum(count - 1 for count in exact_counts.values()) / len(top)
        if top
        else None,
        "lexical_neighbour_pairs": len(neighbours),
        "lexical_neighbour_slots": len(annotated),
        "lexical_neighbour_occupancy": len(annotated) / len(pool) if pool else None,
        "lexical_false_relation_pairs": false_pairs,
        "lexical_unknown_relation_pairs": unknown_pairs,
        "advisory_unverified_relation_pairs": len(neighbours),
        "unique_evidence_count": len(evidence),
        "unique_source_evidence_count": sum(identity[0] == "source" for identity in evidence),
        "unique_artifact_evidence_count": sum(identity[0] == "artifact" for identity in evidence),
        "unique_fact_count": len(facts),
    }


def ensure_comparable_lineage_runs(first: Mapping[str, object], second: Mapping[str, object]) -> None:
    """Refuse every undeclared manifest difference, including newly added fields."""
    if any(
        value.get("schema") != MANIFEST_SCHEMA or value.get("experiment_arm") not in ARMS for value in (first, second)
    ):
        raise LineageEvaluationError("unknown lineage manifest schema or experiment arm")
    required = {
        "fixture_version",
        "fixture_sha256",
        "runtime_revision",
        "runtime_source_sha256",
        "evaluation_source_sha256",
        "backend",
        "capabilities",
        "dependency_state",
        "grouping_version",
        "evidence_version",
        "lexical_version",
        "lexical_threshold",
        "candidate_limit",
        "top_k",
        "model",
        "advisory_jev",
        "environment",
    }
    for value in (first, second):
        if not required <= value.keys() or any(value[key] is None for key in required - {"model"}):
            raise LineageEvaluationError("lineage manifest lacks pinned comparison conditions")
        if (
            value["grouping_version"] != GROUPING_VERSION
            or value["evidence_version"] != EVIDENCE_VERSION
            or value["lexical_version"] != LEXICAL_VERSION
            or value["candidate_limit"] != MAX_POOL
            or value["top_k"] != 8
            or value["lexical_threshold"] != LEXICAL_THRESHOLD
        ):
            raise LineageEvaluationError("unregistered evaluation policy configuration")
    a, b = copy.deepcopy(dict(first)), copy.deepcopy(dict(second))
    a.pop("experiment_arm")
    b.pop("experiment_arm")
    if a != b:
        raise LineageEvaluationError("run manifests differ outside the declared evaluation arm")
