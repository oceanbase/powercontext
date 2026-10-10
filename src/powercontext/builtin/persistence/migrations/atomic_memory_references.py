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

"""Offline conversion of references to legacy Memory and removal of its public rows.

An exact legacy entry citation becomes the deterministic Atomic Memory revision
imported from the same entry version. A relationship to a whole collection has
no single Atomic target: its original location and value are archived with the
collection before the online relationship is removed. Converted business values
are serialized with today's models so they remain readable through normal paths.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated, Any, Literal

import rfc8785
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.experience.recurrence import (
    RecurrenceMatch,
    RecurrenceObservation,
    item_digest,
    match_key,
    observation_id,
    observation_identity,
    selection_key,
    verdict_key,
)
from powercontext.builtin.artifacts.handoff.models import HandoffContent, HandoffSourceCitation
from powercontext.builtin.dream.models import CreateDreamRunRequest, DreamRecord
from powercontext.builtin.persistence.atomic_memory_identity import legacy_entry_artifact_id
from powercontext.builtin.persistence.atomic_memory_readiness import (
    LEGACY_ENTRY_FOREIGN_KEYS,
    atomic_memory_readiness_issues,
    legacy_citation_columns,
    legacy_entry_foreign_key_names,
    legacy_foreign_keys,
)
from powercontext.builtin.persistence.codec import dump_model
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.migrations.atomic_memory_archive import (
    LEGACY_CITATION_COLUMN,
    LegacyCollection,
    archive_collection,
    archive_incoming_reference,
    rows,
    table_names,
)
from powercontext.builtin.work.models import (
    CurrentWorkHandoff,
    HandoffReceipt,
    TaskCheck,
    TaskOutcome,
    WorkClaim,
    WorkContract,
)

DECISIONS_FORMAT = "powercontext.atomic-memory-reference-decisions.v1"
DREAM_HISTORY_FORMAT = "powercontext.dream-history.v1"
_WORK_MODELS: dict[str, type[BaseModel]] = {
    "work-contract": WorkContract,
    "handoff-boundary": CurrentWorkHandoff,
    "handoff-receipt": HandoffReceipt,
    "task-outcome": TaskOutcome,
}
_TERMINAL_DREAM = frozenset({"succeeded", "failed"})


class _LegacyValue(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class _LegacyArtifactRef(_LegacyValue):
    family: str
    artifact_id: str
    revision: int


class _LegacyArtifactCitation(_LegacyValue):
    kind: Literal["artifact"] = "artifact"
    artifact_ref: _LegacyArtifactRef


class _LegacyEntryCitation(_LegacyValue):
    memory_ref: _LegacyArtifactRef
    entry_id: str
    entry_version_id: str


class _LegacyMemoryCitation(_LegacyValue):
    kind: Literal["memory"] = "memory"
    memory_citation: _LegacyEntryCitation


_LegacyCitation = Annotated[
    HandoffSourceCitation | _LegacyArtifactCitation | _LegacyMemoryCitation, Field(discriminator="kind")
]


class _LegacyWorkClaim(WorkClaim):
    """Frozen decoder of a Task Outcome observation that may still cite legacy Memory."""

    evidence: tuple[_LegacyCitation, ...] = ()


class _LegacyTaskCheck(TaskCheck):
    """Frozen decoder of a Task Outcome check that may still cite legacy Memory."""

    evidence: tuple[_LegacyCitation, ...] = ()


def _legacy_item_digests(outcome: Mapping[str, Any]) -> dict[tuple[str, int], str]:
    """Digest each stored Task Outcome item exactly as the ledger recorded it before conversion."""

    digests: dict[tuple[str, int], str] = {}
    for kind, name, model in (("observation", "observations", _LegacyWorkClaim), ("check", "checks", _LegacyTaskCheck)):
        for index, item in enumerate(outcome.get(name, ())):
            digests[(kind, index)] = item_digest(model.model_validate_json(_compact(item)))
    return digests


@dataclass(frozen=True)
class CandidateDecision:
    """An operator-supplied replacement for Candidate evidence that cited only whole collections."""

    scope_id: str
    candidate_id: str
    version: int
    artifact_refs: tuple[dict[str, Any], ...]

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.scope_id, self.candidate_id, self.version)


def load_decisions(payload: Mapping[str, Any]) -> dict[tuple[str, str, int], CandidateDecision]:
    """Validate a decision file; only ``replace`` has a defined, auditable effect."""

    if payload.get("format") != DECISIONS_FORMAT or not isinstance(payload.get("decisions"), list):
        raise ValueError(f"decision file must use format {DECISIONS_FORMAT}")  # noqa: TRY003
    result: dict[tuple[str, str, int], CandidateDecision] = {}
    for item in payload["decisions"]:
        if not isinstance(item, dict) or item.get("carrier") != "candidate" or item.get("field") != "artifact_refs":
            raise ValueError("decisions apply only to Candidate artifact_refs")  # noqa: TRY003
        if item.get("action") != "replace":
            raise ValueError(f"unsupported decision action {item.get('action')!r}; use replace")  # noqa: TRY003
        refs = item.get("artifact_refs")
        if not isinstance(refs, list) or not refs:
            raise ValueError("a replace decision requires non-empty artifact_refs")  # noqa: TRY003
        normalized = tuple(_artifact_ref(ref) for ref in refs)
        if any(ref["family"] == "memory" for ref in normalized):
            raise ValueError("a replace decision cannot cite a legacy Memory collection")  # noqa: TRY003
        decision = CandidateDecision(
            scope_id=str(item["scope_id"]),
            candidate_id=str(item["candidate_id"]),
            version=int(item["version"]),
            artifact_refs=normalized,
        )
        if decision.key in result:
            raise ValueError(f"duplicate decision for {decision.key}")  # noqa: TRY003
        result[decision.key] = decision
    return result


def _artifact_ref(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"family", "artifact_id", "revision"}:
        raise ValueError("an ArtifactRef has exactly family, artifact_id and revision")  # noqa: TRY003
    if type(value["revision"]) is not int or value["revision"] < 1:
        raise ValueError("an ArtifactRef revision must be a positive integer")  # noqa: TRY003
    return {"family": str(value["family"]), "artifact_id": str(value["artifact_id"]), "revision": value["revision"]}


def _compact(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _unique(values: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


@dataclass
class _Context:
    collections: dict[tuple[str, str], LegacyCollection]
    decisions: dict[tuple[str, str, int], CandidateDecision]
    write: bool
    citation_tables: frozenset[str] = frozenset()
    errors: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    used_decisions: set[tuple[str, str, int]] = field(default_factory=set)
    _manifests: dict[tuple[str, str, int], dict[str, str]] = field(default_factory=dict)

    def count(self, name: str, amount: int = 1) -> None:
        self.counts[name] = self.counts.get(name, 0) + amount

    def manifest(self, scope_id: str, memory_id: str, revision: int) -> dict[str, str]:
        key = (scope_id, memory_id, revision)
        if key not in self._manifests:
            collection = self.collections.get((scope_id, memory_id))
            row = (
                None
                if collection is None
                else next((item for item in collection.revisions if item["revision"] == revision), None)
            )
            if row is None:
                raise ValueError(f"cited collection revision {scope_id}/{memory_id}@{revision} does not exist")  # noqa: TRY003
            entries = json.loads(bytes(row["content"]))["manifest"]["entries"]
            self._manifests[key] = {item["entry_id"]: item["entry_version_id"] for item in entries}
        return self._manifests[key]

    async def atomic_ref(
        self, connection: AsyncConnection, scope_id: str, citation: Mapping[str, Any]
    ) -> dict[str, Any]:
        """Map one exact legacy entry citation to its imported Atomic revision."""

        memory_ref = citation["memory_ref"]
        if memory_ref.get("family") != "memory":
            raise ValueError("Memory citation does not identify a legacy collection")  # noqa: TRY003
        memory_id, entry_id, version_id = memory_ref["artifact_id"], citation["entry_id"], citation["entry_version_id"]
        if self.manifest(scope_id, memory_id, memory_ref["revision"]).get(entry_id) != version_id:
            raise ValueError(f"{entry_id} is not present at the cited collection revision")  # noqa: TRY003
        version = await connection.scalar(
            text(
                "SELECT version FROM pc_memory_entry_versions WHERE scope_id = :scope AND memory_artifact_id = :memory "
                "AND entry_version_id = :version AND entry_id = :entry"
            ),
            {"scope": scope_id, "memory": memory_id, "version": version_id, "entry": entry_id},
        )
        if version is None:
            raise ValueError(f"{entry_id}: cited entry version is missing")  # noqa: TRY003
        artifact_id = legacy_entry_artifact_id(scope_id, memory_id, entry_id)
        if not self.write:
            # A plan runs before import; the entry version above is what import will create.
            return {"family": "atomic-memory", "artifact_id": artifact_id, "revision": int(version)}
        present = await connection.scalar(
            text(
                "SELECT 1 FROM pc_artifacts WHERE scope_id = :scope AND family = 'atomic-memory' "
                "AND artifact_id = :id AND revision = :revision"
            ),
            {"scope": scope_id, "id": artifact_id, "revision": version},
        )
        if present != 1:
            raise ValueError(f"{entry_id}: imported Atomic revision is missing")  # noqa: TRY003
        return {"family": "atomic-memory", "artifact_id": artifact_id, "revision": int(version)}


class _Units:
    """Run each record in its own transaction for apply, or on one read-only connection for plan."""

    def __init__(self, database: AsyncDatabase | None, connection: AsyncConnection | None) -> None:
        self._database = database
        self._connection = connection

    @asynccontextmanager
    async def unit(self) -> AsyncIterator[AsyncConnection]:
        if self._connection is not None:
            yield self._connection
            return
        if self._database is None:
            raise RuntimeError("no database for the migration unit")  # noqa: TRY003
        async with self._database.transaction() as connection:
            yield connection


async def _lineage_refs(connection: AsyncConnection, owner: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = await rows(
        connection,
        "pc_artifact_lineage_artifacts",
        ("upstream_family", "upstream_artifact_id", "upstream_revision"),
        "WHERE scope_id = :scope AND family = :family AND artifact_id = :id AND revision = :revision ORDER BY ordinal",
        scope=owner["scope_id"],
        family=owner["family"],
        id=owner["artifact_id"],
        revision=owner["revision"],
    )
    return [
        {
            "family": row["upstream_family"],
            "artifact_id": row["upstream_artifact_id"],
            "revision": row["upstream_revision"],
        }
        for row in values
    ]


async def _replace_lineage_refs(
    connection: AsyncConnection, owner: Mapping[str, Any], refs: list[dict[str, Any]]
) -> None:
    identity = {
        "scope_id": owner["scope_id"],
        "family": owner["family"],
        "artifact_id": owner["artifact_id"],
        "revision": owner["revision"],
    }
    await connection.execute(
        text(
            "DELETE FROM pc_artifact_lineage_artifacts WHERE scope_id = :scope_id AND family = :family "
            "AND artifact_id = :artifact_id AND revision = :revision"
        ),
        identity,
    )
    for ordinal, ref in enumerate(refs):
        await connection.execute(
            text(
                "INSERT INTO pc_artifact_lineage_artifacts (scope_id, family, artifact_id, revision, ordinal, "
                "upstream_family, upstream_artifact_id, upstream_revision) VALUES (:scope_id, :family, :artifact_id, "
                ":revision, :ordinal, :upstream_family, :upstream_artifact_id, :upstream_revision)"
            ),
            {
                **identity,
                "ordinal": ordinal,
                "upstream_family": ref["family"],
                "upstream_artifact_id": ref["artifact_id"],
                "upstream_revision": ref["revision"],
            },
        )


_DROPPED = object()


def _collection_citation(value: Any) -> dict[str, Any] | None:
    if (
        isinstance(value, dict)
        and value.get("kind") == "artifact"
        and isinstance(value.get("artifact_ref"), dict)
        and value["artifact_ref"].get("family") == "memory"
    ):
        return value["artifact_ref"]
    return None


def _holds_collection_citation(value: Any) -> bool:
    if _collection_citation(value) is not None:
        return True
    items = value.values() if isinstance(value, dict) else value if isinstance(value, list) else ()
    return any(_holds_collection_citation(item) for item in items)


async def _convert_citations(
    context: _Context,
    connection: AsyncConnection,
    scope_id: str,
    value: Any,
    collected: list[tuple[dict[str, Any], dict[str, Any]]] | None = None,
    dropped: list[tuple[str, dict[str, Any]]] | None = None,
    path: str = "",
) -> Any:
    """Replace every ``kind=memory`` citation with its exact Atomic revision.

    With ``dropped``, a citation of a whole collection is removed: from a list,
    or by setting a single optional citation to null. The model validation that
    follows rejects a claim left without the evidence it requires.
    """

    if isinstance(value, list):
        converted = [
            await _convert_citations(context, connection, scope_id, item, collected, dropped, f"{path}/{index}")
            for index, item in enumerate(value)
        ]
        return [item for item in converted if item is not _DROPPED]
    if not isinstance(value, dict):
        return value
    if value.get("kind") == "memory" and "memory_citation" in value:
        citation = value["memory_citation"]
        atomic = await context.atomic_ref(connection, scope_id, citation)
        if collected is not None:
            collected.append((citation["memory_ref"], atomic))
        return {"kind": "artifact", "artifact_ref": atomic}
    collection = _collection_citation(value)
    if collection is not None and dropped is not None:
        dropped.append((path, collection))
        return _DROPPED
    result: dict[str, Any] = {}
    for key, item in value.items():
        converted = await _convert_citations(context, connection, scope_id, item, collected, dropped, f"{path}/{key}")
        result[key] = None if converted is _DROPPED else converted
    return result


async def _archive_dropped(
    context: _Context,
    connection: AsyncConnection,
    scope_id: str,
    dropped: list[tuple[str, dict[str, Any]]],
    location: dict[str, Any],
) -> None:
    context.count("archived_collection_relationships", len(dropped))
    if not context.write:
        return
    for path, ref in dropped:
        await archive_incoming_reference(connection, scope_id, ref, {**location, "path": path, "value": ref})


async def _keys(units: _Units, sql: str) -> list[dict[str, Any]]:
    async with units.unit() as connection:
        return [dict(row) for row in (await connection.execute(text(sql))).mappings()]


async def _each(
    context: _Context,
    units: _Units,
    keys: list[dict[str, Any]],
    label: Callable[[Mapping[str, Any]], str],
    convert: Callable[[AsyncConnection, Mapping[str, Any]], Any],
) -> None:
    for key in keys:
        try:
            async with units.unit() as connection:
                await convert(connection, key)
        except (ValueError, KeyError, TypeError) as error:
            context.errors.append(f"{label(key)}: {error}")


def _artifact_label(key: Mapping[str, Any]) -> str:
    return f"{key['scope_id']}/{key['family']}/{key['artifact_id']}@{key['revision']}"


async def _artifact_memory_citations(context: _Context, units: _Units) -> None:
    if "pc_artifacts" not in context.citation_tables:
        return
    keys = await _keys(
        units,
        "SELECT scope_id, family, artifact_id, revision FROM pc_artifacts WHERE family <> 'memory' "
        "AND memory_citations IS NOT NULL AND LENGTH(memory_citations) > 2",
    )

    async def convert(connection: AsyncConnection, key: Mapping[str, Any]) -> None:
        stored = await connection.scalar(
            text(
                "SELECT memory_citations FROM pc_artifacts WHERE scope_id = :scope_id AND family = :family "
                "AND artifact_id = :artifact_id AND revision = :revision"
            ),
            dict(key),
        )
        citations = json.loads(bytes(stored))
        atomic = [await context.atomic_ref(connection, key["scope_id"], item) for item in citations]
        refs = _unique([*await _lineage_refs(connection, key), *atomic])
        context.count("artifact_memory_citations", len(citations))
        if context.write:
            await _replace_lineage_refs(connection, key, refs)
            await connection.execute(
                text(
                    "UPDATE pc_artifacts SET memory_citations = :empty WHERE scope_id = :scope_id AND family = :family "
                    "AND artifact_id = :artifact_id AND revision = :revision"
                ),
                {**key, "empty": b"[]"},
            )

    await _each(context, units, keys, _artifact_label, convert)


async def _collection_lineage(context: _Context, units: _Units) -> None:
    keys = await _keys(
        units,
        "SELECT DISTINCT scope_id, family, artifact_id, revision FROM pc_artifact_lineage_artifacts "
        "WHERE upstream_family = 'memory' AND family NOT IN ('memory', 'handoff')",
    )

    async def convert(connection: AsyncConnection, key: Mapping[str, Any]) -> None:
        refs = await _lineage_refs(connection, key)
        kept: list[dict[str, Any]] = []
        for ordinal, ref in enumerate(refs):
            if ref["family"] != "memory":
                kept.append(ref)
                continue
            context.count("archived_collection_relationships")
            if context.write:
                await archive_incoming_reference(
                    connection,
                    key["scope_id"],
                    ref,
                    {"carrier": "artifact_lineage", "referrer": dict(key), "ordinal": ordinal, "value": ref},
                )
        if context.write:
            await _replace_lineage_refs(connection, key, kept)

    await _each(context, units, keys, _artifact_label, convert)


async def _publication_root_scope(connection: AsyncConnection, key: Mapping[str, Any]) -> str:
    current = (key["scope_id"], key["family"], key["artifact_id"], key["revision"])
    visited: set[tuple[Any, ...]] = set()
    while current not in visited:
        visited.add(current)
        source = (
            (
                await connection.execute(
                    text(
                        "SELECT source_scope_id, source_family, source_artifact_id, source_revision "
                        "FROM pc_artifact_publications WHERE target_scope_id = :scope AND target_family = :family "
                        "AND target_artifact_id = :id AND target_revision = :revision"
                    ),
                    {"scope": current[0], "family": current[1], "id": current[2], "revision": current[3]},
                )
            )
            .mappings()
            .one_or_none()
        )
        if source is None:
            return current[0]
        current = (
            source["source_scope_id"],
            source["source_family"],
            source["source_artifact_id"],
            source["source_revision"],
        )
    raise ValueError("publication chain has a cycle")  # noqa: TRY003


async def _handoffs(context: _Context, units: _Units) -> None:
    keys = await _keys(
        units,
        "SELECT scope_id, family, artifact_id, revision FROM pc_artifacts WHERE family = 'handoff' "
        "AND (CAST(content AS CHAR) LIKE '%memory_citation%' OR CAST(content AS CHAR) LIKE '%\"memory\"%') "
        "UNION SELECT DISTINCT scope_id, family, artifact_id, revision "
        "FROM pc_artifact_lineage_artifacts WHERE family = 'handoff' AND upstream_family = 'memory'",
    )

    async def convert(connection: AsyncConnection, key: Mapping[str, Any]) -> None:
        stored = await connection.scalar(
            text(
                "SELECT content FROM pc_artifacts WHERE scope_id = :scope_id AND family = :family "
                "AND artifact_id = :artifact_id AND revision = :revision"
            ),
            dict(key),
        )
        content = json.loads(bytes(stored))
        scope_id = await _publication_root_scope(connection, key)
        direct: list[tuple[dict[str, Any], dict[str, Any]]] = []
        dropped: list[tuple[str, dict[str, Any]]] = []
        converted = dict(content)
        for name, value in content.items():
            # Omissions explain what was left out; only direct statement evidence belongs in lineage.
            collected = direct if name in {"state", "next_action"} else None
            converted[name] = await _convert_citations(
                context, connection, scope_id, value, collected, dropped, f"/{name}"
            )
        payload = dump_model(HandoffContent.model_validate_json(_compact(converted)), kind="artifact", name="handoff")
        await _archive_dropped(context, connection, scope_id, dropped, {"carrier": "handoff", "referrer": dict(key)})
        refs: list[dict[str, Any]] = []
        for ordinal, ref in enumerate(await _lineage_refs(connection, key)):
            if ref["family"] != "memory":
                refs.append(ref)
                continue
            replacements = [atomic for memory_ref, atomic in direct if memory_ref == ref]
            if replacements:
                refs.extend(replacements)
                continue
            context.count("archived_collection_relationships")
            if context.write:
                await archive_incoming_reference(
                    connection,
                    key["scope_id"],
                    ref,
                    {"carrier": "artifact_lineage", "referrer": dict(key), "ordinal": ordinal, "value": ref},
                )
        context.count("handoff_revisions")
        if not context.write:
            return
        await _replace_lineage_refs(connection, key, _unique(refs))
        await connection.execute(
            text(
                "UPDATE pc_artifacts SET content = :content WHERE scope_id = :scope_id AND family = :family "
                "AND artifact_id = :artifact_id AND revision = :revision"
            ),
            {**key, "content": payload},
        )
        await connection.execute(
            text(
                "UPDATE pc_artifact_publications SET content_digest = :digest WHERE source_scope_id = :scope_id "
                "AND source_family = :family AND source_artifact_id = :artifact_id AND source_revision = :revision"
            ),
            {**key, "digest": hashlib.sha256(payload).hexdigest()},
        )

    await _each(context, units, keys, _artifact_label, convert)


def _source_label(key: Mapping[str, Any]) -> str:
    return f"{key['scope_id']}/{key['source_type']}/{key['source_id']}"


async def _rewrite_recurrence(
    connection: AsyncConnection,
    key: Mapping[str, Any],
    changed: dict[tuple[str, int], tuple[str, str]],
) -> int:
    """Re-key ledger rows whose immutable item locator digests changed with the Task Outcome."""

    def item(ref: Any) -> Any:
        if ref is None or ref.task_outcome_ref.source_id != key["source_id"]:
            return ref
        digests = changed.get((ref.item_kind, ref.item_index))
        if digests is None or ref.item_digest != digests[0]:
            return ref
        return ref.model_copy(update={"item_digest": digests[1]})

    outcome = {"scope": key["scope_id"], "type": key["source_type"], "id": key["source_id"]}
    rekeyed: dict[str, str] = {}
    updated = 0
    for row in await rows(
        connection,
        "pc_recurrence_match",
        ("match_key", "payload"),
        "WHERE scope_id = :scope AND task_outcome_source_type = :type AND task_outcome_source_id = :id",
        **outcome,
    ):
        match = RecurrenceMatch.model_validate_json(bytes(row["payload"]), strict=True)
        revised = RecurrenceMatch.model_validate({**match.model_dump(), "failure_ref": item(match.failure_ref)})
        new_key = match_key(revised)
        if new_key == row["match_key"]:
            continue
        rekeyed[row["match_key"]] = new_key
        await connection.execute(
            text(
                "UPDATE pc_recurrence_match SET match_key = :new_key, failure_item_digest = :digest, payload = :payload "
                "WHERE scope_id = :scope AND match_key = :old_key"
            ),
            {
                "scope": key["scope_id"],
                "new_key": new_key,
                "old_key": row["match_key"],
                "digest": revised.failure_ref.item_digest,
                "payload": dump_model(revised, kind="recurrence-match", name=new_key),
            },
        )
        updated += 1
    for row in await rows(
        connection,
        "pc_recurrence_observation",
        ("observation_id", "payload"),
        "WHERE scope_id = :scope AND task_outcome_source_type = :type AND task_outcome_source_id = :id",
        **outcome,
    ):
        observation = RecurrenceObservation.model_validate_json(bytes(row["payload"]), strict=True)
        digest = observation.recurrence_match_digest
        parts = observation_identity(observation).model_copy(
            update={
                "condition_ref": item(observation.condition_ref),
                "check_ref": item(observation.check_ref),
                "failure_ref": item(observation.failure_ref),
                "recurrence_match_digest": rekeyed.get(digest, digest) if digest is not None else None,
            }
        )
        identifier = observation_id(**parts.model_dump())
        if identifier == row["observation_id"]:
            continue
        revised_observation = RecurrenceObservation.model_validate({**parts.model_dump(), "observation_id": identifier})
        await connection.execute(
            text(
                "UPDATE pc_recurrence_observation SET observation_id = :new_id, selection_key = :selection, "
                "verdict_key = :verdict, payload = :payload WHERE scope_id = :scope AND observation_id = :old_id"
            ),
            {
                "scope": key["scope_id"],
                "new_id": identifier,
                "old_id": row["observation_id"],
                "selection": selection_key(revised_observation),
                "verdict": verdict_key(revised_observation),
                "payload": dump_model(revised_observation, kind="recurrence-observation", name=identifier),
            },
        )
        updated += 1
    return updated


def _outcome_digests(before: dict[tuple[str, int], str], after: TaskOutcome) -> dict[tuple[str, int], tuple[str, str]]:
    changed: dict[tuple[str, int], tuple[str, str]] = {}
    current = [
        *((("observation", index), item) for index, item in enumerate(after.observations)),
        *((("check", index), item) for index, item in enumerate(after.checks)),
    ]
    if set(before) != {position for position, _item in current}:
        raise ValueError("Task Outcome conversion changed its items")  # noqa: TRY003
    for position, item in current:
        digests = (before[position], item_digest(item))
        if digests[0] != digests[1]:
            changed[position] = digests
    return changed


async def _work_sources(context: _Context, units: _Units) -> None:  # noqa: C901 - One Source rewrite with its ledger.
    keys = await _keys(
        units,
        "SELECT scope_id, source_type, source_id FROM pc_sources WHERE source_type = 'content' "
        "AND CAST(payload AS CHAR) LIKE '%\"memory%'",
    )

    async def convert(connection: AsyncConnection, key: Mapping[str, Any]) -> None:
        stored = await connection.scalar(
            text(
                "SELECT payload FROM pc_sources WHERE scope_id = :scope_id AND source_type = :source_type "
                "AND source_id = :source_id"
            ),
            dict(key),
        )
        envelope = json.loads(bytes(stored))
        value = envelope["value"]
        model = _WORK_MODELS.get(str((value.get("metadata") or {}).get("kind")))
        if model is None:
            return
        original = json.loads(value["content"])
        # A receipt's unavailable evidence records what was missing; it asserts no support to drop.
        dropped: list[tuple[str, dict[str, Any]]] | None = None if model is HandoffReceipt else []
        converted = await _convert_citations(context, connection, key["scope_id"], original, None, dropped)
        if model is TaskOutcome:
            produced = []
            for index, ref in enumerate(converted.get("produced_artifacts", ())):
                if ref.get("family") != "memory":
                    produced.append(ref)
                    continue
                context.count("archived_collection_relationships")
                if context.write:
                    await archive_incoming_reference(
                        connection,
                        key["scope_id"],
                        ref,
                        {
                            "carrier": "work_source",
                            "source": dict(key),
                            "field": "produced_artifacts",
                            "index": index,
                            "value": ref,
                        },
                    )
            converted["produced_artifacts"] = produced
        if converted == original:
            return
        after = model.model_validate_json(_compact(converted))
        await _archive_dropped(
            context, connection, key["scope_id"], dropped or [], {"carrier": "work_source", "source": dict(key)}
        )
        context.count("work_sources")
        if not context.write:
            return
        value = {**value, "content": after.model_dump_json(by_alias=True, exclude_none=False, indent=2)}
        await connection.execute(
            text(
                "UPDATE pc_sources SET payload = :payload WHERE scope_id = :scope_id AND source_type = :source_type "
                "AND source_id = :source_id"
            ),
            {**key, "payload": rfc8785.dumps({**envelope, "value": value})},
        )
        if isinstance(after, TaskOutcome):
            changed = _outcome_digests(_legacy_item_digests(original), after)
            if changed:
                context.count("recurrence_rows", await _rewrite_recurrence(connection, key, changed))

    await _each(context, units, keys, _source_label, convert)


def _candidate_label(key: Mapping[str, Any]) -> str:
    return f"Candidate {key['scope_id']}/{key['candidate_id']} v{key['version']}"


async def _candidates(context: _Context, units: _Units) -> None:
    cited = "pc_artifact_candidate_versions" in context.citation_tables
    keys = await _keys(
        units,
        "SELECT scope_id, candidate_id, version FROM pc_artifact_candidate_versions WHERE family <> 'memory' AND "  # noqa: S608
        + (
            "((memory_citations IS NOT NULL AND LENGTH(memory_citations) > 2) "
            "OR CAST(artifact_refs AS CHAR) LIKE '%\"memory\"%')"
            if cited
            else "CAST(artifact_refs AS CHAR) LIKE '%\"memory\"%'"
        ),
    )

    async def convert(connection: AsyncConnection, key: Mapping[str, Any]) -> None:
        row = (
            await rows(
                connection,
                "pc_artifact_candidate_versions",
                ("source_refs", "artifact_refs", *((LEGACY_CITATION_COLUMN,) if cited else ())),
                "WHERE scope_id = :scope_id AND candidate_id = :candidate_id AND version = :version",
                **key,
            )
        )[0]
        refs = json.loads(bytes(row["artifact_refs"]))
        stored = row.get(LEGACY_CITATION_COLUMN)
        citations = [] if stored is None else json.loads(bytes(stored))
        collection_refs = [(index, ref) for index, ref in enumerate(refs) if ref["family"] == "memory"]
        atomic = [await context.atomic_ref(connection, key["scope_id"], item) for item in citations]
        evidence = _unique([*(ref for ref in refs if ref["family"] != "memory"), *atomic])
        identity = (key["scope_id"], key["candidate_id"], key["version"])
        decision = context.decisions.get(identity)
        if not evidence and not json.loads(bytes(row["source_refs"])):
            if decision is None:
                raise ValueError(  # noqa: TRY003
                    "artifact_refs cite only whole legacy Memory collections "
                    f"{[ref for _index, ref in collection_refs]}; supply a replace decision"
                )
            for ref in decision.artifact_refs:
                present = await connection.scalar(
                    text(
                        "SELECT 1 FROM pc_artifacts WHERE scope_id = :scope AND family = :family "
                        "AND artifact_id = :id AND revision = :revision"
                    ),
                    {
                        "scope": key["scope_id"],
                        "family": ref["family"],
                        "id": ref["artifact_id"],
                        "revision": ref["revision"],
                    },
                )
                if present != 1:
                    raise ValueError(f"replacement evidence {ref} does not exist")  # noqa: TRY003
            context.used_decisions.add(identity)
            evidence = list(decision.artifact_refs)
        context.count("candidate_versions")
        context.count("archived_collection_relationships", len(collection_refs))
        if not context.write:
            return
        for index, ref in collection_refs:
            await archive_incoming_reference(
                connection,
                key["scope_id"],
                ref,
                {
                    "carrier": "candidate",
                    "candidate": dict(key),
                    "field": "artifact_refs",
                    "index": index,
                    "value": ref,
                    "decision": None if decision is None else "replace",
                },
            )
        await connection.execute(
            text(
                "UPDATE pc_artifact_candidate_versions SET artifact_refs = :refs"  # noqa: S608
                + (", memory_citations = :citations" if cited else "")
                + " WHERE scope_id = :scope_id AND candidate_id = :candidate_id AND version = :version"
            ),
            {**key, "refs": _compact(evidence), "citations": None if stored is None else b"[]"},
        )

    await _each(context, units, keys, _candidate_label, convert)


def _holds_memory_reference(value: Any) -> bool:
    """Whether conversion rewrites this value: an entry citation or any reference to a collection."""

    if isinstance(value, dict):
        if value.get("kind") == "memory" and "memory_citation" in value:
            return True
        if value.get("family") == "memory" and "artifact_id" in value:
            return True
        return any(_holds_memory_reference(item) for item in value.values())
    if isinstance(value, list):
        return any(_holds_memory_reference(item) for item in value)
    return False


def _dream_inputs(value: Any, artifacts: set[tuple[str, str, int]], sources: set[tuple[str, str]]) -> bool:
    """Collect every pinned Artifact and Source; return whether an entry citation is pinned."""

    if isinstance(value, dict):
        if {"family", "artifact_id"} <= value.keys() and type(value.get("revision")) is int:
            artifacts.add((str(value["family"]), str(value["artifact_id"]), value["revision"]))
        if set(value) == {"source_type", "source_id"}:
            sources.add((str(value["source_type"]), str(value["source_id"])))
        cited = bool(value.get("memory_citations"))
        items = list(value.values())
    elif isinstance(value, list):
        cited, items = False, value
    else:
        return False
    for item in items:
        cited = _dream_inputs(item, artifacts, sources) or cited
    return cited


async def _dream_inputs_change(  # noqa: C901 - One check over each kind of pinned input.
    connection: AsyncConnection, scope_id: str, payload: Mapping[str, Any], *, cited: bool
) -> bool:
    """Whether conversion changes any digest or lineage pinned by an unfinished Dream run."""

    manifest = (payload.get("run") or {}).get("input_manifest")
    if isinstance(manifest, dict) and (
        "memory_citations" in manifest
        or any("memory_citations" in node or "current_entry_version_id" in node for node in manifest.get("nodes", ()))
    ):
        # Legacy Artifact digests include lineage.memory_citations even when empty.
        # Removing that field changes every pinned Artifact digest, without changing its stored body.
        return True
    artifacts: set[tuple[str, str, int]] = set()
    sources: set[tuple[str, str]] = set()
    if _dream_inputs([payload.get("request"), (payload.get("run") or {}).get("input_manifest")], artifacts, sources):
        return True
    for family, artifact_id, revision in sorted(artifacts):
        if family == "memory":
            return True
        identity = {"scope": scope_id, "family": family, "id": artifact_id, "revision": revision}
        where = "WHERE scope_id = :scope AND family = :family AND artifact_id = :id AND revision = :revision"
        if await connection.scalar(
            text(f"SELECT COUNT(*) FROM pc_artifact_lineage_artifacts {where} AND upstream_family = 'memory'"),  # noqa: S608
            identity,
        ):
            return True
        columns = "content, memory_citations" if cited else "content"
        row = (
            (await connection.execute(text(f"SELECT {columns} FROM pc_artifacts {where}"), identity))  # noqa: S608
            .mappings()
            .one_or_none()
        )
        if row is None:
            continue
        if row.get(LEGACY_CITATION_COLUMN) is not None and json.loads(bytes(row[LEGACY_CITATION_COLUMN])):
            return True
        if family == "handoff" and _holds_memory_reference(json.loads(bytes(row["content"]))):
            return True
    for source_type, source_id in sorted(sources):
        stored = await connection.scalar(
            text("SELECT payload FROM pc_sources WHERE scope_id = :scope AND source_type = :type AND source_id = :id"),
            {"scope": scope_id, "type": source_type, "id": source_id},
        )
        if stored is None:
            continue
        value = json.loads(bytes(stored)).get("value") or {}
        model = _WORK_MODELS.get(str((value.get("metadata") or {}).get("kind")))
        if model is None:
            continue
        content = json.loads(value["content"])
        if model is HandoffReceipt:
            # These unavailable addresses survive unchanged, so the Source digest stays pinned.
            content["unavailable_evidence"] = [
                item for item in content.get("unavailable_evidence", ()) if _collection_citation(item) is None
            ]
        if _holds_memory_reference(content):
            return True
    return False


def _without_entry_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop the always-empty entry citation fields from an executable Dream request and its manifest."""

    def without(value: Any, names: frozenset[str]) -> Any:
        return {name: item for name, item in value.items() if name not in names} if isinstance(value, dict) else value

    request = without(payload.get("request"), frozenset({"memory_citations"}))
    run = payload["run"]
    manifest = without(run.get("input_manifest"), frozenset({"memory_citations"}))
    if isinstance(manifest, dict) and isinstance(manifest.get("nodes"), list):
        node_fields = frozenset({"memory_citations", "current_entry_version_id"})
        manifest = {**manifest, "nodes": [without(node, node_fields) for node in manifest["nodes"]]}
    if run.get("input_manifest") is not None:
        run = {**run, "input_manifest": manifest}
    return {**payload, "request": request, "run": run}


