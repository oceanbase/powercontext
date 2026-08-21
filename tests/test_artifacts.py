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

from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest
from pydantic import ValidationError

from powercontext import ArtifactFamilyMismatchError
from powercontext.artifacts import (
    Artifact,
    ArtifactCatalog,
    ArtifactDraft,
    ArtifactLineage,
    ArtifactRef,
    ArtifactStore,
)
from powercontext.context import Artifacts
from powercontext.sources import SourceRef


class ExtractedMemory(Artifact[tuple[str, ...]]):
    family: ClassVar[str] = "extracted-memory"


class HandoffDraft(ArtifactDraft[str]):
    family: ClassVar[str] = "handoff"


class RejectingArtifactBackend(
    ArtifactCatalog[Artifact[object]],
    ArtifactStore[ArtifactDraft[object], Artifact[object]],
):
    async def revise(self, artifact: Artifact[object], draft: ArtifactDraft[object], /) -> Artifact[object]:
        raise AssertionError


def test_artifact_is_a_fixed_family_snapshot_with_direct_lineage() -> None:
    source = SourceRef(source_type="conversation", source_id="session-42-snapshot")
    dependency = ArtifactRef(family="profile", artifact_id="user-profile", revision=2)
    artifact = ExtractedMemory(
        artifact_id="preference-memory",
        revision=3,
        content=("User prefers aisle seats.",),
        lineage=ArtifactLineage(sources=(source,), artifacts=(dependency,)),
    )

    assert artifact.family == "extracted-memory"
    assert artifact.as_ref() == ArtifactRef(family="extracted-memory", artifact_id="preference-memory", revision=3)
    assert artifact.lineage == ArtifactLineage(sources=(source,), artifacts=(dependency,))


@pytest.mark.parametrize(
    ("family", "artifact_id", "revision", "field"),
    [
        ("", "artifact", 1, "family"),
        (" family", "artifact", 1, "family"),
        ("x" * 129, "artifact", 1, "family"),
        ("memory", "", 1, "artifact_id"),
        ("memory", " artifact", 1, "artifact_id"),
        ("memory", "x" * 129, 1, "artifact_id"),
        ("memory", "artifact", 0, "revision"),
        ("memory", "artifact", -1, "revision"),
        ("memory", "artifact", True, "revision"),
    ],
)
def test_artifact_reference_rejects_invalid_identity(
    family: str,
    artifact_id: str,
    revision: int,
    field: str,
) -> None:
    with pytest.raises(ValidationError) as error:
        ArtifactRef(family=family, artifact_id=artifact_id, revision=revision)
    assert error.value.errors()[0]["loc"][0] == field


@pytest.mark.parametrize(
    ("factory", "field"),
    [
        (
            lambda: ExtractedMemory(
                artifact_id="",
                revision=1,
                content=(),
            ),
            "artifact_id",
        ),
        (
            lambda: ExtractedMemory(
                artifact_id="memory",
                revision=0,
                content=(),
            ),
            "revision",
        ),
        (
            lambda: ExtractedMemory(
                artifact_id="memory",
                revision=1,
                content=(),
                lineage=object(),  # ty: ignore[invalid-argument-type]
            ),
            "lineage",
        ),
        (
            lambda: ArtifactLineage(sources=(object(),)),  # ty: ignore[invalid-argument-type]
            "sources",
        ),
        (
            lambda: HandoffDraft(content="handoff", sources=(object(),)),  # ty: ignore[invalid-argument-type]
            "sources",
        ),
    ],
)
def test_artifact_domain_values_reject_invalid_identity_and_lineage(factory, field: str) -> None:
    with pytest.raises(ValidationError) as error:
        factory()
    assert error.value.errors()[0]["loc"][0] == field


def test_artifacts_reject_cross_family_revisions_before_storage() -> None:
    async def scenario() -> None:
        backend = RejectingArtifactBackend()
        artifacts = Artifacts(
            catalog=backend,
            store=backend,
        )
        memory = ExtractedMemory(
            artifact_id="preference-memory",
            revision=3,
            content=("User prefers aisle seats.",),
        )
        draft = HandoffDraft(content="handoff")

        with pytest.raises(ArtifactFamilyMismatchError) as error:
            await artifacts.revise(memory, draft)

        assert error.value.artifact is memory
        assert error.value.draft is draft

    asyncio.run(scenario())
