"""Captured exact snapshots used only by explicit external Skill import or fork."""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel

from powercontext.builtin.artifacts.skill import ExternalSkillSnapshot
from powercontext.sources import Source, SourceMaterialization

EXTERNAL_SKILL_SNAPSHOT_SOURCE_NAME = "external-skill-snapshot"


class ExternalSkillImportMode(StrEnum):
    """The explicit intent that caused a managed Skill proposal."""

    IMPORT = "import"
    FORK = "fork"


class ExternalSkillSnapshotCapture(BaseModel):
    """One exact external package observation selected by a caller."""

    snapshot: ExternalSkillSnapshot
    mode: ExternalSkillImportMode


class ExternalSkillSnapshotSource(Source):
    """Durable direct evidence without assuming authority over the external package."""

    snapshot: ExternalSkillSnapshot
    mode: ExternalSkillImportMode


class ExternalSkillSnapshotSourceAdapter:
    """Materialize caller-selected snapshots as immutable Source evidence."""

    input_class = ExternalSkillSnapshotCapture
    name = EXTERNAL_SKILL_SNAPSHOT_SOURCE_NAME
    source_class = ExternalSkillSnapshotSource

    async def resolve(self, value: ExternalSkillSnapshotCapture, /) -> ExternalSkillSnapshotSource:
        return ExternalSkillSnapshotSource(
            name=_snapshot_id(value),
            materialization=SourceMaterialization.CAPTURED,
            description="Exact external Skill snapshot captured by an explicit managed import or fork.",
            snapshot=value.snapshot,
            mode=value.mode,
        )

    async def read(self, source: ExternalSkillSnapshotSource, /) -> ExternalSkillSnapshotCapture:
        return ExternalSkillSnapshotCapture(snapshot=source.snapshot, mode=source.mode)


def _snapshot_id(value: ExternalSkillSnapshotCapture) -> str:
    registration = value.snapshot.registration
    identity = "\0".join((
        registration.provider,
        registration.agent_kind,
        registration.host_id,
        registration.external_skill_id,
        registration.fingerprint,
        value.mode.value,
    ))
    return f"ext_skill_{hashlib.sha256(identity.encode()).hexdigest()}"


EXTERNAL_SKILL_SNAPSHOT_SOURCE_ADAPTER = ExternalSkillSnapshotSourceAdapter()

__all__ = [
    "EXTERNAL_SKILL_SNAPSHOT_SOURCE_ADAPTER",
    "EXTERNAL_SKILL_SNAPSHOT_SOURCE_NAME",
    "ExternalSkillImportMode",
    "ExternalSkillSnapshotCapture",
    "ExternalSkillSnapshotSource",
    "ExternalSkillSnapshotSourceAdapter",
]