async def _align_request_digest(
    context: _Context, connection: AsyncConnection, key: Mapping[str, Any], stored: str, request: Mapping[str, Any]
) -> None:
    """Keep an idempotent retry of a historical run matching once requests no longer carry entry citations.

    A request that cited legacy entries cannot be sent again, so it keeps its accepted digest.
    """

    if request.get("memory_citations"):
        return
    digest = CreateDreamRunRequest.model_validate({
        name: value for name, value in request.items() if name != "memory_citations"
    }).digest()
    if digest == stored:
        return
    context.count("dream_request_digests")
    if context.write:
        await connection.execute(
            text("UPDATE pc_dream_runs SET request_digest = :digest WHERE scope_id = :scope_id AND run_id = :run_id"),
            {**key, "digest": digest},
        )


async def _dream_runs(context: _Context, units: _Units) -> None:
    keys = await _keys(units, "SELECT scope_id, run_id FROM pc_dream_runs")

    async def convert(connection: AsyncConnection, key: Mapping[str, Any]) -> None:
        row = (
            await rows(
                connection,
                "pc_dream_runs",
                ("status", "request_digest", "payload"),
                "WHERE scope_id = :scope_id AND run_id = :run_id",
                **key,
            )
        )[0]
        payload = json.loads(bytes(row["payload"]))
        if row["status"] not in _TERMINAL_DREAM:
            cited = "pc_artifacts" in context.citation_tables
            if await _dream_inputs_change(connection, key["scope_id"], payload, cited=cited):
                raise ValueError(  # noqa: TRY003
                    "an unfinished Dream run pins evidence this migration rewrites; let it finish first"
                )
            current = _without_entry_fields(payload)
            if current == payload:
                return
            record = DreamRecord.model_validate(current)
            if record.request is None:
                raise ValueError("an unfinished Dream run has no request")  # noqa: TRY003
            context.count("dream_run_formats")
            if context.write:
                await connection.execute(
                    text(
                        "UPDATE pc_dream_runs SET payload = :payload, request_digest = :digest "
                        "WHERE scope_id = :scope_id AND run_id = :run_id"
                    ),
                    {
                        **key,
                        "payload": dump_model(record, kind="dream", name="record"),
                        "digest": record.request.digest(),
                    },
                )
            return
        if payload.get("request") is None:
            history = payload["run"].get("historical_data") or {}
            if history.get("format") == DREAM_HISTORY_FORMAT:
                await _align_request_digest(context, connection, key, row["request_digest"], history["request"])
            return
        run = payload["run"]
        history = {
            "format": DREAM_HISTORY_FORMAT,
            "request": payload["request"],
            "input_manifest": run.get("input_manifest"),
            "request_digest": row["request_digest"],
        }
        record = DreamRecord.model_validate({
            **payload,
            "request": None,
            "run": {**run, "input_manifest": None, "historical_data": history},
        })
        context.count("historical_dream_runs")
        if context.write:
            await connection.execute(
                text("UPDATE pc_dream_runs SET payload = :payload WHERE scope_id = :scope_id AND run_id = :run_id"),
                {**key, "payload": dump_model(record, kind="dream", name="record")},
            )
        await _align_request_digest(context, connection, key, row["request_digest"], history["request"])

    await _each(context, units, keys, lambda key: f"Dream run {key['scope_id']}/{key['run_id']}", convert)


