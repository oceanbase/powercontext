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

"""Transactional custom Artifact projections, build coordination and rebuilds."""

from __future__ import annotations

import json
import math
import struct
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, insert, select, text, tuple_, update

from powercontext.artifacts import ArtifactRef
from powercontext.artifacts.indexes import ArtifactSearchDeclaration, TextIndex, VectorIndex
from powercontext.artifacts.search import ArtifactSearchFamilyNotFound, ArtifactSearchUnavailable
from powercontext.builtin.artifacts.memory.canonical import canonical_embedding, canonical_json
from powercontext.builtin.artifacts.memory.models import EmbeddingProfile
from powercontext.builtin.artifacts.search import TEXT_ANALYZER_VERSION, analyze_text
from powercontext.builtin.persistence.sqlite.profile import ensure_sqlite_vector_extension
from powercontext.builtin.persistence.tables import (
    ARTIFACT_FAMILY_SCHEMAS_TABLE as SCHEMAS,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_HEADS_TABLE as HEADS,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_INDEX_CHANGES_TABLE as CHANGES,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_INDEX_COVERAGE_TABLE as COVERAGE,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_SEARCH_STATES_TABLE as STATES,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_SEARCH_TEXT_TABLE as TEXTS,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_VECTOR_INDEXES_TABLE as DEFINITIONS,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_VECTOR_SPACES_TABLE as SPACES,
)
from powercontext.builtin.persistence.tables import (
    ARTIFACT_VECTORS_TABLE as VECTORS,
)

FORMAT = "float32-le-v1"
_PREVIOUS_TEXT_ANALYZER_VERSION = "jieba-0.42.1-words-v1"


def identity_json(value):
    # IDs, raw content and fragment locators must not be Unicode-normalized by hashing.
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


def digest(value):
    return sha256(identity_json(value)).hexdigest()


def search_declaration(durable):
    if durable.get("search") is not None:
        return ArtifactSearchDeclaration.model_validate(durable["search"])
    fields = durable.get("search_fields", [])
    if fields:
        return ArtifactSearchDeclaration(
            version="legacy-fts-v1",
            indexes=(TextIndex(name="lexical", projection_version="1", fields=tuple(fields)),),
            modes=("text",),
            default_mode="text",
        )
    return None


def key(table, scope, family):
    return (table.c.scope_id == scope) & (table.c.family == family)


async def lock_state(connection, scope, family):
    values = {
        "scope_id": scope,
        "family": family,
        "build_id": None,
        "state": "pending",
        "manifest": {},
        "cleanup_targets": [],
        "start_seq": 0,
        "applied_seq": 0,
        "change_seq": 0,
    }
    if connection.dialect.name == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as dialect_insert

        stmt = dialect_insert(STATES).values(**values).on_conflict_do_nothing(index_elements=["scope_id", "family"])
    else:
        from sqlalchemy.dialects.mysql import insert as dialect_insert

        stmt = dialect_insert(STATES).values(**values).on_duplicate_key_update(family=family)
    await connection.execute(stmt)
    # This is a real write on SQLite, and the same row lock as SELECT FOR UPDATE on MySQL.
    await connection.execute(update(STATES).where(key(STATES, scope, family)).values(change_seq=STATES.c.change_seq))
    return (
        (await connection.execute(select(STATES).where(key(STATES, scope, family)).with_for_update())).mappings().one()
    )


async def read_head(connection, scope, family, artifact_id, *, for_update=False):
    statement = select(HEADS).where(key(HEADS, scope, family), HEADS.c.artifact_id == artifact_id)
    if for_update:
        statement = statement.with_for_update()
    return (await connection.execute(statement)).mappings().one_or_none()


async def record_change(connection, scope, family, artifact_id, schema_version, operation):
    state = await lock_state(connection, scope, family)
    head = await read_head(connection, scope, family, artifact_id)
    sequence = state["change_seq"] + 1
    await connection.execute(update(STATES).where(key(STATES, scope, family)).values(change_seq=sequence))
    await connection.execute(
        insert(CHANGES).values(
            scope_id=scope,
            family=family,
            seq=sequence,
            artifact_id=artifact_id,
            revision=None if head is None else head["revision"],
            schema_version=schema_version,
            governance_generation=0 if head is None else head["governance_generation"],
            operation=operation,
        )
    )
    if state["state"] == "ready":
        await connection.execute(update(STATES).where(key(STATES, scope, family)).values(applied_seq=sequence))
    await prune_changes(connection, scope, family)
    return sequence


async def prune_changes(connection, scope, family):
    applied = await connection.scalar(select(STATES.c.applied_seq).where(key(STATES, scope, family)))
    await connection.execute(delete(CHANGES).where(key(CHANGES, scope, family), CHANGES.c.seq <= applied))


