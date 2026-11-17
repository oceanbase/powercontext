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

"""Explicit cross-Scope Artifact query with per-origin authorization and honest coverage.

This is an aggregation over the registered Family searchers: each declared origin is authorized,
searched through its own registered Searcher, and merged by that origin's own rank. Nothing here
interprets a Family's request fields, scores, or results, and no new retrieval engine, index, or
origin resolver is introduced. Range resolution stays with the caller or with the structural
resolver; this service accepts an already resolved origin list and refuses one it cannot enforce
rather than shrinking it silently.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.artifacts.search import (
    ArtifactSearchExecutionContext,
    ArtifactSearchUnavailable,
    ArtifactSearchUnsupported,
)
from powercontext.builtin.authz import (
    AccessAction,
    AccessDeniedError,
    AccessIdentityRequiredError,
    PrincipalRef,
    ResourceRef,
)
from powercontext.builtin.records import ArtifactRecord
from powercontext.builtin.scope.errors import ScopeNotFoundError
from powercontext.builtin.sources import validate_scope_id
from powercontext.errors import PowerContextError

DEFAULT_CROSS_SCOPE_MAX_SCOPES = 8
DEFAULT_CROSS_SCOPE_MAX_CANDIDATES = 64
CROSS_SCOPE_RESULT_LIMIT = 200
CROSS_SCOPE_SCOPE_LIMIT = 32
CROSS_SCOPE_CANDIDATE_LIMIT = 256

CoverageReason = Literal["complete", "origin_failure", "candidate_budget"]

# One origin being unreachable to this caller: refused, unavailable, or absent. These degrade
# partial coverage into a reported gap. Everything else — a malformed or unsupported common
# request — fails identically at every origin, so it keeps propagating as its owner reported it.
_ORIGIN_FAILURES = (
    AccessDeniedError,
    ArtifactSearchUnavailable,
    ScopeNotFoundError,
)


class CrossScopeQueryError(PowerContextError):
    """Base class for cross-Scope query admission failures."""


class CrossScopeQueryBoundsError(CrossScopeQueryError, ValueError):
    """The requested range or budget cannot be enforced as a complete query."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"cross-Scope query {field}: {detail}")


class CrossScopeQueryUnsupported(CrossScopeQueryError, ValueError):
    """A requested restriction cannot be applied to the current searchable projection."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"cross-Scope query is unsupported for {field}: {detail}")


class CrossScopeQueryRequest(BaseModel):
    """An explicit origin list, one Family's search fields, and the enforced budgets.

    ``limit`` bounds the merged result set. ``max_candidates`` bounds per-origin candidate work
    and is only ever a ceiling: it narrows a caller-declared ``payload.limit`` and never widens
    it, and it adds no limit when the caller omitted one.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    family: str = Field(min_length=1, max_length=128, pattern=r"^[a-z][a-z0-9-]*$")
    payload: dict[str, Any] = Field(default_factory=dict)
    scopes: tuple[str, ...]
    limit: StrictInt = Field(default=10, ge=1, le=CROSS_SCOPE_RESULT_LIMIT)
    max_scopes: StrictInt = Field(default=DEFAULT_CROSS_SCOPE_MAX_SCOPES, ge=1, le=CROSS_SCOPE_SCOPE_LIMIT)
    max_candidates: StrictInt = Field(default=DEFAULT_CROSS_SCOPE_MAX_CANDIDATES, ge=1, le=CROSS_SCOPE_CANDIDATE_LIMIT)
    coverage: Literal["complete", "partial"] = "complete"
    restrict_to: ArtifactRef | None = None

    @field_validator("scopes")
    @classmethod
    def validate_scopes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        # The declared origin order is preserved: it is the merge tie break and the caller's own
        # statement of which origins it asked for. Only uniqueness and shape are enforced.
        if not values:
            raise ValueError("cross-Scope query requires at least one explicit origin")  # noqa: TRY003
        normalized = tuple(validate_scope_id(value) for value in values)
        if len(set(normalized)) != len(normalized):
            raise ValueError("cross-Scope query origins must be unique")  # noqa: TRY003
        return normalized


class CrossScopeHit(BaseModel):
    """One exact Artifact revision, its origin, and its rank inside that origin."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: ArtifactAddress
    origin_rank: StrictInt = Field(ge=1)
    retrieval_score: float | None = None

    @property
    def identity(self) -> tuple[str, str, str, int]:
        """The cross-origin identity: origin Scope, Family, Artifact ID and revision."""

        artifact = self.origin.artifact
        return (self.origin.scope_id, artifact.family, artifact.artifact_id, artifact.revision)


class CrossScopeCoverage(BaseModel):
    """Which origins answered, which failed, and whether the range was fully searched."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    complete: bool
    reason: CoverageReason
    searched_origins: tuple[str, ...] = ()
    failed_origins: tuple[str, ...] = ()
    bounded_origins: tuple[str, ...] = ()