async def _unsupported(context: _Context, units: _Units, tables: set[str]) -> None:
    async with units.unit() as connection:
        if "pc_artifact_candidate_heads" in tables:
            for row in await rows(
                connection, "pc_artifact_candidate_heads", ("scope_id", "candidate_id"), "WHERE family = 'memory'"
            ):
                context.errors.append(
                    f"Candidate {row['scope_id']}/{row['candidate_id']}: legacy Memory Candidate is unsupported"
                )
        if "pc_artifact_publications" in tables:
            for row in await rows(
                connection,
                "pc_artifact_publications",
                ("target_scope_id", "target_artifact_id"),
                "WHERE source_family = 'memory' OR target_family = 'memory'",
            ):
                context.errors.append(
                    f"publication {row['target_scope_id']}/{row['target_artifact_id']}: legacy Memory publication "
                    "is unsupported"
                )


async def _decision_applied(context: _Context, units: _Units, decision: CandidateDecision) -> bool:
    """A committed replacement lets the same decision file be reused for a rerun."""

    cited = "pc_artifact_candidate_versions" in context.citation_tables
    async with units.unit() as connection:
        found = await rows(
            connection,
            "pc_artifact_candidate_versions",
            ("artifact_refs", *((LEGACY_CITATION_COLUMN,) if cited else ())),
            "WHERE scope_id = :scope_id AND candidate_id = :candidate_id AND version = :version",
            scope_id=decision.scope_id,
            candidate_id=decision.candidate_id,
            version=decision.version,
        )
    if not found:
        return False
    citations = found[0].get(LEGACY_CITATION_COLUMN)
    return json.loads(bytes(found[0]["artifact_refs"])) == list(decision.artifact_refs) and (
        citations is None or json.loads(bytes(citations)) == []
    )