async def register_definition(connection, family, version, index):
    spec = index.model_dump(mode="json")
    if isinstance(index, TextIndex):
        spec["analyzer_version"] = TEXT_ANALYZER_VERSION
    space = None
    if isinstance(index, VectorIndex):
        fingerprint = sha256(canonical_json(index.profile.model_dump(mode="json"))).hexdigest()
        backend = connection.dialect.name
        space = uuid4().hex
        values = {
            "space_id": space,
            "profile_fingerprint": fingerprint,
            "profile_json": index.profile.model_dump(mode="json"),
            "dimension": index.profile.dimension,
            "distance": index.profile.distance,
            "normalization": index.profile.normalization,
            "storage_format_version": FORMAT,
            "backend": backend,
            "physical_table": "pc_artifact_vectors" if backend == "sqlite" else f"pc_av_{space}",
            "provision_state": "ready" if backend == "sqlite" else "declared",
        }
        if backend == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as space_insert

            statement = (
                space_insert(SPACES)
                .values(**values)
                .on_conflict_do_nothing(index_elements=["backend", "profile_fingerprint", "storage_format_version"])
            )
        else:
            from sqlalchemy.dialects.mysql import insert as space_insert

            statement = space_insert(SPACES).values(**values).on_duplicate_key_update(backend=backend)
        await connection.execute(statement)
        space_row = (
            (
                await connection.execute(
                    select(SPACES)
                    .where(
                        SPACES.c.backend == backend,
                        SPACES.c.profile_fingerprint == fingerprint,
                        SPACES.c.storage_format_version == FORMAT,
                    )
                    .with_for_update()
                )
            )
            .mappings()
            .one()
        )
        if space_row["profile_json"] != index.profile.model_dump(mode="json"):
            raise ValueError("vector space fingerprint collision")  # noqa: TRY003
        space = space_row["space_id"]
    identity = {
        "family": family,
        "schema_version": version,
        "index_name": index.name,
        "projection_version": index.projection_version,
        "space_id": space,
    }
    existing = (
        (
            await connection.execute(
                select(DEFINITIONS)
                .where(*(DEFINITIONS.c[name] == value for name, value in identity.items()))
                .order_by(DEFINITIONS.c.definition_fingerprint)
            )
        )
        .mappings()
        .first()
    )
    fingerprint = digest(spec)
    exact = (
        (
            await connection.execute(
                select(DEFINITIONS).where(DEFINITIONS.c.index_def_id == digest({**identity, "spec": spec}))
            )
        )
        .mappings()
        .first()
    )
    if exact is not None:
        if any(exact[name] != value for name, value in identity.items()) or exact["projection_spec"] != spec:
            raise ValueError("index definition identity collision")  # noqa: TRY003
        return dict(exact)
    if existing is not None:
        prior_spec = {k: v for k, v in existing["projection_spec"].items() if k != "analyzer_version"}
        current_spec = {k: v for k, v in spec.items() if k != "analyzer_version"}
        if prior_spec != current_spec:
            raise ValueError("index projection version cannot be redefined")  # noqa: TRY003
        if existing["definition_fingerprint"] == fingerprint:
            return dict(existing)
    row = dict(
        index_def_id=digest({**identity, "spec": spec}),
        **identity,
        source_kind="text" if isinstance(index, TextIndex) else index.source,
        projection_spec=spec,
        definition_fingerprint=fingerprint,
    )
    await connection.execute(insert(DEFINITIONS).values(**row))
    return row


def parse_index(definition):
    spec = {k: v for k, v in definition["projection_spec"].items() if k != "analyzer_version"}
    return (TextIndex if spec["kind"] == "text" else VectorIndex).model_validate(spec)


def text_analyzer_current(definition):
    """Recognize supported text encodings without reinterpreting historical definitions."""
    spec = definition["projection_spec"]
    if spec["kind"] != "text":
        return True
    version = spec.get("analyzer_version")
    if version not in {_PREVIOUS_TEXT_ANALYZER_VERSION, TEXT_ANALYZER_VERSION}:
        raise ValueError("custom Artifact text analyzer version is unsupported")  # noqa: TRY003
    return version == TEXT_ANALYZER_VERSION


def require_current_text_analyzer(definition):
    try:
        current = text_analyzer_current(definition)
    except ValueError as error:
        raise ArtifactSearchUnavailable(definition["family"], "text analyzer version is unsupported") from error
    if not current:
        raise ArtifactSearchUnavailable(definition["family"], "text analyzer upgrade is required")


async def _manifest_definitions(connection, manifest):
    identities = {entry["index_def_id"] for _, entry in entries(manifest)}
    definitions = {}
    ordered = sorted(identities)
    for offset in range(0, len(ordered), 100):
        rows = (
            await connection.execute(
                select(DEFINITIONS).where(DEFINITIONS.c.index_def_id.in_(ordered[offset : offset + 100]))
            )
        ).mappings()
        definitions.update((row["index_def_id"], dict(row)) for row in rows)
    if definitions.keys() != identities:
        raise ValueError("custom Artifact search definition is missing")  # noqa: TRY003
    return definitions


async def custom_artifact_text_analyzers_current(connection):
    """Inspect only definitions referenced by current builds; obsolete evidence remains valid."""
    current = True
    async for state in _search_states(connection):
        for definition in (await _manifest_definitions(connection, state["manifest"])).values():
            current = text_analyzer_current(definition) and current
    return current


async def _search_states(connection):
    after = None
    while True:
        statement = select(STATES).order_by(STATES.c.scope_id, STATES.c.family).limit(100)
        if after is not None:
            statement = statement.where(tuple_(STATES.c.scope_id, STATES.c.family) > after)
        rows = (await connection.execute(statement)).mappings().all()
        if not rows:
            return
        for row in rows:
            yield row
        after = (rows[-1]["scope_id"], rows[-1]["family"])


