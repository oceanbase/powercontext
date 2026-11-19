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

"""One-pass processing publishes the same kinds of changes as per-candidate reconciliation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Literal

import pytest

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryGenerationPipeline
from powercontext.builtin.inference import GenerationResult, InvalidInferenceOutputError, character_token_estimator
from powercontext.builtin.inference.models import ConversationReply, ConversationTurn
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_contexts
from tests.e2e.dream_support import _IndependentMemoryReconciler
from tests.e2e.test_atomic_memory_generation_merge import _RevisionExtractor

SCOPE = "project"
BUDGET_100 = "Rollout budget for the payments service is 100 requests per second."
BUDGET_200 = "Rollout budget for the payments service is 200 requests per second."
OWNER = "Rollout owner for the payments service is the platform team."


class _Conversation:
    """Answer each step from a script and record what the model was sent."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.turns: list[ConversationTurn[Any]] = []

    async def generate(self, turn: ConversationTurn[Any], /):
        self.turns.append(turn)
        reply = self.replies.pop(0)
        payload = json.loads(turn.message.split("\n", 1)[0])
        text = json.dumps(reply(payload) if callable(reply) else reply)
        return GenerationResult(output=ConversationReply(output=turn.check(text), history=(*turn.history, text)))


def _facts(*queries: str) -> dict[str, Any]:
    return {"items": [{"memory": query, "details": [query], "query": query} for query in queries]}


def _write(action: str, text: str, *targets: str, **extra: Any) -> dict[str, Any]:
    return {
        "action": action,
        "target_ids": list(targets),
        "kind": "fact",
        "text": text,
        "description": "Rollout fixture fact",
        "reason": "Apply the fixture decision.",
        **extra,
    }


def _run(
    tmp_path: Path,
    conversation: _Conversation,
    scenario,
    *,
    mode: Literal["per_candidate", "one_pass"] = "one_pass",
    input_tokens_limit: int = 24_000,
) -> None:
    pipeline = AtomicMemoryGenerationPipeline(
        extractor=_RevisionExtractor(),
        reconciler=_IndependentMemoryReconciler(),
        estimator=character_token_estimator(),
        conversation=conversation,
    )
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'one-pass.db'}"),
        runtime=RuntimeConfig(
            atomic_memory_related_mode="fts",
            atomic_memory_processing_mode=mode,
            atomic_memory_input_tokens_limit=input_tokens_limit,
        ),
    )

    async def run() -> None:
        async with open_builtin_contexts(config, candidate_pipeline=pipeline) as contexts:
            await contexts.get(SCOPE)
            await scenario(contexts, contexts.atomic_memory.for_scope(SCOPE))

    asyncio.run(run())


async def _texts(memory) -> set[str]:
    return {item.artifact.content.text for item in (await memory.list(limit=100)).items}


def test_create_then_revise_keeps_one_identity_and_both_sources(tmp_path: Path) -> None:
    conversation = _Conversation(
        _facts("rollout budget"),
        {"operations": [_write("create", BUDGET_100)]},
        _facts("rollout budget payments"),
        lambda shown: {"operations": [_write("revise", BUDGET_200, shown["existing_memories"][0]["id"])]},
    )

    async def scenario(contexts, memory) -> None:
        await contexts.records.capture_source(SCOPE, "content", "first", BUDGET_100, {})
        assert (await contexts.process_memory(SCOPE, 1)).processed
        (created,) = (await memory.list()).items
        await contexts.records.capture_source(SCOPE, "content", "second", BUDGET_200, {})
        result = await contexts.process_memory(SCOPE, 1)
        assert result.current_cursor == 2
        (revised,) = (await memory.list()).items
        assert revised.artifact.content.text == BUDGET_200
        assert revised.ref.artifact_id == created.ref.artifact_id and revised.ref.revision > created.ref.revision
        assert {ref.source_id for ref in revised.artifact.lineage.sources} == {"second"}
        first, second, third, fourth = conversation.turns
        # Each later step continues the earlier one under the same instructions.
        assert first.history == () and second.history[: len(first.history) + 1] == (*first.history, second.history[0])
        assert first.instructions == second.instructions == third.instructions
        assert json.loads(second.message.split("\n", 1)[0]) == {"existing_memories": []}
        assert [item["text"] for item in json.loads(fourth.message.split("\n", 1)[0])["existing_memories"]] == [
            BUDGET_100
        ]

    _run(tmp_path, conversation, scenario)


