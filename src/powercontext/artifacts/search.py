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

"""Caller-neutral Artifact search requests and Family-owned result contracts."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from numbers import Real
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from powercontext.artifacts.models import Artifact, ArtifactRef
from powercontext.errors import PowerContextError


class ArtifactSearchQuery(BaseModel):
    """Common constraints inherited by each Family's public search request."""

    model_config = ConfigDict(extra="forbid", strict=True)

    query: str = Field(min_length=1, max_length=8192)
    limit: StrictInt = Field(default=10, ge=1, le=200)
    include_scores: StrictBool = False

    @field_validator("query", mode="before")
    @classmethod
    def trim_query(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @field_validator("*", mode="after")
    @classmethod
    def reject_explicit_null(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("omit the field instead of sending null")  # noqa: TRY003
        return value


@dataclass(frozen=True)
class ArtifactSearchExecutionContext:
    """Trusted invocation metadata kept outside the caller's search payload.

    The host owns the principal, access service, and audit objects. A missing
    context retains a Family's local SDK policy; HTTP supplies an explicit one.
    """

    principal: Any = None
    access: Any = None
    audit: Any = None
    trusted_local: bool = False


@dataclass(frozen=True)
class ChannelScore:
    """A collected channel value before normalization or direction changes."""

    raw: float
    metric: str
    higher_is_better: bool

    def __post_init__(self) -> None:
        _require_finite_real(self.raw, "channel raw score")
        if not isinstance(self.metric, str) or not self.metric.strip():
            raise ValueError("channel metric must be a non-empty string")  # noqa: TRY003
        if type(self.higher_is_better) is not bool:
            raise ValueError("channel score direction must be a boolean")  # noqa: TRY003


@dataclass(frozen=True)
class ArtifactSearchMatch:
    """One exact revision, its retrieval score, and optional collected metadata."""

    artifact_ref: ArtifactRef
    retrieval_score: float
    channel_scores: Mapping[str, ChannelScore] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.artifact_ref, ArtifactRef):
            raise TypeError("search match requires an exact ArtifactRef")  # noqa: TRY003
        ArtifactRef.model_validate(self.artifact_ref.model_dump(), strict=True)
        _require_finite_real(self.retrieval_score, "retrieval score")
        if not 0 <= self.retrieval_score <= 1:
            raise ValueError("retrieval score must be between zero and one")  # noqa: TRY003
        if self.channel_scores is not None:
            if not isinstance(self.channel_scores, Mapping):
                raise ValueError("channel scores must be a mapping")  # noqa: TRY003
            for name, value in self.channel_scores.items():
                if not isinstance(name, str) or not name.strip() or not isinstance(value, ChannelScore):
                    raise ValueError("channel scores require named ChannelScore values")  # noqa: TRY003
                ChannelScore(value.raw, value.metric, value.higher_is_better)


class ArtifactSearchOutcome(Protocol):
    """A Family outcome retaining its final order and actual Artifact values."""

    @property
    def matches(self) -> Sequence[ArtifactSearchMatch]: ...

    @property
    def artifacts(self) -> Sequence[Artifact[Any]] | None: ...


RequestT = TypeVar("RequestT", bound=ArtifactSearchQuery)
OutcomeT_co = TypeVar("OutcomeT_co", bound=ArtifactSearchOutcome, covariant=True)


class ArtifactSearcher(Protocol[RequestT, OutcomeT_co]):
    """A Family's request model and callable bound by one registration."""

    @property
    def family(self) -> str: ...

    @property
    def request_type(self) -> type[RequestT]: ...

    async def search(
        self,
        scope_id: str,
        request: RequestT,
        /,
        *,
        execution_context: ArtifactSearchExecutionContext | None = None,
    ) -> OutcomeT_co: ...


class ArtifactSearchFamilyNotFound(PowerContextError, LookupError):
    """The Artifact repository does not own the requested Family."""

    def __init__(self, family: str) -> None:
        self.family = family
        super().__init__(f"Artifact Family was not found: {family}")


class ArtifactSearchUnsupported(PowerContextError, ValueError):
    """A known Family cannot search, or cannot use a requested search field."""

    def __init__(self, family: str, *, field: str | None = None) -> None:
        self.family = family
        self.field = field
        super().__init__(f"Artifact search is unsupported for {family}" + (f": {field}" if field else ""))


class ArtifactSearchContractError(PowerContextError, RuntimeError):
    """A search implementation returned values that violate the public contract."""

    def __init__(self, family: str, detail: str) -> None:
        self.family = family
        self.detail = detail
        super().__init__(f"Artifact search result contract failed for {family}: {detail}")


def _require_finite_real(value: object, label: str) -> None:
    try:
        finite = not isinstance(value, bool) and isinstance(value, Real) and math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        raise ValueError(f"{label} must be a finite real number")  # noqa: TRY003


__all__ = [
    "ArtifactSearchContractError",
    "ArtifactSearchExecutionContext",
    "ArtifactSearchFamilyNotFound",
    "ArtifactSearchMatch",
    "ArtifactSearchOutcome",
    "ArtifactSearchQuery",
    "ArtifactSearchUnsupported",
    "ArtifactSearcher",
    "ChannelScore",
]