async def upgrade_custom_artifact_text_analyzers(connection):
    """Upgrade materialized text in offline maintenance, preserving vector data and build progress."""
    async for snapshot in _search_states(connection):
        scope, family = snapshot["scope_id"], snapshot["family"]
        state = await lock_state(connection, scope, family)
        manifest = deepcopy(state["manifest"])
        definitions = await _manifest_definitions(connection, manifest)
        changed = False
        for schema, entry in entries(manifest):
            previous = definitions[entry["index_def_id"]]
            if text_analyzer_current(previous):
                continue
            definition = await register_definition(connection, family, schema, parse_index(previous))
            await _upgrade_materialized_text(connection, scope, family, state["build_id"], previous, definition)
            entry["index_def_id"] = definition["index_def_id"]
            changed = True
        if changed:
            # A validator started before maintenance cannot publish using the old manifest.
            await connection.execute(
                update(STATES).where(key(STATES, scope, family)).values(manifest=manifest, validation_token=None)
            )


async def _upgrade_materialized_text(connection, scope, family, build_id, previous, definition):
    from powercontext.builtin.persistence.custom_artifacts import custom_artifact_repository

    repository = custom_artifact_repository(family)
    publisher = ArtifactProjectionPublisher(None)
    # An interrupted build can retain older revisions until its change log is replayed.
    # Re-encode exactly those revisions, without advancing cursors or claiming new coverage.
    for table, primary in ((TEXTS, TEXTS.c.text_id), (COVERAGE, COVERAGE.c.artifact_id)):
        after = ""
        while True:
            rows = (
                (
                    await connection.execute(
                        select(table)
                        .where(
                            projection_key(table, scope, family, build_id, previous["index_def_id"]), primary > after
                        )
                        .order_by(primary)
                        .limit(100)
                    )
                )
                .mappings()
                .all()
            )
            if not rows:
                break
            for row in rows:
                artifact = await repository.get(
                    connection,
                    scope,
                    ArtifactRef(family=family, artifact_id=row["artifact_id"], revision=row["revision"]),
                )
                if artifact.content.schema_version != definition["schema_version"]:
                    raise ValueError("custom Artifact text projection schema differs from its authority")  # noqa: TRY003
                prepared = await publisher.prepare_index(definition, artifact.content.content)
                values = {"index_def_id": definition["index_def_id"]}
                if table is TEXTS:
                    fragment = prepared.fragments[0]
                    values.update(
                        text_id=digest((
                            scope,
                            family,
                            row["artifact_id"],
                            row["revision"],
                            row["schema_version"],
                            definition["index_def_id"],
                            build_id,
                            row["fragment_id"],
                        )),
                        searchable_text=fragment["searchable_text"],
                        input_hash=fragment["input_hash"],
                    )
                else:
                    values["projection_input_hash"] = prepared.input_hash
                await connection.execute(
                    update(table)
                    .where(
                        projection_key(table, scope, family, build_id, previous["index_def_id"]),
                        primary == row[primary.name],
                    )
                    .values(**values)
                )
            after = rows[-1][primary.name]


async def target_manifest(connection, scope, family, *, rebuild=False):  # noqa: C901 - Initial build and repair share one fenced state transition.
    """Initialize a fixed target or restart repair from its persisted declaration."""
    state = await lock_state(connection, scope, family)
    manifest = deepcopy(state["manifest"])
    if manifest:
        definitions = await _manifest_definitions(connection, manifest)
        if not rebuild:
            for definition in definitions.values():
                require_current_text_analyzer(definition)
            return manifest
        for schema, entry in entries(manifest):
            definition = definitions[entry["index_def_id"]]
            if not text_analyzer_current(definition):
                definition = await register_definition(connection, family, schema, parse_index(definition))
                entry["index_def_id"] = definition["index_def_id"]
            entry.update(scan_cursor=None, scan_complete=False)
    else:
        rows = (
            (
                await connection.execute(
                    select(SCHEMAS).where(SCHEMAS.c.family == family).order_by(SCHEMAS.c.schema_version)
                )
            )
            .mappings()
            .all()
        )
        if not rows:
            raise ArtifactSearchFamilyNotFound(family)
        manifest = {"schemas": {}, "config": None, "default_schema": None}
        for row in rows:
            declaration = search_declaration(row["declaration"])
            if declaration is None:
                continue
            schema = row["schema_version"]
            if manifest["config"] is None:
                manifest["config"] = declaration.model_dump(mode="json")
                manifest["default_schema"] = schema
            indexes = {}
            for index in declaration.indexes:
                definition = await register_definition(connection, family, schema, index)
                indexes[index.name] = {
                    "index_def_id": definition["index_def_id"],
                    "space_id": definition["space_id"],
                    "scan_cursor": None,
                    "scan_complete": False,
                }
            manifest["schemas"][schema] = {"indexes": indexes}
    targets = list(state["cleanup_targets"])
    if state["build_id"] is not None:
        for _, entry in entries(state["manifest"]):
            target = {
                "build_id": state["build_id"],
                "index_def_id": entry["index_def_id"],
                "space_id": entry["space_id"],
            }
            if target not in targets:
                targets.append(target)
    await connection.execute(
        update(STATES)
        .where(key(STATES, scope, family))
        .values(
            build_id=uuid4().hex,
            state="cleaning" if targets else "building",
            manifest=manifest,
            cleanup_targets=targets,
            validation_token=None,
            start_seq=state["change_seq"],
            applied_seq=state["change_seq"],
        )
    )
    await prune_changes(connection, scope, family)
    return manifest


