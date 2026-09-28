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

"""Runtime acceptance for the recall-sufficiency gate and its bounded expansion.

These tests exercise the real SQLite FTS recall path through the in-process runtime seam
(``ScopedContextApplication._prepare_build``) rather than the pure gate module: a three-term
query lets the round-zero admission floor reject a candidate that the lower round-one floor
re-admits, which is exactly the behaviour the gate exists to drive.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import ExperienceSearchOutcome
from powercontext.builtin.artifacts.memory import EmbeddingProfile, MemoryEntryInput
from powercontext.builtin.artifacts.search import AdmissionCounts
from powercontext.builtin.artifacts.topic_memory import (
    TopicMemoryContent,
    TopicMemoryDraft,
    prepare_topic_memory_projection,
)
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    PrepareContextRequest,
    RememberMemoryRequest,
    RuntimeConfig,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.application import (
    BuiltinRuntime,
    ScopedContextApplication,
    _RecallRoundOutcome,
)
from powercontext.builtin.runtime.prepared_context import PreparedContextBuild, PreparedMemoryCandidates
from powercontext.builtin.runtime.recall_sufficiency import (
    MEMORY_FAMILY,
    REASON_AT_MAX_ROUNDS,
    REASON_BUDGET_FLOOR,
    REASON_EXPANSION_FAILED,
    REASON_NO_CONTENT,
    REASON_SUFFICIENT,
    RecallSufficiencyGate,
)
from powercontext.builtin.scope import ScopeDraft

# A three-term query is required: the round-zero floor only differs from the round-one floor
# once the query has more than two Analyzer terms (``fts_query_requirements`` clamps a short
# query to a single required match).
_QUERY = "alpha beta gamma"
_MEMORY_ONLY = {"sections": [{"family": "memory", "limit": 8}]}
_TOPIC_MEMORY_ONLY = {"sections": [{"family": "topic-memory", "limit": 8}]}


class _RecallRoundLog:
    """Record every ``_recall_round`` invocation and optionally starve a later round."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.empty_from: int | None = None
        self.force_recoverable_family: str | None = None

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        original = ScopedContextApplication._recall_round
        log = self

        async def counted(
            application: ScopedContextApplication,
            request: PrepareContextRequest,
            scope_ids: Any,
            families: set[str],
            builder: Any,
            *,
            admission: Any,
            reuse: Any,
            topic_reuse: Any,
        ) -> Any:
            log.calls.append({"families": set(families), "admission": admission})
            if log.empty_from is not None and len(log.calls) >= log.empty_from:
                return _RecallRoundOutcome()
            result = await original(
                application,
                request,
                scope_ids,
                families,
                builder,
                admission=admission,
                reuse=reuse,
                topic_reuse=topic_reuse,
            )
            if log.force_recoverable_family is None:
                return result
            return _RecallRoundOutcome(
                memory=result.memory,
                experience=result.experience,
                topic_memory=result.topic_memory,
                admissions=(
                    AdmissionCounts(
                        family=log.force_recoverable_family,
                        scope_id=scope_ids[0],
                        retrieved=2,
                        admitted=1,
                    ),
                ),
                embedding_calls=result.embedding_calls,
                generation_calls=result.generation_calls,
            )

        monkeypatch.setattr(ScopedContextApplication, "_recall_round", counted)


@asynccontextmanager
async def _runtime(database: Path, runtime: RuntimeConfig | None = None) -> AsyncIterator[BuiltinRuntime]:
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
        runtime=runtime if runtime is not None else RuntimeConfig(),
    )
    async with open_builtin_runtime(config, scheduler_path=database.with_suffix(".scheduler.db")) as opened:
        yield opened


def _entry(text: str) -> MemoryEntryInput:
    return MemoryEntryInput(kind="fact", text=text)


async def _seed(runtime: BuiltinRuntime, scope_id: str, texts: list[str]) -> None:
    await runtime.memory.for_scope(scope_id).remember(
        RememberMemoryRequest(entries=tuple(_entry(text) for text in texts))
    )


async def _seed_topic_memories(runtime: BuiltinRuntime, scope_id: str, count: int) -> None:
    provider = cast(Any, runtime._provider)
    async with provider.database.transaction() as connection:
        for position in range(count):
            content = TopicMemoryContent(
                title=f"alpha beta topic {position:02d}",
                summary=f"Useful evidence {position}",
                detail="unrelated detail",
            )
            await provider.repositories.topic_memories.publish_create(
                connection,
                scope_id,
                f"topic-over-cap-{position:02d}",
                TopicMemoryDraft(content=content),
                prepare_topic_memory_projection(content),
            )


