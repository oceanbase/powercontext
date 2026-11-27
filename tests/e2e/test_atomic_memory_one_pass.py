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
import logging
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
        # The scripted conversation keeps its history as the texts themselves.
        history = (*(str(message) for message in turn.history), turn.message, text)
        return GenerationResult(output=ConversationReply(output=turn.check(text), history=history, transcript=history))


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
        assert first.history == () and second.history[0] == first.message and third.history == ()
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


NOTES = "Rollout notes for the payments service: owner and budget are tracked here."


def _published(tmp_path: Path, *replies) -> tuple[set[str], InvalidInferenceOutputError | None]:
    """Process a window stating the budget and the owner beside the notes memory; return the store and any rejection."""

    conversation = _Conversation(*replies)
    outcome: list[Any] = []

    async def scenario(contexts, memory) -> None:
        await contexts.records.create_atomic_memories(SCOPE, ({"kind": "fact", "text": NOTES},))
        await contexts.process_memory(SCOPE, 1)
        await contexts.records.capture_source(SCOPE, "content", "second", f"{BUDGET_200} {OWNER}", {})
        try:
            await contexts.process_memory(SCOPE, 1)
        except InvalidInferenceOutputError as error:
            outcome.append(error)
        outcome.append(await _texts(memory))

    _run(tmp_path, conversation, scenario)
    return outcome[-1], outcome[0] if len(outcome) == 2 else None


def test_two_operations_revising_one_shown_memory_are_rejected(tmp_path: Path) -> None:
    def each_on_its_own(shown: dict[str, Any]) -> dict[str, Any]:
        (notes,) = shown["existing_memories"]
        return {
            "operations": [
                _write("revise", f"{NOTES} {BUDGET_200}", notes["id"]),
                _write("revise", f"{NOTES} {OWNER}", notes["id"]),
            ]
        }

    def combined(shown: dict[str, Any]) -> dict[str, Any]:
        (notes,) = shown["existing_memories"]
        return {
            "operations": [
                _write("revise", f"{NOTES} {BUDGET_200} {OWNER}", notes["id"]),
                {"action": "noop", "target_ids": [notes["id"]], "reason": "The revision above states it."},
            ]
        }

    # The second revision would replace the first one's text and lose the budget.
    texts, rejected = _published(tmp_path / "separate", _facts("rollout notes", "payments service"), each_on_its_own)
    assert rejected is not None and "only one operation" in str(rejected)
    assert texts == {NOTES}

    texts, rejected = _published(tmp_path / "combined", _facts("rollout notes", "payments service"), combined)
    assert rejected is None and texts == {f"{NOTES} {BUDGET_200} {OWNER}"}


def test_two_rechecked_memories_revising_one_memory_are_rejected(tmp_path: Path) -> None:
    creates = {"operations": [_write("create", BUDGET_200), _write("create", OWNER)]}

    def each_on_its_own(listed: dict[str, Any]) -> dict[str, Any]:
        first, second = listed["recheck"]
        # Both created memories recalled the notes, which carry one label in both lists.
        (label,) = {item["id"] for entry in listed["recheck"] for item in entry["existing_memories"]}
        return {
            "operations": [
                {**_write("revise", f"{NOTES} {first['text']}", label), "id": first["id"]},
                {**_write("revise", f"{NOTES} {second['text']}", label), "id": second["id"]},
            ]
        }

    def combined(listed: dict[str, Any]) -> dict[str, Any]:
        first, second = listed["recheck"]
        label = first["existing_memories"][0]["id"]
        return {
            "operations": [
                {**_write("revise", f"{NOTES} {first['text']} {second['text']}", label), "id": first["id"]},
                {"id": second["id"], "action": "noop", "target_ids": [label], "reason": "The revision states it."},
            ]
        }

    texts, rejected = _published(tmp_path / "separate", _facts("unrelated words"), creates, each_on_its_own)
    assert rejected is not None and "only one operation" in str(rejected)
    assert texts == {NOTES}

    texts, rejected = _published(tmp_path / "combined", _facts("unrelated words"), creates, combined)
    assert rejected is None and texts == {f"{NOTES} {BUDGET_200} {OWNER}"}