def entries(manifest):
    return tuple(
        (schema, entry)
        for schema, config in manifest.get("schemas", {}).items()
        for entry in config["indexes"].values()
    )


def vector_bytes(values, profile):
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values):
        raise ValueError("vectors must contain finite numbers")  # noqa: TRY003
    vector = canonical_embedding(values, dimension=profile.dimension, normalization=profile.normalization)
    try:
        encoded = struct.pack(f"<{len(vector)}f", *vector)
    except (OverflowError, struct.error) as error:
        raise ValueError("vector exceeds float32 range") from error  # noqa: TRY003
    if not all(math.isfinite(v) for v in struct.unpack(f"<{len(vector)}f", encoded)):
        raise ValueError("vector exceeds float32 range")  # noqa: TRY003
    return encoded


def index_parts(index, content):
    if isinstance(index, VectorIndex) and index.fragments is not None:
        parts = content[index.fragments.array_field]
        if not isinstance(parts, list):
            raise ValueError("fragment projection requires a persistent array")  # noqa: TRY003
    else:
        parts = [content]
    seen = set()
    for offset, part in enumerate(parts):
        fragment_id = "__whole__"
        locator = {"fields": list(index.fields)}
        if isinstance(index, VectorIndex) and index.fragments is not None:
            fragment_id = part[index.fragments.id_field]
            if not isinstance(fragment_id, str) or not 1 <= len(fragment_id) <= 128 or fragment_id == "__whole__":
                raise ValueError("fragment identity must be a bounded persistent string")  # noqa: TRY003
            locator = {"array": index.fragments.array_field, "id": fragment_id, "offset": offset}
        if fragment_id in seen:
            raise ValueError("fragment identities must be unique")  # noqa: TRY003
        seen.add(fragment_id)
        yield part, fragment_id, locator


def content_vector(index, part):
    if EmbeddingProfile.model_validate(part[index.profile_field]) != index.profile:
        raise ValueError("persistent vector profile differs from the declared index")  # noqa: TRY003
    return vector_bytes(part[index.vector_field], index.profile)


@dataclass(frozen=True)
class PreparedIndex:
    definition: dict[str, Any]
    fragments: tuple[dict[str, Any], ...]
    input_hash: str


@dataclass(frozen=True)
class PreparedArtifactIndexes:
    build_id: str | None
    content_hash: str
    projections: dict[str, PreparedIndex]