async def convert_references(
    collections: dict[tuple[str, str], LegacyCollection],
    decisions: dict[tuple[str, str, int], CandidateDecision],
    *,
    database: AsyncDatabase | None = None,
    connection: AsyncConnection | None = None,
) -> tuple[dict[str, int], tuple[str, ...]]:
    """Plan (with ``connection``) or apply (with ``database``) every legacy reference conversion."""

    units = _Units(database, connection)
    async with units.unit() as current:
        tables = await table_names(current)
        citation_tables = frozenset(await legacy_citation_columns(current, tables))
    context = _Context(collections, decisions, write=database is not None, citation_tables=citation_tables)
    await _unsupported(context, units, tables)
    if "pc_artifact_candidate_versions" in tables:
        await _candidates(context, units)
    if "pc_dream_runs" in tables:
        await _dream_runs(context, units)
    if context.errors and context.write:
        return context.counts, tuple(context.errors)
    if "pc_artifacts" in tables:
        await _artifact_memory_citations(context, units)
        await _handoffs(context, units)
        await _collection_lineage(context, units)
    if "pc_sources" in tables:
        await _work_sources(context, units)
    for key in sorted(set(decisions) - context.used_decisions):
        if not await _decision_applied(context, units, decisions[key]):
            context.errors.append(f"decision {key} matches no blocked Candidate evidence")
    context.counts["blocked_references"] = sum("supply a replace decision" in error for error in context.errors)
    return context.counts, tuple(context.errors)


