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

"""Cross-Scope query behavior through the public Runtime applications."""

from __future__ import annotations

import asyncio

import pytest

from powercontext.artifacts import ArtifactAddress, ArtifactRef, ArtifactSearchExecutionContext
from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    ApproveCandidateRequest,
    BuiltinConfig,
    BuiltinRuntime,
    CaptureSource,
    ProposeExperienceRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.cross_scope_query import (
    CrossScopeQueryBoundsError,
    CrossScopeQueryRequest,
    CrossScopeQueryUnsupported,
)
from powercontext.builtin.scope import ScopeDraft, ScopeMutation, ScopeSelection


async def _scope(runtime: BuiltinRuntime, key: str, *, parent: str | None = None) -> str:
    assert runtime.scopes is not None
    return (
        await runtime.scopes.create(ScopeDraft(title=key, summary=key, idempotency_key=key, parent_scope_id=parent))
    ).scope_id


async def _experience(
    runtime: BuiltinRuntime, scope: str, marker: str, *, target: ArtifactRef | None = None
) -> ArtifactAddress:
    """Commit one exact Experience revision through the ordinary propose/approve path."""

    source = await runtime.sources.for_scope(scope).capture(
        CaptureSource(source_id=marker, content=marker, metadata={})
    )
    candidate = await runtime.experience.for_scope(scope).propose(
        ProposeExperienceRequest(
            proposal=ExperienceContent(
                situation=marker, action="Inspect the contract", outcome="Reviewed", lesson="Reuse the runtime"
            ),
            sources=(source.source_ref,),
            artifacts=() if target is None else (target,),
            target=target,
        )
    )
    approved = await runtime.review.for_scope(scope).approve(
        ApproveCandidateRequest(candidate_id=candidate.candidate_id, expected_version=candidate.version)
    )
    assert approved.result_artifact is not None
    return ArtifactAddress(scope_id=scope, artifact=approved.result_artifact)


def _trusted() -> ArtifactSearchExecutionContext:
    """The unconfigured embedding policy: no Access service, explicitly trusted local."""

    return ArtifactSearchExecutionContext(trusted_local=True)


def test_cross_scope_query_keeps_same_named_artifacts_distinct_and_reads_exactly() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            orders = await _scope(runtime, "orders")
            interface = await _scope(runtime, "interface", parent=orders)
            operations = await _scope(runtime, "operations")
            # Identical content in two Scopes produces two distinct origins, not one merged hit.
            left = await _experience(runtime, interface, "retrymarker")
            right = await _experience(runtime, operations, "retrymarker")
            assert left.artifact != right.artifact or left.scope_id != right.scope_id

            # Structural descendants only: the referenced external Scope is not a member.
            scopes = runtime.scopes
            assert scopes is not None
            order_scope = await scopes.get(orders)
            await scopes.update(
                orders,
                ScopeMutation(
                    expected_version=order_scope.version,
                    title=order_scope.title,
                    summary=order_scope.summary,
                    context_references=(operations,),
                ),
            )
            subtree = await scopes.resolve_range(ScopeSelection(mode="subtree", root_scope_id=orders))
            assert set(subtree.scope_ids) == {orders, interface}
            assert operations not in subtree.scope_ids

            query = runtime.cross_scope.for_scope(orders)
            result = await query.search(
                CrossScopeQueryRequest(
                    family="experience",
                    payload={"query": "retrymarker", "limit": 10},
                    scopes=(interface, operations),
                    # Admit each origin's share of the declared window, or complete
                    # coverage would be refused rather than silently under-searched.
                    max_candidates=64,
                ),
                execution_context=_trusted(),
            )

            assert {hit.origin.scope_id for hit in result.hits} == {interface, operations}
            assert len({hit.identity for hit in result.hits}) == len(result.hits) == 2
            assert result.coverage.complete is True
            assert result.coverage.reason == "complete"
            assert set(result.coverage.searched_origins) == {interface, operations}

            # A later revision of one origin does not move an earlier hit: opening it returns the
            # exact revision that was hit, never the new head.
            hit_before = next(hit for hit in result.hits if hit.origin.scope_id == interface)
            newer = await _experience(runtime, interface, "lateremarker", target=hit_before.origin.artifact)
            assert newer.artifact.revision == hit_before.origin.artifact.revision + 1

            # Exact readback returns the revision that was hit, from its own origin.
            for hit in result.hits:
                record = await query.open_hit(hit.origin, execution_context=_trusted())
                assert (record.scope_id, record.family, record.artifact_id, record.revision) == (
                    hit.origin.scope_id,
                    hit.origin.artifact.family,
                    hit.origin.artifact.artifact_id,
                    hit.origin.artifact.revision,
                )
                assert record.content["situation"] == "retrymarker"
            reopened = await query.open_hit(hit_before.origin, execution_context=_trusted())
            assert reopened.revision == hit_before.origin.artifact.revision
            assert reopened.content["situation"] == "retrymarker"

    asyncio.run(scenario())


def test_cross_scope_query_reports_partial_coverage_and_refuses_impossible_budgets() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            orders = await _scope(runtime, "orders")
            interface = await _scope(runtime, "interface", parent=orders)
            await _experience(runtime, interface, "budgetmarker")
            query = runtime.cross_scope.for_scope(orders)

            # A missing origin is a reported gap in partial mode and a failure in complete mode.
            partial = await query.search(
                CrossScopeQueryRequest(
                    family="experience",
                    payload={"query": "budgetmarker", "limit": 200},
                    scopes=(interface, "scp_missing"),
                    coverage="partial",
                ),
                execution_context=_trusted(),
            )
            assert partial.coverage.complete is False
            assert partial.coverage.reason == "origin_failure"
            assert partial.coverage.failed_origins == ("scp_missing",)
            assert [hit.origin.scope_id for hit in partial.hits] == [interface]

            with pytest.raises(Exception):  # noqa: B017 -- the owning Scope error is asserted elsewhere
                await query.search(
                    CrossScopeQueryRequest(
                        family="experience",
                        payload={"query": "budgetmarker", "limit": 200},
                        scopes=(interface, "scp_missing"),
                    ),
                    execution_context=_trusted(),
                )

            # An under-sized candidate budget cannot honestly report complete coverage.
            with pytest.raises(CrossScopeQueryBoundsError) as bounded:
                await query.search(
                    CrossScopeQueryRequest(
                        family="experience",
                        payload={"query": "budgetmarker", "limit": 200},
                        scopes=(interface,),
                        max_candidates=4,
                    ),
                    execution_context=_trusted(),
                )
            assert bounded.value.field == "max_candidates"

            # A candidate restriction the current projection cannot honour is refused, not faked.
            with pytest.raises(CrossScopeQueryUnsupported):
                await query.search(
                    CrossScopeQueryRequest(
                        family="experience",
                        payload={"query": "budgetmarker"},
                        scopes=(interface,),
                        restrict_to=ArtifactAddress(
                            scope_id=interface,
                            artifact=(await _experience(runtime, interface, "otherml")).artifact,
                        ).artifact,
                    ),
                    execution_context=_trusted(),
                )

    asyncio.run(scenario())