class ArtifactProjectionPublisher:
    """Prepare without a transaction; publish only under the build coordinator."""

    def __init__(self, database, embedding_model=None):
        self.database = database
        self.embedding_model = embedding_model

    async def prepare_index(self, definition, content):
        require_current_text_analyzer(definition)
        index = parse_index(definition)
        fragments = []
        texts = []
        for part, fragment_id, locator in index_parts(index, content):
            value = {"fragment_id": fragment_id, "fragment_locator": identity_json(locator).decode()}
            if isinstance(index, TextIndex) or index.source == "text":
                fields = [part[field] for field in index.fields]
                if any(not isinstance(field, str) for field in fields):
                    raise ValueError("text projection requires strings")  # noqa: TRY003
                original = "\n".join(fields)
                if isinstance(index, TextIndex):
                    value["searchable_text"] = analyze_text(original)
                else:
                    texts.append(original)
            else:
                value["embedding"] = content_vector(index, part)
            value["input_hash"] = digest({"projection": definition["definition_fingerprint"], "part": part})
            fragments.append(value)
        if texts:
            model = self.embedding_model
            if model is None or model.profile != index.profile:
                raise ArtifactSearchUnavailable(
                    definition["family"], "embedding resource unavailable for declared profile"
                )
            result = await model.embed(tuple(texts))
            if len(result.vectors) != len(texts):
                raise ValueError("embedding result must match the prepared input count")  # noqa: TRY003
            for fragment, vector in zip(fragments, result.vectors, strict=True):
                fragment["embedding"] = vector_bytes(vector, index.profile)
        return PreparedIndex(
            definition,
            tuple(fragments),
            digest({"projection": definition["definition_fingerprint"], "content": content}),
        )

    async def prepare(self, scope, family, schema_version, content):
        content = deepcopy(content)
        async with self.database.transaction(consistent_snapshot=True) as connection:
            state = (await connection.execute(select(STATES).where(key(STATES, scope, family)))).mappings().first()
            if state is None or state["state"] != "ready":
                return PreparedArtifactIndexes(None, digest(content), {})
            definitions = {}
            for schema, entry in entries(state["manifest"]):
                if schema == schema_version:
                    definitions[entry["index_def_id"]] = dict(
                        (
                            await connection.execute(
                                select(DEFINITIONS).where(DEFINITIONS.c.index_def_id == entry["index_def_id"])
                            )
                        )
                        .mappings()
                        .one()
                    )
        projections = {}
        try:
            for definition in definitions.values():
                require_current_text_analyzer(definition)
            for identity, definition in definitions.items():
                projections[identity] = await self.prepare_index(definition, content)
        except Exception:
            # A rebuild may have suspended this index while encoding was in flight.
            # Content writes then require only the durable change log.
            async with self.database.transaction(consistent_snapshot=True) as connection:
                current = (await connection.execute(select(STATES).where(key(STATES, scope, family)))).mappings().one()
            if current["state"] == "ready":
                raise
            return PreparedArtifactIndexes(None, digest(content), {})
        return PreparedArtifactIndexes(state["build_id"], digest(content), projections)

    async def validate_prepared(self, connection, scope, family, schema_version, content, prepared):
        state = await lock_state(connection, scope, family)
        if state["state"] != "ready":
            durable = await connection.scalar(
                select(SCHEMAS.c.declaration).where(
                    SCHEMAS.c.family == family, SCHEMAS.c.schema_version == schema_version
                )
            )
            declaration = None if durable is None else search_declaration(durable)
            if declaration is not None:
                # Resource-independent content invariants hold even while projections are suspended.
                for index in declaration.indexes:
                    if isinstance(index, VectorIndex):
                        for part, _, _ in index_parts(index, content):
                            if index.source == "content":
                                content_vector(index, part)
            return state
        expected = {entry["index_def_id"] for schema, entry in entries(state["manifest"]) if schema == schema_version}
        if prepared is None:
            if expected:
                raise ValueError("ready Artifact indexes must be prepared before the write transaction")  # noqa: TRY003
        elif (
            prepared.build_id != state["build_id"]
            or prepared.content_hash != digest(content)
            or set(prepared.projections) != expected
        ):
            raise ValueError("Artifact content or search build changed; prepare again before committing")  # noqa: TRY003
        return state

    async def publish(self, connection, scope, artifact, prepared):
        family, identity = artifact.family, artifact.artifact_id
        state = await lock_state(connection, scope, family)
        head = await read_head(connection, scope, family, identity, for_update=True)
        if state["state"] == "ready":
            if head["lifecycle_state"] == "active":
                await self.validate_prepared(
                    connection, scope, family, artifact.content.schema_version, artifact.content.content, prepared
                )
            for _, entry in entries(state["manifest"]):
                projection = (
                    None
                    if prepared is None or head["lifecycle_state"] != "active"
                    else prepared.projections.get(entry["index_def_id"])
                )
                await self.replace(
                    connection,
                    scope,
                    family,
                    identity,
                    artifact.revision,
                    head["governance_generation"],
                    state["build_id"],
                    entry["index_def_id"],
                    projection,
                )
        await record_change(connection, scope, family, identity, artifact.content.schema_version, "upsert")

    async def replace(
        self, connection, scope, family, artifact_id, revision, governance, build_id, index_def_id, prepared
    ):
        state = await lock_state(connection, scope, family)
        if (
            state["build_id"] != build_id
            or state["state"] not in {"ready", "building"}
            or state["validation_token"] is not None
        ):
            raise ValueError("projection build is not writable")  # noqa: TRY003
        if index_def_id not in {entry["index_def_id"] for _, entry in entries(state["manifest"])}:
            raise ValueError("projection definition is not in the current build")  # noqa: TRY003
        definition = dict(
            (await connection.execute(select(DEFINITIONS).where(DEFINITIONS.c.index_def_id == index_def_id)))
            .mappings()
            .one()
        )
        index = parse_index(definition)
        table = await projection_table(connection, definition)
        await connection.execute(
            delete(table).where(
                projection_key(table, scope, family, build_id, index_def_id), table.c.artifact_id == artifact_id
            )
        )
        await connection.execute(
            delete(COVERAGE).where(
                projection_key(COVERAGE, scope, family, build_id, index_def_id), COVERAGE.c.artifact_id == artifact_id
            )
        )
        if prepared is None:
            return
        if prepared.definition != definition:
            raise ValueError("prepared definition does not match its index")  # noqa: TRY003
        schema = definition["schema_version"]
        rows = []
        for fragment in prepared.fragments:
            identity = (
                scope,
                family,
                artifact_id,
                revision,
                schema,
                definition["index_def_id"],
                build_id,
                fragment["fragment_id"],
            )
            row = dict(
                scope_id=scope,
                family=family,
                artifact_id=artifact_id,
                revision=revision,
                schema_version=schema,
                index_def_id=definition["index_def_id"],
                build_id=build_id,
                governance_generation=governance,
                **fragment,
            )
            if isinstance(index, VectorIndex):
                row.update(
                    vector_id=digest(identity), space_id=definition["space_id"], dimension=index.profile.dimension
                )
                if connection.dialect.name != "sqlite":
                    row["embedding"] = list(struct.unpack(f"<{index.profile.dimension}f", row["embedding"]))
            else:
                row["text_id"] = digest(identity)
            rows.append(row)
        if rows:
            await connection.execute(insert(table), rows)
        await connection.execute(
            insert(COVERAGE).values(
                scope_id=scope,
                family=family,
                index_def_id=index_def_id,
                build_id=build_id,
                artifact_id=artifact_id,
                revision=revision,
                schema_version=schema,
                fragment_count=len(rows),
                projection_input_hash=prepared.input_hash,
                governance_generation=governance,
            )
        )