async def drop_legacy_citation_columns(connection: AsyncConnection) -> None:
    """Remove the emptied legacy citation columns; every citation was converted and accepted first."""

    for table in await legacy_citation_columns(connection, await table_names(connection)):
        await connection.execute(text(f"ALTER TABLE {table} DROP COLUMN {LEGACY_CITATION_COLUMN}"))


async def drop_legacy_foreign_keys(connection: AsyncConnection) -> None:
    """Detach the retained legacy entry tables from public Artifact rows."""

    tables = await table_names(connection)
    pending = await legacy_foreign_keys(connection, tables)
    if not pending:
        return
    if connection.dialect.name == "mysql":
        for table in pending:
            for name in await legacy_entry_foreign_key_names(connection, table):
                await connection.execute(text(f"ALTER TABLE {table} DROP FOREIGN KEY {name}"))
        return
    # Removing a foreign key does not change SQLite's stored row format, so the
    # documented schema-text rewrite avoids copying both tables under live checks.
    version = int(await connection.scalar(text("PRAGMA schema_version")) or 0)
    await connection.execute(text("PRAGMA writable_schema = ON"))
    for table in pending:
        created = await connection.scalar(
            text("SELECT sql FROM sqlite_master WHERE type = 'table' AND name = :name"), {"name": table}
        )
        await connection.execute(
            text("UPDATE sqlite_master SET sql = :sql WHERE type = 'table' AND name = :name"),
            {"sql": _without_artifact_foreign_key(str(created), LEGACY_ENTRY_FOREIGN_KEYS[table]), "name": table},
        )
    await connection.execute(text(f"PRAGMA schema_version = {version + 1}"))
    await connection.execute(text("PRAGMA writable_schema = OFF"))