def test_rejected_operations_leave_the_window_unpublished(tmp_path: Path) -> None:
    conversation = _Conversation(_facts("rollout budget"), {"operations": [_write("revise", BUDGET_100, "M9")]})

    async def scenario(contexts, memory) -> None:
        await contexts.records.capture_source(SCOPE, "content", "first", BUDGET_100, {})
        with pytest.raises(InvalidInferenceOutputError) as captured:
            await contexts.process_memory(SCOPE, 1)
        assert captured.value.operation == "atomic-memory-one-pass"
        assert (await memory.list()).items == ()

    _run(tmp_path, conversation, scenario)


def test_reply_in_another_shape_is_rejected_instead_of_read_as_no_operation(tmp_path: Path) -> None:
    conversation = _Conversation(_facts("rollout budget"), {"candidates": [_write("create", BUDGET_100)]})

    async def scenario(contexts, memory) -> None:
        await contexts.records.capture_source(SCOPE, "content", "first", BUDGET_100, {})
        with pytest.raises(InvalidInferenceOutputError) as captured:
            await contexts.process_memory(SCOPE, 1)
        assert captured.value.operation == "atomic-memory-one-pass"
        assert (await memory.list()).items == ()

    _run(tmp_path, conversation, scenario)


def test_step_two_must_return_an_operation_for_every_listed_entry(tmp_path: Path) -> None:
    def process(path: Path, entries: dict[str, Any], operations: dict[str, Any]):
        outcome: list[Any] = []

        async def scenario(contexts, memory) -> None:
            await contexts.records.capture_source(SCOPE, "content", "first", f"{BUDGET_100} {OWNER}", {})
            try:
                outcome.append((await contexts.process_memory(SCOPE, 1)).current_cursor)
            except InvalidInferenceOutputError as error:
                outcome.append(error)
            outcome.append(await _texts(memory))

        path.mkdir()
        _run(path, _Conversation(entries, operations), scenario)
        return outcome

    # Two listed memories and no operation: dropping them silently would consume the window.
    rejected, texts = process(tmp_path / "none", _facts("rollout budget", "rollout owner"), {"operations": []})
    assert isinstance(rejected, InvalidInferenceOutputError) and rejected.operation == "atomic-memory-one-pass"
    assert texts == set()

    # One operation for two entries leaves one listed memory undecided.
    one = {"operations": [_write("create", BUDGET_100)]}
    rejected, texts = process(tmp_path / "fewer", _facts("rollout budget", "rollout owner"), one)
    assert isinstance(rejected, InvalidInferenceOutputError) and rejected.operation == "atomic-memory-one-pass"
    assert texts == set()

    # Declining an entry with a noop without target still counts as its decision.
    declined = {
        "operations": [
            _write("create", BUDGET_100),
            {"action": "noop", "target_ids": [], "reason": "The owner is not worth keeping."},
        ]
    }
    assert process(tmp_path / "declined", _facts("rollout budget", "rollout owner"), declined) == [1, {BUDGET_100}]

    # Entries repeating a query are one entry, and a step 1 without entries needs no operation.
    assert process(tmp_path / "repeated", _facts("rollout budget", "rollout budget"), one) == [1, {BUDGET_100}]
    assert process(tmp_path / "empty", _facts(), {"operations": []}) == [1, set()]


def _input_estimate(turn: ConversationTurn[Any]) -> int:
    return character_token_estimator().estimate("\n".join((turn.instructions, *map(str, turn.history), turn.message)))


def test_recheck_budget_counts_the_step_two_reply_it_resends(tmp_path: Path) -> None:
    def conversation() -> _Conversation:
        operation = {**_write("create", BUDGET_200), "reason": " ".join(["Keep the new rollout budget."] * 200)}
        return _Conversation(
            _facts("unrelated words"),
            {"operations": [operation]},
            lambda listed: {"operations": [{**_write("create", BUDGET_200), "id": listed["recheck"][0]["id"]}]},
        )

    async def prepare(contexts) -> None:
        await contexts.records.create_atomic_memories(SCOPE, ({"kind": "fact", "text": BUDGET_100},))
        await contexts.process_memory(SCOPE, 1)
        await contexts.records.capture_source(SCOPE, "content", "second", BUDGET_200, {})

    measured = conversation()

    async def measure(contexts, memory) -> None:
        await prepare(contexts)
        await contexts.process_memory(SCOPE, 1)
        assert len(measured.turns) == 3

    (tmp_path / "measure").mkdir()
    _run(tmp_path / "measure", measured, measure)
    step_two, recheck = (_input_estimate(turn) for turn in measured.turns[1:])
    # The recheck resends the long step 2 reply, so it is the larger input by more than its own message.
    assert recheck - step_two > character_token_estimator().estimate(measured.turns[2].message)

    limited = conversation()

    async def scenario(contexts, memory) -> None:
        await prepare(contexts)
        with pytest.raises(InvalidInferenceOutputError) as captured:
            await contexts.process_memory(SCOPE, 1)
        assert captured.value.operation == "atomic-memory-input-budget"
        assert len(limited.turns) == 2
        assert await _texts(memory) == {BUDGET_100}

    _run(tmp_path, limited, scenario, input_tokens_limit=recheck - 1)