def projection_key(table, scope, family, build_id, index_def_id):
    return key(table, scope, family) & (table.c.build_id == build_id) & (table.c.index_def_id == index_def_id)


async def projection_table(connection, definition):
    if definition["space_id"] is None:
        return TEXTS
    space = (
        (await connection.execute(select(SPACES).where(SPACES.c.space_id == definition["space_id"]))).mappings().one()
    )
    if space["provision_state"] != "ready":
        raise ArtifactSearchUnavailable(definition["family"], "vector space preparation is incomplete")
    if connection.dialect.name == "sqlite":
        return VECTORS
    from powercontext.builtin.persistence.artifact_vector_space import native_vector_table

    try:
        return native_vector_table(space)
    except ValueError as error:
        raise ArtifactSearchUnavailable(definition["family"], "registered vector space is incompatible") from error


class ArtifactIndexBuilder:
    """Resume cleanup, scans and replay while the complete family search is unavailable."""

    def __init__(self, database, embedding_model=None):
        self.database = database
        self.publisher = ArtifactProjectionPublisher(database, embedding_model)

    async def refresh(self, scope, family, *, rebuild=False):
        if not rebuild:
            async with self.database.transaction(consistent_snapshot=True) as connection:
                manifest = await connection.scalar(select(STATES.c.manifest).where(key(STATES, scope, family)))
                if manifest:
                    for definition in (await _manifest_definitions(connection, manifest)).values():
                        require_current_text_analyzer(definition)
                    return manifest
        async with self.database.transaction() as connection:
            return await target_manifest(connection, scope, family, rebuild=rebuild)

    async def clean(self, scope, family, build_id, *, limit=1000):
        """Delete a bounded batch, fenced by the current build and its coordinator lock."""
        async with self.database.transaction() as connection:
            state = await lock_state(connection, scope, family)
            if state["build_id"] != build_id or state["state"] != "cleaning":
                return False
            targets = list(state["cleanup_targets"])
            remaining = limit
            while targets and remaining:
                target = targets[0]
                definition = (
                    (
                        await connection.execute(
                            select(DEFINITIONS).where(DEFINITIONS.c.index_def_id == target["index_def_id"])
                        )
                    )
                    .mappings()
                    .one()
                )
                tables = [COVERAGE]
                # An unprovisioned space has never accepted projection rows.
                if (
                    target["space_id"] is None
                    or await connection.scalar(
                        select(SPACES.c.provision_state).where(SPACES.c.space_id == target["space_id"])
                    )
                    == "ready"
                ):
                    tables.insert(0, await projection_table(connection, definition))
                complete = True
                for table in tables:
                    where = projection_key(table, scope, family, target["build_id"], target["index_def_id"])
                    primary = table.c.artifact_id if table is COVERAGE else next(iter(table.primary_key.columns))
                    ids = (await connection.execute(select(primary).where(where).limit(remaining))).scalars().all()
                    if ids:
                        await connection.execute(delete(table).where(where, primary.in_(ids)))
                        remaining -= len(ids)
                    if not remaining:
                        complete = False
                        break
                if complete:
                    targets.pop(0)
            await connection.execute(
                update(STATES)
                .where(key(STATES, scope, family))
                .values(cleanup_targets=targets, state="cleaning" if targets else "building")
            )
            return not targets

    async def build(self, scope, family):  # noqa: C901 - Resume ordered cleanup, scans, replay and validation.
        async with self.database.transaction(consistent_snapshot=True) as connection:
            state = (await connection.execute(select(STATES).where(key(STATES, scope, family)))).mappings().one()
            for definition in (await _manifest_definitions(connection, state["manifest"])).values():
                require_current_text_analyzer(definition)
        if state["state"] == "ready":
            return True
        if not state["manifest"]:
            return False
        build_id = state["build_id"]
        if state["state"] == "cleaning" and not await self.clean(scope, family, build_id):
            return False
        # Reclaim an interrupted validator. Any previous validator must still present
        # its own token at commit, so it cannot publish after this recovery.
        async with self.database.transaction() as connection:
            current = await lock_state(connection, scope, family)
            if current["build_id"] != build_id or current["state"] != "building":
                return False
            await connection.execute(update(STATES).where(key(STATES, scope, family)).values(validation_token=None))
            manifest = current["manifest"]
        for _, entry in entries(manifest):
            if entry["scan_complete"]:
                continue
            cursor = entry["scan_cursor"] or ""
            while True:
                async with self.database.transaction(consistent_snapshot=True) as connection:
                    identities = (
                        (
                            await connection.execute(
                                select(HEADS.c.artifact_id)
                                .where(key(HEADS, scope, family), HEADS.c.artifact_id > cursor)
                                .order_by(HEADS.c.artifact_id)
                                .limit(100)
                            )
                        )
                        .scalars()
                        .all()
                    )
                if not identities:
                    async with self.database.transaction() as connection:
                        current = await lock_state(connection, scope, family)
                        if not self._writable(current, build_id):
                            return False
                        progress = deepcopy(current["manifest"])
                        for _, item in entries(progress):
                            if item["index_def_id"] == entry["index_def_id"]:
                                item["scan_complete"] = True
                        await connection.execute(
                            update(STATES).where(key(STATES, scope, family)).values(manifest=progress)
                        )
                    break
                for identity in identities:
                    if not await self._project_current(
                        scope, family, build_id, entry["index_def_id"], identity, cursor=identity
                    ):
                        return False
                    cursor = identity
        for _ in range(8):
            if not await self._catch_up(scope, family, build_id):
                return False
            if await self._validate_and_publish(scope, family, build_id):
                return True
        return False

    @staticmethod
    def _writable(state, build_id):
        return state["build_id"] == build_id and state["state"] == "building" and state["validation_token"] is None

    async def _project_current(self, scope, family, build_id, index_def_id, identity, *, cursor=None):
        from powercontext.builtin.persistence.custom_artifacts import custom_artifact_repository

        repository = custom_artifact_repository(family)
        for _ in range(8):
            async with self.database.transaction(consistent_snapshot=True) as connection:
                state = (await connection.execute(select(STATES).where(key(STATES, scope, family)))).mappings().one()
                if not self._writable(state, build_id):
                    return False
                definition = dict(
                    (await connection.execute(select(DEFINITIONS).where(DEFINITIONS.c.index_def_id == index_def_id)))
                    .mappings()
                    .one()
                )
                head = await read_head(connection, scope, family, identity)
                artifact = None
                if head is not None and head["lifecycle_state"] == "active":
                    artifact = await repository.get(
                        connection, scope, ArtifactRef(family=family, artifact_id=identity, revision=head["revision"])
                    )
            prepared = None
            if artifact is not None and artifact.content.schema_version == definition["schema_version"]:
                prepared = await self.publisher.prepare_index(definition, artifact.content.content)
            async with self.database.transaction() as connection:
                current = await lock_state(connection, scope, family)
                if not self._writable(current, build_id):
                    return False
                if await read_head(connection, scope, family, identity, for_update=True) != head:
                    continue
                await self.publisher.replace(
                    connection,
                    scope,
                    family,
                    identity,
                    0 if head is None else head["revision"],
                    0 if head is None else head["governance_generation"],
                    build_id,
                    index_def_id,
                    prepared,
                )
                if cursor is not None:
                    progress = deepcopy(current["manifest"])
                    for _, entry in entries(progress):
                        if entry["index_def_id"] == index_def_id:
                            entry["scan_cursor"] = max(cursor, entry["scan_cursor"] or "")
                    await connection.execute(update(STATES).where(key(STATES, scope, family)).values(manifest=progress))
            return True
        return False

    async def _catch_up(self, scope, family, build_id):
        while True:
            async with self.database.transaction(consistent_snapshot=True) as connection:
                state = (await connection.execute(select(STATES).where(key(STATES, scope, family)))).mappings().one()
                if not self._writable(state, build_id):
                    return False
                changes = (
                    (
                        await connection.execute(
                            select(CHANGES)
                            .where(key(CHANGES, scope, family), CHANGES.c.seq > state["applied_seq"])
                            .order_by(CHANGES.c.seq)
                            .limit(100)
                        )
                    )
                    .mappings()
                    .all()
                )
            if not changes:
                return True
            for change in changes:
                for _, entry in entries(state["manifest"]):
                    if not await self._project_current(
                        scope, family, build_id, entry["index_def_id"], change["artifact_id"]
                    ):
                        return False
                async with self.database.transaction() as connection:
                    current = await lock_state(connection, scope, family)
                    if not self._writable(current, build_id):
                        return False
                    await connection.execute(
                        update(STATES)
                        .where(key(STATES, scope, family))
                        .values(applied_seq=max(change["seq"], current["applied_seq"]))
                    )
                    await prune_changes(connection, scope, family)

    async def _validate_and_publish(self, scope, family, build_id):
        token = uuid4().hex
        async with self.database.transaction() as connection:
            state = await lock_state(connection, scope, family)
            if not self._writable(state, build_id) or state["applied_seq"] != state["change_seq"]:
                return False
            if not all(entry["scan_complete"] for _, entry in entries(state["manifest"])):
                return False
            manifest, sequence = state["manifest"], state["change_seq"]
            await connection.execute(update(STATES).where(key(STATES, scope, family)).values(validation_token=token))
        try:
            async with self.database.transaction(consistent_snapshot=True) as connection:
                valid = await self._coverage_valid(connection, scope, family, build_id, manifest)
            if not valid:
                return False
            async with self.database.transaction() as connection:
                state = await lock_state(connection, scope, family)
                if (
                    state["build_id"] != build_id
                    or state["state"] != "building"
                    or state["validation_token"] != token
                    or state["change_seq"] != sequence
                    or state["manifest"] != manifest
                ):
                    return False
                await connection.execute(
                    update(STATES).where(key(STATES, scope, family)).values(state="ready", validation_token=None)
                )
                await prune_changes(connection, scope, family)
            return True
        finally:
            async with self.database.transaction() as connection:
                await lock_state(connection, scope, family)
                await connection.execute(
                    update(STATES)
                    .where(
                        key(STATES, scope, family), STATES.c.build_id == build_id, STATES.c.validation_token == token
                    )
                    .values(validation_token=None)
                )

    async def _coverage_valid(self, connection, scope, family, build_id, manifest):  # noqa: C901 - Keep the complete projection or publication invariant together.
        from powercontext.builtin.persistence.custom_artifacts import custom_artifact_repository

        repository = custom_artifact_repository(family)
        heads = (
            (
                await connection.execute(
                    select(HEADS).where(key(HEADS, scope, family), HEADS.c.lifecycle_state == "active")
                )
            )
            .mappings()
            .all()
        )
        artifacts = {}
        for head in heads:
            artifact = await repository.get(
                connection,
                scope,
                ArtifactRef(family=family, artifact_id=head["artifact_id"], revision=head["revision"]),
            )
            artifacts[head["artifact_id"]] = (head, artifact)
        for schema, entry in entries(manifest):
            definition = dict(
                (
                    await connection.execute(
                        select(DEFINITIONS).where(DEFINITIONS.c.index_def_id == entry["index_def_id"])
                    )
                )
                .mappings()
                .one()
            )
            table = await projection_table(connection, definition)
            index = parse_index(definition)
            if isinstance(index, VectorIndex):
                probe = vector_bytes([1.0] * index.profile.dimension, index.profile)
                if connection.dialect.name == "sqlite":
                    await ensure_sqlite_vector_extension(connection)
                    await connection.execute(text("SELECT vec_distance_l2(:vector, :vector)"), {"vector": probe})
                else:
                    from sqlalchemy import bindparam

                    values = list(struct.unpack(f"<{index.profile.dimension}f", probe))
                    await connection.execute(
                        text("SELECT l2_distance(:probe, :probe)").bindparams(
                            bindparam("probe", type_=table.c.embedding.type)
                        ),
                        {"probe": values},
                    )
            coverage = {
                row["artifact_id"]: row
                for row in (
                    await connection.execute(
                        select(COVERAGE).where(projection_key(COVERAGE, scope, family, build_id, entry["index_def_id"]))
                    )
                ).mappings()
            }
            expected = {
                identity for identity, (_, artifact) in artifacts.items() if artifact.content.schema_version == schema
            }
            if set(coverage) != expected:
                return False
            rows = (
                (
                    await connection.execute(
                        select(table).where(projection_key(table, scope, family, build_id, entry["index_def_id"]))
                    )
                )
                .mappings()
                .all()
            )
            by_artifact = {}
            for row in rows:
                if row["artifact_id"] not in expected:
                    return False
                by_artifact.setdefault(row["artifact_id"], []).append(row)
            for identity in expected:
                head, artifact = artifacts[identity]
                content = artifact.content.content
                row = coverage[identity]
                parts = (
                    content[index.fragments.array_field]
                    if isinstance(index, VectorIndex) and index.fragments
                    else [content]
                )
                if (
                    row["revision"],
                    row["schema_version"],
                    row["governance_generation"],
                    row["fragment_count"],
                    row["projection_input_hash"],
                ) != (
                    head["revision"],
                    schema,
                    head["governance_generation"],
                    len(parts),
                    digest({"projection": definition["definition_fingerprint"], "content": content}),
                ):
                    return False
                projected = by_artifact.get(identity, [])
                if len(projected) != len(parts):
                    return False
                expected_parts = {
                    (
                        part[index.fragments.id_field]
                        if isinstance(index, VectorIndex) and index.fragments
                        else "__whole__"
                    ): (offset, part)
                    for offset, part in enumerate(parts)
                }
                if {item["fragment_id"] for item in projected} != set(expected_parts):
                    return False
                for item in projected:
                    offset, part = expected_parts[item["fragment_id"]]
                    locator = (
                        {"array": index.fragments.array_field, "id": item["fragment_id"], "offset": offset}
                        if isinstance(index, VectorIndex) and index.fragments
                        else {"fields": list(index.fields)}
                    )
                    if (
                        item["input_hash"] != digest({"projection": definition["definition_fingerprint"], "part": part})
                        or item["fragment_locator"] != identity_json(locator).decode()
                    ):
                        return False
                    if isinstance(index, TextIndex):
                        if item["searchable_text"] != analyze_text("\n".join(part[field] for field in index.fields)):
                            return False
                    else:
                        if (item["space_id"], item["dimension"]) != (definition["space_id"], index.profile.dimension):
                            return False
                        vector = (
                            item["embedding"]
                            if connection.dialect.name == "sqlite"
                            else struct.pack(f"<{index.profile.dimension}f", *item["embedding"])
                        )
                        if len(vector) != 4 * index.profile.dimension or not all(
                            math.isfinite(value) for value in struct.unpack(f"<{index.profile.dimension}f", vector)
                        ):
                            return False
                        if index.source == "content" and vector != vector_bytes(
                            part[index.vector_field], index.profile
                        ):
                            return False
                if any(
                    (
                        item["scope_id"],
                        item["family"],
                        item["revision"],
                        item["schema_version"],
                        item["governance_generation"],
                        item["index_def_id"],
                    )
                    != (
                        scope,
                        family,
                        head["revision"],
                        schema,
                        head["governance_generation"],
                        definition["index_def_id"],
                    )
                    for item in projected
                ):
                    return False
        return True