def _without_artifact_foreign_key(created: str, columns: tuple[str, ...]) -> str:
    marker = "FOREIGN KEY(" + ", ".join(columns) + ") REFERENCES pc_artifacts"
    start = created.find(marker)
    if start < 0:
        raise ValueError(f"unexpected legacy entry table definition: {created[:80]}")  # noqa: TRY003
    end = created.find(")", created.find("(", start + len(marker))) + 1
    tail = created[end:]
    for clause in (" ON DELETE RESTRICT", " ON UPDATE RESTRICT"):
        if tail.startswith(clause):
            tail = tail[len(clause) :]
            end += len(clause)
    prefix = created[:start].rstrip()
    if prefix.endswith(","):
        prefix = prefix[:-1]
    elif tail.lstrip().startswith(","):
        tail = tail.lstrip()[1:]
    return prefix + tail


async def remove_legacy_collections(
    database: AsyncDatabase, collections: dict[tuple[str, str], LegacyCollection]
) -> int:
    """Delete archived collections and their owned public rows; legacy entry tables stay unused.

    Collections may cite each other, so every collection's own lineage is
    removed before any collection row, independent of identifier order.
    """

    owned = "WHERE scope_id = :scope AND family = 'memory' AND artifact_id = :id"
    live: list[tuple[str, str]] = []
    for scope_id, memory_id in sorted(collections):
        identity = {"scope": scope_id, "id": memory_id}
        async with database.transaction() as connection:
            if not await connection.scalar(text(f"SELECT COUNT(*) FROM pc_artifacts {owned}"), identity):  # noqa: S608
                continue
            # The archive keeps the lineage and is compared only by revision bodies, so a rerun reuses it.
            await archive_collection(connection, scope_id, memory_id)
            for table in ("pc_artifact_lineage_sources", "pc_artifact_lineage_artifacts"):
                await connection.execute(text(f"DELETE FROM {table} {owned}"), identity)  # noqa: S608
        live.append((scope_id, memory_id))
    removed = 0
    for scope_id, memory_id in live:
        async with database.transaction() as connection:
            identity = {"scope": scope_id, "id": memory_id}
            remaining = await connection.scalar(
                text(
                    "SELECT COUNT(*) FROM pc_access_relationships WHERE resource_type = 'artifact' AND scope_id = :scope "
                    "AND family = 'memory' AND artifact_id = :id"
                ),
                identity,
            )
            if remaining:
                raise ValueError(f"{scope_id}/{memory_id}: legacy grants were not retargeted")  # noqa: TRY003
            for table in ("pc_artifact_tags", "pc_artifact_heads"):
                await connection.execute(text(f"DELETE FROM {table} {owned}"), identity)  # noqa: S608
            await connection.execute(
                text(f"DELETE FROM pc_access_owners {owned} AND owner_kind = 'artifact'"),  # noqa: S608
                identity,
            )
            await connection.execute(text(f"DELETE FROM pc_artifacts {owned}"), identity)  # noqa: S608
            removed += 1
    return removed