async def _create_scope(runtime: BuiltinRuntime, key: str) -> str:
    assert runtime.scopes is not None
    created = await runtime.scopes.create(
        ScopeDraft(title="Gate", summary="Recall gate acceptance", idempotency_key=key)
    )
    return created.scope_id


def _memory_request(*, max_bytes: int = 8000, assembly: dict[str, Any] | None = None) -> PrepareContextRequest:
    payload: dict[str, Any] = {"query": _QUERY, "max_bytes": max_bytes}
    payload["assembly"] = _MEMORY_ONLY if assembly is None else assembly
    return PrepareContextRequest.model_validate(payload)


async def _prepare_build(
    runtime: BuiltinRuntime,
    scope_id: str,
    request: PrepareContextRequest,
) -> tuple[PreparedContextBuild, Any]:
    application = runtime.context.for_scope(scope_id)
    async with runtime._scope_operation(scope_id) as scope:
        return await application._prepare_build(request, scope)


def test_default_off_matches_a_sufficient_round_zero_byte_for_byte(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "e1.db"
        request = _memory_request()

        async with _runtime(database) as disabled:
            scope_id = await _create_scope(disabled, "e1")
            await _seed(disabled, scope_id, [f"alpha beta gamma memory {index}" for index in range(8)])
            disabled_build = await _prepare_build(disabled, scope_id, request)
        disabled_build, disabled_effort = disabled_build
        assert disabled_effort is None
        assert len(log.calls) == 1

        log.calls.clear()
        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True, recall_gate_min_top_gap=0.0),
        ) as enabled:
            enabled_build = await _prepare_build(enabled, scope_id, request)

        enabled_build, effort = enabled_build
        assert effort is not None
        assert len(log.calls) == 1
        assert effort.rounds == 1
        assert effort.assessment == REASON_SUFFICIENT
        assert len(effort.candidates_by_round) == 1
        assert enabled_build.context.status == "ready"
        assert enabled_build.context.content == disabled_build.context.content
        assert enabled_build.context.content_bytes == disabled_build.context.content_bytes
        assert enabled_build.origins == disabled_build.origins

    asyncio.run(scenario())