def test_split_rechecks_are_each_budgeted_as_their_own_request(tmp_path: Path) -> None:
    former_owner = "Rollout owner for the payments service is the infrastructure team."

    def recheck(listed: dict[str, Any]) -> dict[str, Any]:
        operations = []
        for entry in listed["recheck"]:
            topic = "budget" if "budget" in entry["text"] else "owner"
            targets = [item["id"] for item in entry["existing_memories"] if topic in item["text"]]
            operations.append({**_write("revise" if targets else "create", entry["text"], *targets), "id": entry["id"]})
        return {"operations": operations}

    def conversation() -> _Conversation:
        return _Conversation(
            _facts("unrelated words"),
            {"operations": [_write("create", BUDGET_200), _write("create", OWNER)]},
            recheck,
            recheck,
        )

    async def prepare(contexts) -> None:
        await contexts.records.create_atomic_memories(
            SCOPE, ({"kind": "fact", "text": BUDGET_100}, {"kind": "fact", "text": former_owner})
        )
        await contexts.process_memory(SCOPE, 2)
        await contexts.records.capture_source(SCOPE, "content", "second", f"{BUDGET_200} {OWNER}", {})

    measured = conversation()

    async def measure(contexts, memory) -> None:
        await prepare(contexts)
        await contexts.process_memory(SCOPE, 1)
        # Within the budget both created memories are rechecked in one request.
        assert len(measured.turns) == 3

    (tmp_path / "measure").mkdir()
    _run(tmp_path / "measure", measured, measure)

    limited = conversation()

    async def scenario(contexts, memory) -> None:
        await prepare(contexts)
        await contexts.process_memory(SCOPE, 1)
        first, second = limited.turns[2:]
        # Both rechecks continue step 2; the second does not carry the first one's message.
        assert first.history == second.history and first.message not in second.history
        assert await _texts(memory) == {BUDGET_200, OWNER}

    # One request for both created memories is one token over; each on its own fits.
    _run(tmp_path, limited, scenario, input_tokens_limit=_input_estimate(measured.turns[2]) - 1)


def test_split_recheck_keeps_created_memories_when_revise_identity_correction_is_exhausted(tmp_path: Path) -> None:
    former_owner = "Rollout owner for the payments service is the infrastructure team."
    merged = f"{BUDGET_200} {former_owner}"

    def merge_both(listed: dict[str, Any]) -> dict[str, Any]:
        operations = []
        for entry in listed["recheck"]:
            labels = [item["id"] for item in entry["existing_memories"]]
            if "budget" in entry["text"]:
                # Both published memories were recalled for this created memory and are merged into it.
                assert len(labels) == 2
                operations.append({**_write("merge", merged, *labels), "id": entry["id"]})
            else:
                operations.append({**_write("revise", entry["text"], labels[0]), "id": entry["id"]})
        return {"operations": operations}

    def conversation(*rechecks) -> _Conversation:
        return _Conversation(
            _facts("unrelated words"),
            {"operations": [_write("create", BUDGET_200), _write("create", OWNER)]},
            *rechecks,
        )

    async def prepare(contexts) -> None:
        await contexts.records.create_atomic_memories(
            SCOPE, ({"kind": "fact", "text": BUDGET_100}, {"kind": "fact", "text": former_owner})
        )
        await contexts.process_memory(SCOPE, 2)
        await contexts.records.capture_source(SCOPE, "content", "second", f"{BUDGET_200} {OWNER}", {})

    def noop_owner(listed: dict[str, Any]) -> dict[str, Any]:
        first, second = listed["recheck"]
        reply = merge_both({"recheck": [first]})
        label = second["existing_memories"][0]["id"]
        return {
            "operations": [
                *reply["operations"],
                {"id": second["id"], "action": "noop", "target_ids": [label], "reason": "The merge states it."},
            ]
        }

    measured = conversation(noop_owner)

    async def measure(contexts, memory) -> None:
        await prepare(contexts)
        await contexts.process_memory(SCOPE, 1)
        assert len(measured.turns) == 3

    (tmp_path / "measure").mkdir()
    _run(tmp_path / "measure", measured, measure)

    limited = conversation(merge_both, merge_both)

    async def scenario(contexts, memory) -> None:
        await prepare(contexts)
        cursor = await contexts.process_memory(SCOPE, 1)
        # The second recheck saw the merged memory under an earlier label and kept answering revise for two
        # published identities. Its created memory is kept; the earlier merge stays as decided.
        assert len(limited.turns) == 4 and not limited.replies
        assert cursor.current_cursor == cursor.high_watermark > cursor.previous_cursor
        assert cursor.remaining_work is False
        assert await _texts(memory) == {merged, OWNER}

    _run(tmp_path, limited, scenario, input_tokens_limit=_input_estimate(measured.turns[2]) - 1)