async def residual_issues(  # noqa: C901 - One flat list of independent residual checks.
    connection: AsyncConnection, tables: set[str], *, thorough: bool, collections_removed: bool = True
) -> list[str]:
    """Report public legacy objects; ``thorough`` also scans business payloads for typed legacy references.

    Before removal (``collections_removed=False``) the archived collections,
    their own lineage and the emptied legacy citation columns may still be
    public; every other reference must be gone.
    """

    issues = await atomic_memory_readiness_issues(connection, tables, collections_removed=collections_removed)
    if not thorough:
        return issues

    async def count(sql: str) -> int:
        return int(await connection.scalar(text(sql), {"historical": '%"request":null%'}) or 0)

    citation_tables = await legacy_citation_columns(connection, tables)
    cited = "(memory_citations IS NOT NULL AND LENGTH(memory_citations) > 2) OR "
    checks = {
        "pc_artifacts": (
            "SELECT COUNT(*) FROM pc_artifacts WHERE "  # noqa: S608
            + (cited if "pc_artifacts" in citation_tables else "")
            + "(family = 'handoff' AND CAST(content AS CHAR) LIKE '%memory_citation%')",
            "Artifacts still hold legacy Memory citations",
        ),
        "pc_artifact_candidate_versions": (
            "SELECT COUNT(*) FROM pc_artifact_candidate_versions WHERE "  # noqa: S608
            + (cited if "pc_artifact_candidate_versions" in citation_tables else "")
            + "CAST(artifact_refs AS CHAR) LIKE '%\"memory\"%'",
            "Candidates still hold legacy Memory references",
        ),
        "pc_dream_runs": (
            "SELECT COUNT(*) FROM pc_dream_runs WHERE status IN ('succeeded', 'failed') "
            "AND CAST(payload AS CHAR) NOT LIKE :historical",
            "terminal Dream runs still use the executable legacy format",
        ),
    }
    if "pc_dream_runs" in tables and await count(
        "SELECT COUNT(*) FROM pc_dream_runs WHERE status NOT IN ('succeeded', 'failed') "
        "AND CAST(payload AS CHAR) LIKE '%memory_citations%'"
    ):
        issues.append("unfinished Dream runs still carry legacy entry citation fields")
    for table, (sql, message) in checks.items():
        if table in tables and await count(sql):
            issues.append(message)
    if "pc_artifacts" in tables:
        for row in await rows(
            connection,
            "pc_artifacts",
            ("content",),
            "WHERE family = 'handoff' AND CAST(content AS CHAR) LIKE '%\"memory\"%'",
        ):
            if _holds_collection_citation(json.loads(bytes(row["content"]))):
                issues.append("Handoffs still cite whole legacy Memory collections")
                break
    if "pc_sources" in tables:
        for row in await rows(
            connection,
            "pc_sources",
            ("payload",),
            "WHERE source_type = 'content' AND CAST(payload AS CHAR) LIKE '%memory%'",
        ):
            value = json.loads(bytes(row["payload"]))["value"]
            model = _WORK_MODELS.get(str((value.get("metadata") or {}).get("kind")))
            if model is None:
                continue
            content = value["content"]
            current = json.loads(content)
            if model is HandoffReceipt:
                # Only unavailable addresses may describe an archived collection.
                current["unavailable_evidence"] = [
                    item for item in current.get("unavailable_evidence", ()) if _collection_citation(item) is None
                ]
            if "memory_citation" in content or _holds_collection_citation(current):
                issues.append("structured Work Sources still hold legacy Memory citations")
                break
    return issues


__all__ = [
    "DECISIONS_FORMAT",
    "DREAM_HISTORY_FORMAT",
    "CandidateDecision",
    "convert_references",
    "drop_legacy_citation_columns",
    "drop_legacy_foreign_keys",
    "load_decisions",
    "remove_legacy_collections",
    "residual_issues",
]
