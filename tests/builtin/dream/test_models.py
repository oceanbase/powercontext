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

"""Dream evidence selection failures name the rejected field and its bounds."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.catalog_changes.models import TagDreamTarget, TagTarget
from powercontext.builtin.dream.models import CreateDreamRunRequest
from powercontext.sources import SourceRef


def _artifacts(count: int) -> tuple[ArtifactRef, ...]:
    return tuple(
        ArtifactRef(family="experience", artifact_id=f"experience-{index}", revision=1) for index in range(count)
    )


def _sources(count: int) -> tuple[SourceRef, ...]:
    return tuple(SourceRef(source_type="content", source_id=f"source-{index}") for index in range(count))


def _tag_target() -> TagDreamTarget:
    ref = ArtifactRef(family="experience", artifact_id="experience-1", revision=1)
    return TagDreamTarget(
        target=TagTarget(family="experience", artifact_id="experience-1"),
        expected_etag="etag-1",
        basis_ref=ref,
    )


def test_empty_artifact_selection_names_artifacts() -> None:
    with pytest.raises(ValidationError) as raised:
        CreateDreamRunRequest(operation="refine_experience", idempotency_key="empty")

    [error] = raised.value.errors()
    assert error["loc"] == ("artifacts",)
    assert error["type"] == "evidence_selection_required"
    assert "1 to 20 Artifacts" in error["msg"]
    assert "at most 32 references with Sources" in error["msg"]


def test_artifact_selection_above_the_limit_names_artifacts() -> None:
    with pytest.raises(ValidationError) as raised:
        CreateDreamRunRequest(
            operation="refine_experience",
            artifacts=_artifacts(21),
            idempotency_key="too-many",
        )

    [error] = raised.value.errors()
    assert error["loc"] == ("artifacts",)
    assert error["type"] == "evidence_limit_exceeded"
    assert "at most 20 Artifacts" in error["msg"]
    assert "at most 32 references with Sources" in error["msg"]


def test_combined_reference_limit_names_sources_and_the_total_bound() -> None:
    with pytest.raises(ValidationError) as raised:
        CreateDreamRunRequest(
            operation="refine_experience",
            artifacts=_artifacts(20),
            sources=_sources(13),
            idempotency_key="too-many-references",
        )

    [error] = raised.value.errors()
    assert error["loc"] == ("sources",)
    assert error["type"] == "evidence_limit_exceeded"
    assert "at most 32 references" in error["msg"]


def test_a_full_page_of_artifacts_still_allows_paired_sources() -> None:
    request = CreateDreamRunRequest(
        operation="refine_experience",
        artifacts=_artifacts(20),
        sources=_sources(12),
        idempotency_key="at-the-limit",
    )

    assert len(request.artifacts) == 20
    assert len(request.sources) == 12


def test_revise_tags_accepts_an_empty_artifact_selection_with_sources() -> None:
    request = CreateDreamRunRequest(
        operation="revise_tags",
        tag_target=_tag_target(),
        sources=_sources(1),
        idempotency_key="tag-change",
    )

    assert request.artifacts == ()
    assert len(request.sources) == 1


def test_revise_tags_requires_at_least_one_reference() -> None:
    with pytest.raises(ValidationError) as raised:
        CreateDreamRunRequest(
            operation="revise_tags",
            tag_target=_tag_target(),
            idempotency_key="no-references",
        )

    [error] = raised.value.errors()
    assert error["loc"] == ("sources",)
    assert error["type"] == "evidence_selection_required"
    assert error["msg"] == "sources or artifacts must select at least 1 reference (at most 32 references in total)"


def test_revise_tags_reports_only_the_combined_limit() -> None:
    with pytest.raises(ValidationError) as raised:
        CreateDreamRunRequest(
            operation="revise_tags",
            tag_target=_tag_target(),
            artifacts=_artifacts(21),
            sources=_sources(12),
            idempotency_key="tag-limit",
        )

    [error] = raised.value.errors()
    assert error["loc"] == ("sources",)
    assert error["type"] == "evidence_limit_exceeded"
    assert error["msg"] == "artifacts and sources must select at most 32 references in total"


def test_revise_tags_has_no_separate_twenty_artifact_limit() -> None:
    # The basis_ref below is already among these Artifacts, so the combined
    # selection stays exactly at the 32-reference limit.
    request = CreateDreamRunRequest(
        operation="revise_tags",
        tag_target=_tag_target(),
        artifacts=_artifacts(21),
        sources=_sources(11),
        idempotency_key="tag-at-the-limit",
    )

    assert len(request.artifacts) == 21
    assert len(request.sources) == 11