def _one_pass_events(caplog) -> list[dict[str, object]]:
    fields = ("outcome", "fallback_reason", "estimated_input_tokens", "effective_budget")
    return [
        {field: getattr(record, field) for field in fields if hasattr(record, field)}
        for record in caplog.records
        if getattr(record, "event", None) == "atomic_memory.one_pass"
    ]


def test_window_over_the_step_two_budget_is_prepared_per_candidate(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
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
    (event,) = _one_pass_events(caplog)
    assert event["outcome"] == "fallback" and event["fallback_reason"] == "operations_input_over_budget"
    # The operator sees by how much the request exceeded the budget, and none of the evidence or memory text.
    assert event["effective_budget"] == 4300 and isinstance(estimated := event["estimated_input_tokens"], int)
    assert estimated > 4300
    assert not any("condition0" in record.getMessage() for record in caplog.records)


def test_window_over_the_step_one_budget_is_prepared_per_candidate(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
    measured = _Conversation(_facts("rollout owner"), {"operations": [_write("create", OWNER)]})
    limited = _Conversation()

    async def scenario(contexts, memory) -> None:
        await contexts.records.capture_source(SCOPE, "content", "owner", OWNER, {})
        assert (await contexts.process_memory(SCOPE, 1)).current_cursor == 1
        assert await _texts(memory) == {OWNER}

    (tmp_path / "measure").mkdir()
    _run(tmp_path / "measure", measured, scenario)
    caplog.clear()
    # Step 1 is one token over; the per-candidate extraction of the same evidence has shorter instructions and fits.
    limit = _input_estimate(measured.turns[0]) - 1
    _run(tmp_path, limited, scenario, input_tokens_limit=limit)
    assert limited.turns == []
    assert _one_pass_events(caplog) == [
        {
            "outcome": "fallback",
            "fallback_reason": "entries_input_over_budget",
            "estimated_input_tokens": limit + 1,
            "effective_budget": limit,
        }
    ]


def test_window_prepared_in_one_pass_reports_success(tmp_path: Path, caplog) -> None:
    caplog.set_level(logging.INFO)
    conversation = _Conversation(_facts("rollout budget"), {"operations": [_write("create", BUDGET_100)]})

    async def scenario(contexts, memory) -> None:
        await contexts.records.capture_source(SCOPE, "content", "first", BUDGET_100, {})
        await contexts.process_memory(SCOPE, 1)

    _run(tmp_path, conversation, scenario)
    assert _one_pass_events(caplog) == [{"outcome": "success"}]


def test_default_mode_never_starts_a_conversation(tmp_path: Path) -> None:
    conversation = _Conversation()

    async def scenario(contexts, memory) -> None:
        await contexts.records.capture_source(SCOPE, "content", "first", BUDGET_100, {})
        await contexts.process_memory(SCOPE, 1)
        assert await _texts(memory) == {BUDGET_100}
        assert conversation.turns == []

    _run(tmp_path, conversation, scenario, mode="per_candidate")