def test_one_expansion_round_re_admits_a_candidate_blocked_at_round_zero(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "e2.db"
        async with _runtime(
            database,
            RuntimeConfig(
                recall_gate_enabled=True,
                recall_gate_min_candidates=2,
                recall_gate_min_top_score=0.0,
                recall_gate_min_top_gap=0.0,
                recall_gate_min_lexical_overlap=0.0,
            ),
        ) as runtime:
            scope_id = await _create_scope(runtime, "e2")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence", "alpha solo marker"])
            build, effort = await _prepare_build(runtime, scope_id, _memory_request())

        assert effort is not None
        assert effort.rounds == 2
        assert effort.expansion_actions == ("admission",)
        assert len(effort.candidates_by_round) == 2
        assert effort.candidates_by_round[1] > effort.candidates_by_round[0]
        assert len(log.calls) == 2
        assert build.context.content is not None
        assert "alpha beta gamma evidence" in build.context.content
        assert "alpha solo marker" in build.context.content

    asyncio.run(scenario())


def test_fully_admitted_memory_does_not_expand_when_no_candidate_can_be_recovered(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "fully-admitted.db"
        async with _runtime(
            database,
            RuntimeConfig(
                recall_gate_enabled=True,
                recall_gate_min_candidates=2,
                recall_gate_min_top_score=0.0,
                recall_gate_min_top_gap=0.0,
                recall_gate_min_lexical_overlap=0.0,
            ),
        ) as runtime:
            scope_id = await _create_scope(runtime, "fully-admitted")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence"])
            build, effort = await _prepare_build(runtime, scope_id, _memory_request())

        assert effort is not None
        assert effort.rounds == 1
        assert effort.expansion_actions == ()
        assert effort.candidates_by_round == (1,)
        assert len(log.calls) == 1
        assert build.context.content is not None
        assert "alpha beta gamma evidence" in build.context.content

    asyncio.run(scenario())


def test_recoverability_is_refreshed_after_an_expansion_round(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "recoverability-refresh.db"
        async with _runtime(
            database,
            RuntimeConfig(
                recall_gate_enabled=True,
                recall_gate_min_candidates=2,
                recall_gate_min_top_score=0.0,
                recall_gate_min_top_gap=0.0,
                recall_gate_min_lexical_overlap=0.0,
            ),
        ) as runtime:
            scope_id = await _create_scope(runtime, "recoverability-refresh")
            await _seed(runtime, scope_id, ["alpha evidence"])
            build, effort = await _prepare_build(runtime, scope_id, _memory_request())

        assert effort is not None
        assert effort.rounds == 2
        assert effort.expansion_actions == ("admission",)
        assert effort.candidates_by_round == (0, 1)
        assert len(log.calls) == 2
        assert build.context.content is not None
        assert "alpha evidence" in build.context.content

    asyncio.run(scenario())


def test_two_expansion_rounds_stop_at_max_rounds(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.force_recoverable_family = MEMORY_FAMILY
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "e3.db"
        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True, recall_gate_max_rounds=2, recall_gate_min_candidates=100),
        ) as runtime:
            scope_id = await _create_scope(runtime, "e3")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence", "alpha solo marker", "beta gamma marker"])
            _build, effort = await _prepare_build(runtime, scope_id, _memory_request())

        assert effort is not None
        assert effort.rounds == 3
        assert effort.assessment == REASON_AT_MAX_ROUNDS
        assert effort.expansion_actions == ("admission", "policy-floor")
        assert len(log.calls) == 3

    asyncio.run(scenario())


def test_topic_embedding_timeout_is_paid_once_per_prepare(tmp_path, monkeypatch) -> None:
    class SlowEmbedding:
        profile = EmbeddingProfile(profile_id="slow", model="slow", dimension=2)
        attempts = 0

        async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
            self.attempts += 1
            await asyncio.sleep(20)
            return EmbeddingResult(vectors=((1.0, 0.0),))

    log = _RecallRoundLog()
    log.force_recoverable_family = "topic-memory"
    log.install(monkeypatch)
    embedding = SlowEmbedding()

    async def scenario() -> None:
        async with _runtime(
            tmp_path / "topic-timeout.db",
            RuntimeConfig(recall_gate_enabled=True, recall_gate_max_rounds=2, recall_gate_min_candidates=100),
        ) as runtime:
            scope_id = await _create_scope(runtime, "topic-timeout")
            await _seed_topic_memories(runtime, scope_id, 1)
            runtime._topic_memory_embedding_model = embedding
            request = _memory_request(assembly=_TOPIC_MEMORY_ONLY)
            for attempt in (1, 2):
                build, effort = await _prepare_build(runtime, scope_id, request)
                assert effort.rounds == 3
                assert effort.expansion_actions == ("admission", "policy-floor")
                assert effort.added_embeddings == 0
                assert build.context.content is not None
                assert "alpha beta topic" in build.context.content
                # This is the external inference-attempt budget for one request.
                assert embedding.attempts == attempt

    asyncio.run(scenario())


def test_empty_scope_is_no_content_and_does_not_expand(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "e4.db"
        async with _runtime(database, RuntimeConfig(recall_gate_enabled=True)) as runtime:
            scope_id = await _create_scope(runtime, "e4")
            monkeypatch.setattr(runtime, "_experience_recall", None)
            monkeypatch.setattr(runtime, "_topic_memory_search", None)
            build, effort = await _prepare_build(runtime, scope_id, PrepareContextRequest(query=_QUERY))

        assert effort is not None
        assert effort.assessment == REASON_NO_CONTENT
        assert effort.rounds == 1
        assert len(log.calls) == 1
        assert build.context.status == "empty"

    asyncio.run(scenario())


def test_configured_experience_with_no_retrieved_rows_does_not_expand(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def no_experience(scope_id: str, _query: str, _limit: int, **_kwargs: Any) -> ExperienceSearchOutcome:
        return ExperienceSearchOutcome(
            admission=AdmissionCounts(family="experience", scope_id=scope_id, retrieved=0, admitted=0)
        )

    async def scenario() -> None:
        database = tmp_path / "empty-experience.db"
        async with _runtime(database, RuntimeConfig(recall_gate_enabled=True)) as runtime:
            scope_id = await _create_scope(runtime, "empty-experience")
            monkeypatch.setattr(runtime, "_experience_recall", no_experience)
            monkeypatch.setattr(runtime, "_topic_memory_search", None)
            build, effort = await _prepare_build(
                runtime,
                scope_id,
                PrepareContextRequest.model_validate({
                    "query": _QUERY,
                    "assembly": {"sections": [{"family": "experience", "limit": 2}]},
                }),
            )

        assert effort is not None
        assert effort.assessment == REASON_NO_CONTENT
        assert effort.rounds == 1
        assert len(log.calls) == 1
        assert build.context.status == "empty"

    asyncio.run(scenario())


def test_expansion_never_widens_the_selected_family_set(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.force_recoverable_family = MEMORY_FAMILY
    log.install(monkeypatch)

    async def unavailable(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("an unselected family must never be searched")  # noqa: TRY003

    async def scenario() -> None:
        database = tmp_path / "e5.db"
        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True, recall_gate_min_candidates=100),
        ) as runtime:
            scope_id = await _create_scope(runtime, "e5")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence", "alpha solo marker", "beta gamma marker"])
            monkeypatch.setattr(runtime, "_experience_recall", unavailable)
            monkeypatch.setattr(runtime, "_topic_memory_search", unavailable)
            build, _effort = await _prepare_build(runtime, scope_id, _memory_request())

        assert build.context.status == "ready"
        assert len(log.calls) == 3
        assert {frozenset(call["families"]) for call in log.calls} == {frozenset({"memory"})}

    asyncio.run(scenario())


def test_memory_head_change_during_expansion_fails_open_to_round_zero(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        database = tmp_path / "head-change.db"
        request = _memory_request()

        async with _runtime(database) as disabled:
            scope_id = await _create_scope(disabled, "head-change")
            await _seed(disabled, scope_id, ["alpha beta gamma evidence", "alpha solo marker"])
            baseline, _baseline_effort = await _prepare_build(disabled, scope_id, request)

        original = ScopedContextApplication._recall_round
        calls = 0

        async def changed_head(
            application: ScopedContextApplication,
            request: PrepareContextRequest,
            scope_ids: Any,
            families: set[str],
            builder: Any,
            *,
            admission: Any,
            reuse: Any,
            topic_reuse: Any,
        ) -> Any:
            nonlocal calls
            calls += 1
            if calls == 1:
                return await original(
                    application,
                    request,
                    scope_ids,
                    families,
                    builder,
                    admission=admission,
                    reuse=reuse,
                    topic_reuse=topic_reuse,
                )
            return _RecallRoundOutcome(
                memory=(
                    PreparedMemoryCandidates(
                        scope_id=scope_ids[0],
                        memory_ref=ArtifactRef(family="memory", artifact_id="memory", revision=999),
                        hits=(),
                    ),
                )
            )

        monkeypatch.setattr(ScopedContextApplication, "_recall_round", changed_head)
        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True, recall_gate_min_candidates=100),
        ) as runtime:
            build, effort = await _prepare_build(runtime, scope_id, request)

        assert effort is not None
        assert effort.assessment == REASON_EXPANSION_FAILED
        assert effort.rounds == 1
        assert calls == 2
        assert build.context.content == baseline.context.content
        assert build.origins == baseline.origins

    asyncio.run(scenario())


def test_later_rounds_never_remove_an_accumulated_candidate(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        database = tmp_path / "e6.db"
        request = _memory_request()

        async with _runtime(database) as disabled:
            scope_id = await _create_scope(disabled, "e6")
            await _seed(disabled, scope_id, ["alpha beta gamma evidence", "alpha solo marker", "beta gamma marker"])
            baseline, _baseline_effort = await _prepare_build(disabled, scope_id, request)

        log = _RecallRoundLog()
        log.force_recoverable_family = MEMORY_FAMILY
        log.empty_from = 3  # the round-two lookup returns no new candidates at all
        log.install(monkeypatch)
        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True, recall_gate_min_candidates=100),
        ) as runtime:
            build, effort = await _prepare_build(runtime, scope_id, request)

        assert effort is not None
        assert effort.rounds == 3
        assert len(log.calls) == 3
        # Round zero admits the two entries covering >= 2 query terms; round one lowers the floor
        # and re-admits the one-term entry (3 total); the starved round two adds nothing, so the
        # accumulated pool never shrinks after the round it was seeded at.
        assert effort.candidates_by_round == (2, 3, 3)
        assert all(origin in build.origins for origin in baseline.origins)

    asyncio.run(scenario())


def test_later_round_failure_preserves_committed_expansion_trace(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        database = tmp_path / "later-failure.db"
        request = _memory_request()

        async with _runtime(database) as disabled:
            scope_id = await _create_scope(disabled, "later-failure")
            await _seed(disabled, scope_id, ["alpha beta gamma evidence", "alpha solo marker", "beta gamma marker"])
            baseline, _baseline_effort = await _prepare_build(disabled, scope_id, request)

        original = ScopedContextApplication._recall_round
        calls = 0

        async def fails_on_second_expansion(
            application: ScopedContextApplication,
            request: PrepareContextRequest,
            scope_ids: Any,
            families: set[str],
            builder: Any,
            *,
            admission: Any,
            reuse: Any,
            topic_reuse: Any,
        ) -> Any:
            nonlocal calls
            calls += 1
            if calls == 3:
                raise RuntimeError("later expansion failed")  # noqa: TRY003
            result = await original(
                application,
                request,
                scope_ids,
                families,
                builder,
                admission=admission,
                reuse=reuse,
                topic_reuse=topic_reuse,
            )
            if calls == 2:
                return _RecallRoundOutcome(
                    memory=result.memory,
                    experience=result.experience,
                    topic_memory=result.topic_memory,
                    admissions=(
                        AdmissionCounts(
                            family=MEMORY_FAMILY,
                            scope_id=scope_ids[0],
                            retrieved=2,
                            admitted=1,
                        ),
                    ),
                    embedding_calls=result.embedding_calls,
                    generation_calls=result.generation_calls,
                )
            return result

        monkeypatch.setattr(ScopedContextApplication, "_recall_round", fails_on_second_expansion)
        async with _runtime(
            database,
            RuntimeConfig(recall_gate_enabled=True, recall_gate_min_candidates=100),
        ) as runtime:
            build, effort = await _prepare_build(runtime, scope_id, request)

        assert effort is not None
        assert effort.assessment == REASON_EXPANSION_FAILED
        assert effort.rounds == 2
        assert effort.expansion_actions == ("admission",)
        assert len(effort.candidates_by_round) == 2
        assert calls == 3
        assert build.context.content == baseline.context.content
        assert build.origins == baseline.origins

    asyncio.run(scenario())


def test_gate_failure_fails_open_to_the_round_zero_result(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        database = tmp_path / "e7.db"
        request = _memory_request()

        async with _runtime(database) as disabled:
            scope_id = await _create_scope(disabled, "e7")
            await _seed(disabled, scope_id, ["alpha beta gamma evidence", "alpha solo marker"])
            baseline, _baseline_effort = await _prepare_build(disabled, scope_id, request)

        def exploded(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError("gate exploded")  # noqa: TRY003

        monkeypatch.setattr(RecallSufficiencyGate, "assess", exploded)
        async with _runtime(database, RuntimeConfig(recall_gate_enabled=True)) as runtime:
            build, effort = await _prepare_build(runtime, scope_id, request)

        assert effort is not None
        assert effort.assessment == REASON_EXPANSION_FAILED
        assert effort.rounds == 1
        assert effort.expansion_actions == ()
        assert len(effort.candidates_by_round) == 1
        assert build.context.content == baseline.context.content
        assert build.origins == baseline.origins

    asyncio.run(scenario())


def test_empty_assembly_returns_before_any_recall(tmp_path, monkeypatch) -> None:
    async def scenario() -> None:
        database = tmp_path / "e8.db"
        async with _runtime(database, RuntimeConfig(recall_gate_enabled=True)) as runtime:
            scope_id = await _create_scope(runtime, "e8")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence"])

            def forbidden(*_args: Any, **_kwargs: Any) -> Any:
                raise AssertionError("empty assembly must not reach the recall seam")  # noqa: TRY003

            monkeypatch.setattr(ScopedContextApplication, "_prepare_build", forbidden)
            result = await runtime.context.for_scope(scope_id).prepare(
                PrepareContextRequest.model_validate({"query": _QUERY, "assembly": {"sections": []}})
            )

        assert result.status == "empty"
        assert result.content is None

    asyncio.run(scenario())


def test_budget_floor_short_circuits_the_gate(tmp_path, monkeypatch) -> None:
    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "e9.db"
        async with _runtime(database, RuntimeConfig(recall_gate_enabled=True)) as runtime:
            scope_id = await _create_scope(runtime, "e9")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence", "alpha solo marker"])
            _build, effort = await _prepare_build(runtime, scope_id, _memory_request(max_bytes=512))

        assert effort is not None
        assert effort.assessment == REASON_BUDGET_FLOOR
        assert effort.rounds == 1
        assert len(log.calls) == 1

    asyncio.run(scenario())


def test_prepare_delivers_final_recall_effort_to_the_optional_sink(tmp_path) -> None:
    async def scenario() -> None:
        observed = []

        async def sink(effort) -> None:
            observed.append(effort)

        database = tmp_path / "sink.db"
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
            runtime=RuntimeConfig(
                recall_gate_enabled=True,
                recall_gate_min_candidates=1,
                recall_gate_min_top_gap=0.0,
            ),
        )
        async with open_builtin_runtime(
            config,
            scheduler_path=database.with_suffix(".scheduler.db"),
            recall_effort_sink=sink,
        ) as runtime:
            scope_id = await _create_scope(runtime, "sink")
            await _seed(runtime, scope_id, ["alpha beta gamma " + "evidence " * 500])
            prepared = await runtime.context.for_scope(scope_id).prepare(_memory_request(max_bytes=800))

        assert prepared.status == "ready"
        assert len(observed) == 1
        assert observed[0].rounds == 1
        assert observed[0].truncated_items == 1
        assert observed[0].dropped_items == 0
        assert not hasattr(prepared, "recall_effort")

    asyncio.run(scenario())


def test_topic_memory_candidate_cap_does_not_look_recoverable_when_all_candidates_are_eligible(
    tmp_path,
    monkeypatch,
) -> None:
    log = _RecallRoundLog()
    log.install(monkeypatch)

    async def scenario() -> None:
        database = tmp_path / "topic-over-cap.db"
        config = RuntimeConfig(
            recall_gate_enabled=True,
            recall_gate_min_candidates=2,
            recall_gate_min_lexical_overlap=0.9,
        )
        async with _runtime(database, config) as runtime:
            scope_id = await _create_scope(runtime, "topic-over-cap")
            await _seed_topic_memories(runtime, scope_id, 40)
            request = PrepareContextRequest.model_validate({
                "query": "alpha beta gamma delta epsilon",
                "max_bytes": 8000,
                "assembly": _TOPIC_MEMORY_ONLY,
            })
            _build, effort = await _prepare_build(runtime, scope_id, request)

        assert effort is not None
        assert effort.rounds == 1
        assert effort.candidates_by_round == (8,)
        assert len(log.calls) == 1

    asyncio.run(scenario())


def test_recall_effort_sink_failure_does_not_fail_prepare(tmp_path, caplog) -> None:
    async def scenario() -> None:
        async def sink(_effort) -> None:
            raise RuntimeError("sink failed")  # noqa: TRY003

        database = tmp_path / "sink-failure.db"
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
            runtime=RuntimeConfig(recall_gate_enabled=True),
        )
        async with open_builtin_runtime(
            config,
            scheduler_path=database.with_suffix(".scheduler.db"),
            recall_effort_sink=sink,
        ) as runtime:
            scope_id = await _create_scope(runtime, "sink-failure")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence"])
            prepared = await runtime.context.for_scope(scope_id).prepare(_memory_request())

        assert prepared.status == "ready"
        assert any(record.message == "Recall effort sink failed" for record in caplog.records)

    asyncio.run(scenario())


def test_disabled_gate_does_not_call_recall_effort_sink(tmp_path) -> None:
    async def scenario() -> None:
        calls = 0

        async def sink(_effort) -> None:
            nonlocal calls
            calls += 1

        database = tmp_path / "sink-disabled.db"
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
            runtime=RuntimeConfig(recall_gate_enabled=False),
        )
        async with open_builtin_runtime(
            config,
            scheduler_path=database.with_suffix(".scheduler.db"),
            recall_effort_sink=sink,
        ) as runtime:
            scope_id = await _create_scope(runtime, "sink-disabled")
            await _seed(runtime, scope_id, ["alpha beta gamma evidence"])
            prepared = await runtime.context.for_scope(scope_id).prepare(_memory_request())

        assert prepared.status == "ready"
        assert calls == 0

    asyncio.run(scenario())