class CrossScopeQueryResult(BaseModel):
    """Authorized hits in per-origin rank order together with their coverage statement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    hits: tuple[CrossScopeHit, ...] = ()
    coverage: CrossScopeCoverage


class ScopedCrossScopeQuery:
    """Run one cross-Scope query or one exact readback in an existing Scope."""

    def __init__(self, runtime: Any, scope_id: str) -> None:
        self._runtime = runtime
        self.scope_id = validate_scope_id(scope_id)

    async def search(
        self,
        request: CrossScopeQueryRequest,
        /,
        *,
        execution_context: ArtifactSearchExecutionContext | None = None,
    ) -> CrossScopeQueryResult:
        """Authorize and search every declared origin, then merge by origin rank."""

        principal, access = _invocation(execution_context)
        if request.restrict_to is not None:
            raise CrossScopeQueryUnsupported(
                "restrict_to",
                "the approved-head projection indexes only current revisions, so candidates cannot be "
                "restricted before scoring",
            )
        _require_range(request)
        # The query Scope itself must exist before any origin is authorized.
        async with self._runtime._scoped_operation(self.scope_id):
            pass
        # The Family validates the caller's original payload first, so its own defaults and errors
        # are what the budget caps — never a guess and never a rewritten invalid value.
        declared = _invocation_window(self._runtime, request)
        window = _OriginWindow(request, declared)
        if window.bounded and request.coverage == "complete":
            # Refuse before spending any origin search: this range cannot be fully searched.
            raise CrossScopeQueryBoundsError(
                "max_candidates",
                "the candidate budget is smaller than the effective per-origin window, so the range "
                "cannot be reported as completely searched",
            )
        searched: list[tuple[str, tuple[CrossScopeHit, ...]]] = []
        failed: list[str] = []
        bounded: list[str] = []
        for origin in request.scopes:
            try:
                ranked = await self._origin_hits(origin, request, window, principal, access, execution_context)
            except _ORIGIN_FAILURES:
                if request.coverage == "complete":
                    raise
                failed.append(origin)
                continue
            if window.bounded:
                bounded.append(origin)
            searched.append((origin, ranked))
        hits = _merge_by_origin_rank(tuple(searched), limit=request.limit)
        return CrossScopeQueryResult(hits=hits, coverage=_coverage(searched, failed, bounded))

    async def _origin_hits(
        self,
        origin: str,
        request: CrossScopeQueryRequest,
        window: _OriginWindow,
        principal: PrincipalRef | None,
        access: Any,
        execution_context: ArtifactSearchExecutionContext | None,
    ) -> tuple[CrossScopeHit, ...]:
        """Authorize one actual origin, then delegate to that Family's own searcher."""

        await _authorize_origin(principal, access, execution_context, origin)
        outcome = await self._runtime.artifacts.for_scope(origin).search(
            request.family,
            window.payload(request.payload),
            execution_context=execution_context,
        )
        return _origin_ranks(origin, outcome)

    async def open_hit(
        self,
        origin: ArtifactAddress,
        /,
        *,
        execution_context: ArtifactSearchExecutionContext | None = None,
    ) -> ArtifactRecord:
        """Read one exact revision after rechecking this origin's current permission.

        Exact read is deliberately separate from search permission: a hit survives a later
        publication of the same Artifact, and opening it never falls back to the current head.
        """

        principal, access = _invocation(execution_context)
        target = ArtifactAddress(scope_id=validate_scope_id(origin.scope_id), artifact=origin.artifact)
        await _authorize_origin(principal, access, execution_context, target.scope_id)
        return await self._runtime.records.for_scope(target.scope_id).get_artifact_revision(
            target.artifact.family,
            target.artifact.artifact_id,
            target.artifact.revision,
            execution_context=execution_context,
        )


class CrossScopeQueryApplication:
    """Select the cross-Scope query service for one existing Scope."""

    def __init__(self, runtime: Any) -> None:
        self._runtime = runtime

    def for_scope(self, scope_id: str, /) -> ScopedCrossScopeQuery:
        return ScopedCrossScopeQuery(self._runtime, scope_id)


