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

"""Read the exact entry evidence retained by the frozen Atomic Memory import."""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import ArtifactLineage, ArtifactRef
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemory
from powercontext.builtin.artifacts.memory.errors import InvalidMemoryCitationError
from powercontext.builtin.artifacts.memory.models import Memory, MemoryEntryVersion
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.atomic_memory_identity import legacy_entry_artifact_id
from powercontext.builtin.persistence.memory import _decode_entry
from powercontext.builtin.persistence.tables import MEMORY_ENTRY_VERSIONS_TABLE


@dataclass(frozen=True)
class ImportedMemoryEvidence:
    entry: MemoryEntryVersion
    anchor: ArtifactRef
    lineage: ArtifactLineage


async def read_imported_memory_evidence(
    connection: AsyncConnection,
    artifacts: ArtifactRepository,
    scope_id: str,
    artifact: AtomicMemory,
) -> ImportedMemoryEvidence | None:
    """Recognize an unchanged imported revision without rewriting immutable lineage.

    The collection anchor identifies the frozen manifest, not the Sources that
    support every entry in it. Only its exact entry version supplies generation
    evidence. Explicit collection references on ordinary Atomic revisions keep
    their normal meaning.
    """

    if (
        artifact.content.creation is not None
        or artifact.lineage.sources
        or artifact.lineage.memory_citations
        or artifact.lineage.publication_source is not None
        or not artifact.lineage.artifacts
    ):
        return None
    anchor = artifact.lineage.artifacts[0]
    if anchor.family != Memory.family:
        return None
    memory = await artifacts.get(connection, scope_id, anchor)
    if not isinstance(memory, Memory):
        return None
    pointer = next(
        (
            item
            for item in memory.content.manifest.entries
            if legacy_entry_artifact_id(scope_id, memory.artifact_id, item.entry_id) == artifact.artifact_id
        ),
        None,
    )
    if pointer is None:
        return None
    table = MEMORY_ENTRY_VERSIONS_TABLE
    row = (
        (
            await connection.execute(
                select(table).where(
                    table.c.scope_id == scope_id,
                    table.c.family == Memory.family,
                    table.c.memory_artifact_id == memory.artifact_id,
                    table.c.entry_id == pointer.entry_id,
                    table.c.entry_version_id == pointer.entry_version_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise InvalidMemoryCitationError("imported-entry-missing")
    entry = _decode_entry(row)
    if entry.version != artifact.revision or entry.created_in_revision != anchor.revision:
        return None
    if entry.entry_content_hash != pointer.entry_content_hash:
        raise InvalidMemoryCitationError("hash-mismatch")
    predecessor = (
        ()
        if entry.version == 1
        else (ArtifactRef(family=AtomicMemory.family, artifact_id=artifact.artifact_id, revision=entry.version - 1),)
    )
    expected = tuple(dict.fromkeys(ref.model_dump_json() for ref in (anchor, *entry.artifacts, *predecessor)))
    actual = tuple(ref.model_dump_json() for ref in artifact.lineage.artifacts)
    if artifact.content.kind != entry.kind or artifact.content.text != entry.text or actual != expected:
        return None
    # The anchor remains in stored lineage and is readable as provenance. Do not
    # connect it into the generation graph: a separately selected collection
    # node can expand all of its Sources after global graph deduplication.
    return ImportedMemoryEvidence(
        entry=entry,
        anchor=anchor,
        lineage=ArtifactLineage(sources=entry.sources, artifacts=(*entry.artifacts, *predecessor)),
    )
