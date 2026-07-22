"""Canonical row codecs shared by SQL Memory adapters."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import cast

from powercontext.artifacts import ArtifactLineage, ArtifactRef
from powercontext.errors import InvalidMemoryEvidenceError, MemoryBackendConfigurationError
from powercontext.memory.canonical import canonical_json
from powercontext.memory.models import (
    MemoryChange,
    MemoryChangeOp,
    MemoryContent,
    MemoryEntryState,
    MemoryEntryVersion,
    MemoryManifest,
    MemoryManifestEntry,
)
from powercontext.memory.protocols import MemoryCommit, MemoryEvidenceCodec
from powercontext.sources import Source


class _InvalidStoredMemoryError(MemoryBackendConfigurationError):
    def __init__(self, code: str, detail: str | None = None) -> None:
        messages = {
            "content": "invalid stored MemoryContent",
            "lineage": "invalid stored ArtifactLineage",
            "manifest-entry": "invalid stored Memory manifest entry",
            "change": "invalid stored Memory change",
            "artifact-ref": "invalid stored ArtifactRef",
            "source-codec": "stored Source evidence requires a MemoryEvidenceCodec",
            "array": f"{detail} must be an array",
        }
        super().__init__(messages[code])


def encode_memory_content(content: MemoryContent) -> str:
    value = {
        "schema": content.schema,
        "manifest": {
            "format": content.manifest.format,
            "entries": tuple(
                {
                    "entry_id": entry.entry_id,
                    "entry_version_id": entry.entry_version_id,
                    "entry_content_hash": entry.entry_content_hash,
                    "state": entry.state,
                }
                for entry in content.manifest.entries
            ),
        },
        "changes": tuple(
            {
                "op": change.op,
                "entry_id": change.entry_id,
                "from_entry_version_id": change.from_entry_version_id,
                "to_entry_version_id": change.to_entry_version_id,
                "reason": change.reason,
            }
            for change in content.changes
        ),
    }
    return canonical_json(value).decode("utf-8")


def decode_memory_content(value: str) -> MemoryContent:
    match json.loads(value):
        case {
            "schema": "powercontext.memory.v1",
            "manifest": {"format": "flat-v1", "entries": list(entries)},
            "changes": list(changes),
        }:
            return MemoryContent(
                manifest=MemoryManifest(entries=tuple(_decode_manifest_entry(item) for item in entries)),
                changes=tuple(_decode_change(item) for item in changes),
            )
        case _:
            raise _InvalidStoredMemoryError("content")


def encode_lineage(lineage: ArtifactLineage, codec: MemoryEvidenceCodec | None) -> str:
    source_refs = tuple(_encode_source(source, codec) for source in lineage.sources)
    artifact_refs = tuple(_encode_artifact(artifact, codec) for artifact in lineage.artifacts)
    return canonical_json({"sources": source_refs, "artifacts": artifact_refs}).decode("utf-8")


def decode_lineage(value: str, codec: MemoryEvidenceCodec | None) -> ArtifactLineage:
    match json.loads(value):
        case {"sources": list(sources), "artifacts": list(artifacts)}:
            return ArtifactLineage(
                sources=tuple(_decode_source(item, codec) for item in sources),
                artifacts=tuple(_decode_artifact(item, codec) for item in artifacts),
            )
        case _:
            raise _InvalidStoredMemoryError("lineage")


def encode_entry_refs(
    entry: MemoryEntryVersion,
    codec: MemoryEvidenceCodec | None,
) -> tuple[str, str]:
    sources = tuple(_encode_source(source, codec) for source in entry.sources)
    artifacts = tuple(_encode_artifact(artifact, codec) for artifact in entry.artifacts)
    return canonical_json(sources).decode("utf-8"), canonical_json(artifacts).decode("utf-8")


def decode_entry_version(
    *,
    memory_artifact_id: object,
    entry_id: object,
    entry_version_id: object,
    version: object,
    previous_version_id: object,
    kind: object,
    text: object,
    source_refs: object,
    artifact_refs: object,
    entry_content_hash: object,
    created_in_revision: object,
    codec: MemoryEvidenceCodec | None,
) -> MemoryEntryVersion:
    sources = _json_array(source_refs, "source refs")
    artifacts = _json_array(artifact_refs, "artifact refs")
    return MemoryEntryVersion(
        memory_artifact_id=cast(str, memory_artifact_id),
        entry_id=cast(str, entry_id),
        entry_version_id=cast(str, entry_version_id),
        version=cast(int, version),
        previous_version_id=cast(str | None, previous_version_id),
        kind=cast(str, kind),
        text=cast(str, text),
        sources=tuple(_decode_source(item, codec) for item in sources),
        artifacts=tuple(_decode_artifact(item, codec) for item in artifacts),
        entry_content_hash=cast(str, entry_content_hash),
        created_in_revision=cast(int, created_in_revision),
    )


def validate_commit_evidence(value: MemoryCommit, codec: MemoryEvidenceCodec | None) -> None:
    """Re-resolve every new or operation-level evidence ref inside the write transaction."""

    sources = (*value.memory.lineage.sources, *(source for entry in value.entry_versions for source in entry.sources))
    artifacts = (
        *value.memory.lineage.artifacts,
        *(artifact for entry in value.entry_versions for artifact in entry.artifacts),
    )
    _validate_source_evidence(sources, codec)
    _validate_artifact_evidence(artifacts, codec)


def _validate_source_evidence(sources: Sequence[Source], codec: MemoryEvidenceCodec | None) -> None:
    if sources and codec is None:
        raise InvalidMemoryEvidenceError("source-codec")
    if codec is None:
        return

    source_refs: dict[bytes, tuple[Source, object]] = {}
    for source in sources:
        reference = codec.encode_source(source)
        key = canonical_json(reference)
        previous = source_refs.get(key)
        if previous is not None and previous[0] != source:
            raise InvalidMemoryEvidenceError("source-changed")
        source_refs[key] = (source, reference)
    for key, (source, reference) in source_refs.items():
        resolved = codec.decode_source(reference)
        if resolved != source or canonical_json(codec.encode_source(resolved)) != key:
            raise InvalidMemoryEvidenceError("source-changed")


def _validate_artifact_evidence(artifacts: Sequence[ArtifactRef], codec: MemoryEvidenceCodec | None) -> None:
    if artifacts and codec is None:
        raise InvalidMemoryEvidenceError("artifact-codec")
    if codec is None:
        return

    artifact_refs: dict[bytes, tuple[ArtifactRef, object]] = {}
    for artifact in artifacts:
        reference = codec.encode_artifact(artifact)
        key = canonical_json(reference)
        previous = artifact_refs.get(key)
        if previous is not None and previous[0] != artifact:
            raise InvalidMemoryEvidenceError("artifact-changed")
        artifact_refs[key] = (artifact, reference)
    for key, (artifact, reference) in artifact_refs.items():
        resolved = codec.decode_artifact(reference)
        if resolved != artifact or canonical_json(codec.encode_artifact(resolved)) != key:
            raise InvalidMemoryEvidenceError("artifact-changed")


def _decode_manifest_entry(value: object) -> MemoryManifestEntry:
    match value:
        case {
            "entry_id": str(entry_id),
            "entry_version_id": str(entry_version_id),
            "entry_content_hash": str(entry_content_hash),
            "state": ("active" | "inactive") as state,
        }:
            return MemoryManifestEntry(entry_id, entry_version_id, entry_content_hash, cast(MemoryEntryState, state))
        case _:
            raise _InvalidStoredMemoryError("manifest-entry")


def _decode_change(value: object) -> MemoryChange:
    match value:
        case {
            "op": str(op),
            "entry_id": str(entry_id),
            "from_entry_version_id": from_id,
            "to_entry_version_id": to_id,
            "reason": reason,
        } if (
            op in {"add", "revise", "deactivate", "reactivate"}
            and (from_id is None or isinstance(from_id, str))
            and (to_id is None or isinstance(to_id, str))
            and (reason is None or isinstance(reason, str))
        ):
            return MemoryChange(cast(MemoryChangeOp, op), entry_id, from_id, to_id, reason)
        case _:
            raise _InvalidStoredMemoryError("change")


def _encode_source(value: Source, codec: MemoryEvidenceCodec | None) -> object:
    if codec is None:
        raise _InvalidStoredMemoryError("source-codec")
    return codec.encode_source(value)


def _decode_source(value: object, codec: MemoryEvidenceCodec | None) -> Source:
    if codec is None:
        raise _InvalidStoredMemoryError("source-codec")
    return codec.decode_source(value)


def _encode_artifact(value: ArtifactRef, codec: MemoryEvidenceCodec | None) -> object:
    if codec is None:
        return {"artifact_id": value.artifact_id, "revision": value.revision}
    return codec.encode_artifact(value)


def _decode_artifact(value: object, codec: MemoryEvidenceCodec | None) -> ArtifactRef:
    if codec is not None:
        return codec.decode_artifact(value)
    match value:
        case {"artifact_id": str(artifact_id), "revision": int(revision)} if not isinstance(revision, bool):
            return ArtifactRef(artifact_id, revision)
        case _:
            raise _InvalidStoredMemoryError("artifact-ref")


def _json_array(value: object, label: str) -> Sequence[object]:
    """Decode one SQL text column whose canonical JSON value must be an array."""

    decoded = json.loads(cast(str, value))
    if not isinstance(decoded, list):
        raise _InvalidStoredMemoryError("array", label)
    return decoded