class _OriginWindow:
    """Cap the Family's own effective window by each origin's share of the candidate budget.

    The effective window is decoded by the registered Family request model, so a default and an
    explicit value are treated identically and an invalid original value has already been reported.
    The allocation is a ceiling, never a work request: it only ever lowers that window, so it can
    neither widen a declared top-1 request nor inject a value above a Family's own limit.
    """

    def __init__(self, request: CrossScopeQueryRequest, declared: int) -> None:
        self._declared = declared
        self.per_origin = request.max_candidates // len(request.scopes)
        if self.per_origin < 1:
            raise CrossScopeQueryBoundsError(
                "max_candidates", "the candidate budget is smaller than the number of declared origins"
            )
        # The Family's effective window — explicit or default — is the work the caller asked for.
        self.bounded = self.per_origin < self._declared

    def payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return the payload to send, capping the effective window and never widening it."""

        if self.per_origin >= self._declared:
            return payload
        return {**payload, "limit": self.per_origin}


def _require_range(request: CrossScopeQueryRequest) -> None:
    """Reject a range no search could satisfy, before any origin is touched."""

    if len(request.scopes) > request.max_scopes:
        raise CrossScopeQueryBoundsError(
            "max_scopes", f"{len(request.scopes)} origins exceed the declared maximum of {request.max_scopes}"
        )
    if request.limit > request.max_candidates:
        raise CrossScopeQueryBoundsError("max_candidates", "the result limit cannot exceed the total candidate budget")


def _invocation_window(runtime: Any, request: CrossScopeQueryRequest) -> int:
    """Decode the caller's payload with its registered Family model and return its effective window.

    Validation happens here, on the original payload, through the same binding the search will use:
    a Family default, a Family-specific field, and a rejected out-of-range value all stay that
    Family's own contract, and no caller rewrites the limit before it has been admitted.
    """

    service = runtime._artifact_search
    if service is None:
        raise ArtifactSearchUnsupported(request.family, field="family")
    return service.parse_request(request.family, request.payload).limit


def _invocation(
    execution_context: ArtifactSearchExecutionContext | None,
) -> tuple[PrincipalRef | None, Any]:
    """Reject an invocation that carries neither a Principal nor trusted local policy."""

    if execution_context is None:
        raise AccessIdentityRequiredError
    context = execution_context
    if type(context.trusted_local) is not bool or (context.trusted_local and context.access is not None):
        raise AccessDeniedError
    if context.principal is None:
        if not context.trusted_local or context.access is not None:
            raise AccessIdentityRequiredError
    elif not isinstance(context.principal, PrincipalRef):
        raise AccessIdentityRequiredError
    if context.access is None and not context.trusted_local:
        raise AccessDeniedError
    return context.principal, context.access


async def _authorize_origin(
    principal: PrincipalRef | None,
    access: Any,
    execution_context: ArtifactSearchExecutionContext | None,
    origin: str,
) -> None:
    """Check current `scope.read` on the actual origin and nothing wider."""

    if access is None:
        return
    await access.require(
        principal,
        AccessAction.SCOPE_READ,
        ResourceRef.scope(origin),
        context=None if execution_context is None else execution_context.audit,
    )


def _origin_ranks(origin: str, outcome: Any) -> tuple[CrossScopeHit, ...]:
    """Keep a Family's own final order as that origin's rank, never a shared score scale."""

    return tuple(
        CrossScopeHit(
            origin=ArtifactAddress(scope_id=origin, artifact=match.artifact_ref),
            origin_rank=rank,
            retrieval_score=match.retrieval_score,
        )
        for rank, match in enumerate(outcome.matches, start=1)
    )


def _merge_by_origin_rank(
    searched: tuple[tuple[str, tuple[CrossScopeHit, ...]], ...],
    *,
    limit: int,
) -> tuple[CrossScopeHit, ...]:
    """Interleave origins rank by rank with the declared origin order as the stable tie break.

    Scores from different origins were produced by different indexes and are never compared.
    """

    if not searched:
        return ()
    merged: list[CrossScopeHit] = []
    seen: set[tuple[str, str, str, int]] = set()
    depth = max(len(ranked) for _, ranked in searched)
    for index in range(depth):
        for _origin, ranked in searched:
            if index >= len(ranked):
                continue
            hit = ranked[index]
            if hit.identity in seen:
                continue
            seen.add(hit.identity)
            merged.append(hit)
            if len(merged) >= limit:
                return tuple(merged)
    return tuple(merged)


def _coverage(
    searched: list[tuple[str, tuple[CrossScopeHit, ...]]],
    failed: list[str],
    bounded: list[str],
) -> CrossScopeCoverage:
    """Report complete coverage only when every declared origin was fully searched."""

    if failed:
        reason: CoverageReason = "origin_failure"
    elif bounded:
        reason = "candidate_budget"
    else:
        reason = "complete"
    return CrossScopeCoverage(
        complete=not failed and not bounded,
        reason=reason,
        searched_origins=tuple(origin for origin, _ in searched),
        failed_origins=tuple(failed),
        bounded_origins=tuple(bounded),
    )


__all__ = [
    "CrossScopeCoverage",
    "CrossScopeHit",
    "CrossScopeQueryApplication",
    "CrossScopeQueryBoundsError",
    "CrossScopeQueryError",
    "CrossScopeQueryRequest",
    "CrossScopeQueryResult",
    "CrossScopeQueryUnsupported",
    "ScopedCrossScopeQuery",
]
