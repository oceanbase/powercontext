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

"""Typed Artifact search registration and public result validation."""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Generic, TypeVar

from pydantic import ValidationError
from pydantic_core import InitErrorDetails

from powercontext.artifacts import Artifact
from powercontext.artifacts.search import (
    ArtifactSearchContractError,
    ArtifactSearcher,
    ArtifactSearchExecutionContext,
    ArtifactSearchFamilyNotFound,
    ArtifactSearchMatch,
    ArtifactSearchOutcome,
    ArtifactSearchQuery,
    ArtifactSearchUnsupported,
)
from powercontext.builtin.statistics import ModelUsagePurpose

RequestT = TypeVar("RequestT", bound=ArtifactSearchQuery)
OutcomeT_co = TypeVar("OutcomeT_co", bound=ArtifactSearchOutcome, covariant=True)


class _SearcherBinding(Generic[RequestT, OutcomeT_co]):
    def __init__(
        self,
        searcher: ArtifactSearcher[RequestT, OutcomeT_co],
        embedding_purpose: ModelUsagePurpose | None,
    ) -> None:
        self.searcher = searcher
        self.embedding_purpose = embedding_purpose

    async def invoke(
        self,
        scope_id: str,
        payload: Mapping[str, Any],
        execution_context: ArtifactSearchExecutionContext | None,
    ) -> ArtifactSearchOutcome:
        encoded = _encode_request_payload(payload, self.searcher.request_type.__name__)
        request = self.searcher.request_type.model_validate_json(encoded, strict=True)
        outcome = await self.searcher.search(scope_id, request, execution_context=execution_context)
        _validate_public_outcome(self.searcher.family, request, outcome)
        return outcome


class ArtifactSearchService:
    """Dispatch repository-known Families without knowing their concrete models."""

    def __init__(self, *, known_families: Iterable[str]) -> None:
        self._known_families = frozenset(known_families)
        self._bindings: dict[str, _SearcherBinding[Any, ArtifactSearchOutcome]] = {}

    def register(
        self,
        searcher: ArtifactSearcher[RequestT, OutcomeT_co],
        *,
        embedding_purpose: ModelUsagePurpose | None = None,
    ) -> None:
        """Keep a Family's parser and callable in the same typed binding."""

        if searcher.family not in self._known_families:
            raise ArtifactSearchFamilyNotFound(searcher.family)
        if searcher.family in self._bindings:
            raise ValueError(f"Artifact searcher is already registered for {searcher.family}")  # noqa: TRY003
        if not isinstance(searcher.request_type, type) or not issubclass(searcher.request_type, ArtifactSearchQuery):
            raise TypeError("Artifact search request type must inherit ArtifactSearchQuery")  # noqa: TRY003
        self._bindings[searcher.family] = _SearcherBinding(searcher, embedding_purpose)

    def embedding_purpose(self, family: str) -> ModelUsagePurpose | None:
        """Read invocation metadata while preserving unknown/unsupported failures."""

        return self._binding(family).embedding_purpose

    async def search(
        self,
        scope_id: str,
        family: str,
        payload: Mapping[str, Any],
        *,
        execution_context: ArtifactSearchExecutionContext | None = None,
    ) -> ArtifactSearchOutcome:
        """Parse with the registered request model and return its existing outcome."""

        return await self._binding(family).invoke(scope_id, payload, execution_context)

    def _binding(self, family: str) -> _SearcherBinding[Any, ArtifactSearchOutcome]:
        if family not in self._known_families:
            raise ArtifactSearchFamilyNotFound(family)
        binding = self._bindings.get(family)
        if binding is None:
            raise ArtifactSearchUnsupported(family, field="family")
        return binding


def _encode_request_payload(payload: Mapping[str, Any], title: str) -> str:
    try:
        return json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, OverflowError):
        errors: list[InitErrorDetails] = []
        pending: list[tuple[Any, tuple[str | int, ...]]] = [(payload, ())]
        visited: set[int] = set()
        while pending:
            value, path = pending.pop()
            if isinstance(value, float) and not math.isfinite(value):
                errors.append({"type": "finite_number", "loc": path, "input": value})
            elif isinstance(value, (Mapping, list, tuple)) and id(value) not in visited:
                visited.add(id(value))
                entries = value.items() if isinstance(value, Mapping) else enumerate(value)
                pending.extend((item, (*path, key)) for key, item in entries)
        if not errors:
            errors.append({
                "type": "json_invalid",
                "loc": (),
                "input": None,
                "ctx": {"error": "search payload must contain JSON-compatible values"},
            })
        raise ValidationError.from_exception_data(title, errors) from None


def _validate_public_outcome(family: str, request: ArtifactSearchQuery, outcome: ArtifactSearchOutcome) -> None:
    try:
        matches = outcome.matches
        artifacts = outcome.artifacts
    except AttributeError as exc:
        raise ArtifactSearchContractError(family, "matches and artifacts are required") from exc
    if (
        not isinstance(matches, Sequence)
        or not isinstance(artifacts, Sequence)
        or isinstance(matches, (str, bytes, bytearray))
        or isinstance(artifacts, (str, bytes, bytearray))
    ):
        raise ArtifactSearchContractError(family, "public matches and artifacts must be sequences")
    if len(matches) != len(artifacts):
        raise ArtifactSearchContractError(family, "matches and artifacts must have the same count")
    if len(matches) > request.limit:
        raise ArtifactSearchContractError(family, "result count exceeds the parsed request limit")
    identities: set[tuple[str, str, int]] = set()
    for match, artifact in zip(matches, artifacts, strict=True):
        identity = _validate_public_result(family, request, match, artifact)
        if identity in identities:
            raise ArtifactSearchContractError(family, "an exact Artifact revision appears more than once")
        identities.add(identity)


def _validate_public_result(
    family: str,
    request: ArtifactSearchQuery,
    match: ArtifactSearchMatch,
    artifact: Artifact[Any],
) -> tuple[str, str, int]:
    if not isinstance(match, ArtifactSearchMatch) or not isinstance(artifact, Artifact):
        raise ArtifactSearchContractError(family, "results must contain ArtifactSearchMatch and Artifact values")
    try:
        ArtifactSearchMatch(match.artifact_ref, match.retrieval_score, match.channel_scores)
        artifact_ref = artifact.as_ref()
    except (ValueError, TypeError, AttributeError) as exc:
        raise ArtifactSearchContractError(family, "invalid exact reference or score metadata") from exc
    if match.artifact_ref.family != family or artifact_ref != match.artifact_ref:
        raise ArtifactSearchContractError(family, "Artifact identity, Family, and match order must agree")
    if request.include_scores and match.channel_scores is None:
        raise ArtifactSearchContractError(family, "requested channel scores were not collected")
    return (match.artifact_ref.family, match.artifact_ref.artifact_id, match.artifact_ref.revision)


__all__ = ["ArtifactSearchService"]