def test_noop_publishes_nothing_and_advances_the_cursor(tmp_path: Path) -> None:
    conversation = _Conversation(
        _facts("rollout budget"),
        {"operations": [_write("create", BUDGET_100)]},
        _facts("rollout budget"),
        lambda shown: {
            "operations": [
                {"action": "noop", "target_ids": [shown["existing_memories"][0]["id"]], "reason": "Already stated."}
            ]
        },
    )

    async def scenario(contexts, memory) -> None:
        await contexts.records.capture_source(SCOPE, "content", "first", BUDGET_100, {})
        await contexts.process_memory(SCOPE, 1)
        before = (await memory.list()).items
        await contexts.records.capture_source(SCOPE, "content", "again", BUDGET_100, {})
        assert (await contexts.process_memory(SCOPE, 1)).current_cursor == 2
        assert (await memory.list()).items == before

    _run(tmp_path, conversation, scenario)


def test_merge_combines_shown_memories_into_one(tmp_path: Path) -> None:
    combined = f"{BUDGET_100} {OWNER}"
    conversation = _Conversation(
        _facts("rollout payments"),
        lambda shown: {"operations": [_write("merge", combined, *(item["id"] for item in shown["existing_memories"]))]},
    )

    async def scenario(contexts, memory) -> None:
        await contexts.records.create_atomic_memories(
            SCOPE, ({"kind": "fact", "text": BUDGET_100}, {"kind": "fact", "text": OWNER})
        )
        originals = (await memory.list()).items
        await contexts.process_memory(SCOPE, 2)
        await contexts.records.capture_source(SCOPE, "content", "combine", combined, {})
        await contexts.process_memory(SCOPE, 1)
        (merged,) = (await memory.list()).items
        assert merged.artifact.content.text == combined
        assert {ref.artifact_id for ref in merged.artifact.lineage.artifacts} == {
            item.ref.artifact_id for item in originals
        }

    _run(tmp_path, conversation, scenario)


def test_created_memory_is_rechecked_against_a_memory_its_query_missed(tmp_path: Path) -> None:
    def recheck(listed: dict[str, Any]) -> dict[str, Any]:
        (entry,) = listed["recheck"]
        assert [item["text"] for item in entry["existing_memories"]] == [BUDGET_100]
        return {
            "operations": [{**_write("revise", BUDGET_200, entry["existing_memories"][0]["id"]), "id": entry["id"]}]
        }

    conversation = _Conversation(
        _facts("unrelated words"),
        {"operations": [_write("create", BUDGET_200)]},
        recheck,
    )

    async def scenario(contexts, memory) -> None:
        await contexts.records.create_atomic_memories(SCOPE, ({"kind": "fact", "text": BUDGET_100},))
        (original,) = (await memory.list()).items
        await contexts.process_memory(SCOPE, 1)
        await contexts.records.capture_source(SCOPE, "content", "second", BUDGET_200, {})
        await contexts.process_memory(SCOPE, 1)
        (revised,) = (await memory.list()).items
        assert revised.artifact.content.text == BUDGET_200
        assert revised.ref.artifact_id == original.ref.artifact_id
        assert len(conversation.turns) == 3 and not conversation.replies

    _run(tmp_path, conversation, scenario)


def test_rejected_operations_leave_the_window_unpublished(tmp_path: Path) -> None:
    conversation = _Conversation(_facts("rollout budget"), {"operations": [_write("revise", BUDGET_100, "M9")]})

    async def scenario(contexts, memory) -> None:
        await contexts.records.capture_source(SCOPE, "content", "first", BUDGET_100, {})
        with pytest.raises(InvalidInferenceOutputError) as captured:
            await contexts.process_memory(SCOPE, 1)
        assert captured.value.operation == "atomic-memory-one-pass"
        assert (await memory.list()).items == ()

    _run(tmp_path, conversation, scenario)


def test_window_over_the_step_two_budget_is_prepared_per_candidate(tmp_path: Path) -> None:
    conversation = _Conversation(_facts("rollout budget payments"))
    detail = " ".join(f"condition{index}" for index in range(120))

    async def scenario(contexts, memory) -> None:
        await contexts.records.create_atomic_memories(
            SCOPE, tuple({"kind": "fact", "text": f"{BUDGET_100} Case {index}: {detail}."} for index in range(5))
        )
        await contexts.process_memory(SCOPE, 5)
        await contexts.records.capture_source(SCOPE, "content", "owner", OWNER, {})
        assert (await contexts.process_memory(SCOPE, 1)).current_cursor == 6
        assert OWNER in await _texts(memory)
        # Step 1 ran; the five memories it recalled did not fit beside it, so step 2 was never sent.
        assert len(conversation.turns) == 1

    # One comparison of one memory fits this budget; step 1 plus all five memories does not.
    _run(tmp_path, conversation, scenario, input_tokens_limit=4300)


def test_default_mode_never_starts_a_conversation(tmp_path: Path) -> None:
    conversation = _Conversation()

    async def scenario(contexts, memory) -> None:
        await contexts.records.capture_source(SCOPE, "content", "first", BUDGET_100, {})
        await contexts.process_memory(SCOPE, 1)
        assert await _texts(memory) == {BUDGET_100}
        assert conversation.turns == []

    _run(tmp_path, conversation, scenario, mode="per_candidate")
