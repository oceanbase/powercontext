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

"""A reconciliation decision that breaks a workset rule is returned to the model instead of failing the flush."""

from __future__ import annotations

import asyncio
import json

from pydantic_ai.embeddings import TestEmbeddingModel
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, RetryPromptPart, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.inference import InvalidInferenceOutputError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, RuntimeConfig, open_builtin_runtime
from powercontext.builtin.runtime.config import InferenceConfig
from powercontext.builtin.scope import ScopeDraft

REVISE_RULE = "revise must have exactly one published identity"


class _Provider:
    """One Source yields two candidates; the second is first 'revised' into the unpublished first candidate."""

    def __init__(self, *, corrects: bool) -> None:
        self.corrects = corrects
        self.retry_feedback: list[str] = []
        self.comparisons = 0

    async def respond(self, messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        request = messages[-1]
        assert isinstance(request, ModelRequest)
        retries = [part for part in request.parts if isinstance(part, RetryPromptPart)]
        self.retry_feedback.extend(str(part.content) for part in retries)
        (prompt,) = (
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        )
        value = json.loads(str(prompt))
        if "proposal" not in value:
            evidence = value["evidence"][0]["evidence_id"]
            output = {
                "candidates": [
                    {
                        "kind": "decision",
                        "text": "Billing uses PostgreSQL 16.",
                        "description": "Billing database is PostgreSQL 16",
                        "evidence_ids": [evidence],
                    },
                    {
                        "kind": "decision",
                        "text": "MySQL is no longer an option.",
                        "description": "MySQL is excluded",
                        "evidence_ids": [evidence],
                    },
                ]
            }
            return _json(output)
        related = [item["item_id"] for item in value["related"]]
        evidence = value["proposal"]["evidence_ids"]
        if not related:
            content = {
                "kind": "decision",
                "text": value["proposal"]["text"],
                "description": value["proposal"]["description"],
            }
            return _json({
                "action": "create",
                "compared_ids": [],
                "content": content,
                "evidence_ids": evidence,
                "reason": "Independent decision.",
            })
        self.comparisons += 1
        merged = "Billing uses PostgreSQL 16; MySQL is no longer an option."
        action = "merge" if retries and self.corrects else "revise"
        return _json({
            "action": action,
            "compared_ids": related,
            "target_ids": related[:1],
            "content": {"kind": "decision", "text": merged, "description": "PostgreSQL selected; MySQL excluded"},
            "evidence_ids": evidence,
            "reason": "Same decision.",
        })


def _json(value: object) -> ModelResponse:
    return ModelResponse(parts=[TextPart(json.dumps(value))])


async def _flush(provider: _Provider, monkeypatch, tmp_path):
    monkeypatch.setattr("pydantic_ai.models.infer_model", lambda _name, **_kwargs: FunctionModel(provider.respond))
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'retry.db'}"),
        inference=InferenceConfig(generation_model="provider:test-model"),
    )
    async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
        assert runtime.scopes is not None
        assert runtime.atomic_memory is not None
        scope = await runtime.scopes.create(ScopeDraft(title="Billing", summary="Decisions", idempotency_key="billing"))
        await runtime.sources.for_scope(scope.scope_id).capture(
            CaptureSource(source_id="decision", content="Billing uses PostgreSQL 16; MySQL is out.", metadata={})
        )
        memory = runtime.atomic_memory.for_scope(scope.scope_id)
        error = None
        try:
            await memory.flush()
        except InvalidInferenceOutputError as raised:
            error = raised
        cursor = await memory.cursor()
        entries = (await memory.list()).items
        return error, cursor.sequence, entries


def test_flush_publishes_the_decision_corrected_after_rule_feedback(monkeypatch, tmp_path):
    provider = _Provider(corrects=True)

    error, cursor, entries = asyncio.run(_flush(provider, monkeypatch, tmp_path))

    assert error is None
    assert cursor == 1
    assert [entry.artifact.content.text for entry in entries] == [
        "Billing uses PostgreSQL 16; MySQL is no longer an option."
    ]
    assert provider.comparisons == 2
    assert len(provider.retry_feedback) == 1
    assert REVISE_RULE in provider.retry_feedback[0]


def test_flush_reports_the_broken_rule_after_the_retry_budget_is_spent(monkeypatch, tmp_path):
    provider = _Provider(corrects=False)

    error, cursor, entries = asyncio.run(_flush(provider, monkeypatch, tmp_path))

    assert isinstance(error, InvalidInferenceOutputError)
    assert error.operation == "atomic-memory-reconcile"
    assert REVISE_RULE in error.detail
    assert (cursor, entries) == (0, ())
    # The default generation_max_requests of 2 allows one corrected attempt.
    assert provider.comparisons == 2


def test_rule_feedback_names_the_rule_without_source_content(monkeypatch, tmp_path):
    provider = _Provider(corrects=False)

    asyncio.run(_flush(provider, monkeypatch, tmp_path))

    assert provider.retry_feedback
    assert not any("PostgreSQL" in feedback or "MySQL" in feedback for feedback in provider.retry_feedback)


_PUBLISHED_WORKFLOW = "Project Larch uses planning, multiple workers for implementation and multiple reviews."
_TRANSLATED_WORKFLOW = "Project Larch 采用规划、多个 worker 实施、多个 review 的工作方式。"
_UPDATED_WORKFLOW = "Project Larch 采用规划、单个 worker 实施、多个 review 的工作方式。"


class _PublishedWorkflowProvider:
    """Repeat the published fact with a new description, or make an actual fact correction."""

    def __init__(self, *, corrects: bool = False, changes_fact: bool = False) -> None:
        self.corrects = corrects
        self.changes_fact = changes_fact
        self.retry_feedback: list[str] = []

    async def respond(self, messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        request = messages[-1]
        assert isinstance(request, ModelRequest)
        retries = [part for part in request.parts if isinstance(part, RetryPromptPart)]
        self.retry_feedback.extend(str(part.content) for part in retries)
        (prompt,) = (
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        )
        value = json.loads(str(prompt))
        if "proposal" not in value:
            return _json({
                "candidates": [
                    {
                        "kind": "preference",
                        "text": _UPDATED_WORKFLOW if self.changes_fact else _TRANSLATED_WORKFLOW,
                        "description": "Project Larch 开发流程",
                        "evidence_ids": [value["evidence"][0]["evidence_id"]],
                    }
                ]
            })

        # The real vector index must recall the published record before reconciliation.
        (target,) = value["related"]
        assert target["text"] == _PUBLISHED_WORKFLOW
        assert len(target["original_refs"]) == 1
        corrected = bool(retries) and self.corrects
        return _json({
            "action": "noop" if corrected else "revise",
            "compared_ids": [target["item_id"]],
            "target_ids": [target["item_id"]],
            "content": None
            if corrected
            else {
                "kind": "preference",
                "text": _UPDATED_WORKFLOW if self.changes_fact else _PUBLISHED_WORKFLOW,
                "description": "Project Larch development workflow",
            },
            "evidence_ids": []
            if corrected
            else list(dict.fromkeys([*target["evidence_ids"], *value["proposal"]["evidence_ids"]])),
            "reason": "Retain the existing workflow." if corrected else "Apply the Source evidence to the workflow.",
        })


async def _flush_published_workflow(provider: _PublishedWorkflowProvider, monkeypatch, tmp_path):
    monkeypatch.setattr("pydantic_ai.models.infer_model", lambda _name, **_kwargs: FunctionModel(provider.respond))
    monkeypatch.setattr(
        "pydantic_ai.embeddings.infer_embedding_model", lambda _name, **_kwargs: TestEmbeddingModel(dimensions=3)
    )
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'published-workflow.db'}"),
        inference=InferenceConfig(
            generation_model="provider:test-model",
            embedding_model="test",
            embedding_profile_id="workflow-fixture",
            embedding_dimension=3,
        ),
        runtime=RuntimeConfig(atomic_memory_related_mode="vector"),
    )
    async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
        assert runtime.scopes is not None and runtime.atomic_memory is not None
        scope = await runtime.scopes.create(
            ScopeDraft(title="Project Larch", summary="Development workflow", idempotency_key="larch")
        )
        memory = runtime.atomic_memory.for_scope(scope.scope_id)
        original = (
            await memory.create([
                AtomicMemoryContent(
                    kind="preference", text=_PUBLISHED_WORKFLOW, description="Published development preference"
                )
            ])
        ).primary
        receipt = await runtime.sources.for_scope(scope.scope_id).capture(
            CaptureSource(
                source_id="workflow-update",
                content=_UPDATED_WORKFLOW if provider.changes_fact else _TRANSLATED_WORKFLOW,
                metadata={},
            )
        )
        error = None
        try:
            await memory.flush()
        except InvalidInferenceOutputError as raised:
            error = raised
        current = await memory.get(original.ref.artifact_id)
        (listed,) = (await memory.list()).items
        assert listed == current
        historical = await memory.get(original.ref.artifact_id, revision=original.ref.revision)
        assert historical.artifact == original.artifact
        history = await runtime.records.for_scope(scope.scope_id).list_artifact_revisions(
            "atomic-memory", original.ref.artifact_id, limit=10, cursor=None
        )
        assert history.next_cursor is None
        return error, (await memory.cursor()).sequence, original, current, history, receipt


def test_translation_noop_after_retry_preserves_published_record_and_advances_cursor(monkeypatch, tmp_path):
    provider = _PublishedWorkflowProvider(corrects=True)

    error, cursor, original, current, history, receipt = asyncio.run(
        _flush_published_workflow(provider, monkeypatch, tmp_path)
    )

    assert error is None
    assert cursor == receipt.sequence
    assert current == original
    assert len(history.items) == 1
    assert provider.retry_feedback


def test_repeated_translation_revision_leaves_published_record_and_cursor_unchanged(monkeypatch, tmp_path):
    provider = _PublishedWorkflowProvider()

    error, cursor, original, current, history, _receipt = asyncio.run(
        _flush_published_workflow(provider, monkeypatch, tmp_path)
    )

    assert isinstance(error, InvalidInferenceOutputError)
    assert error.operation == "atomic-memory-reconcile"
    assert cursor == 0
    assert current == original
    assert len(history.items) == 1
    assert provider.retry_feedback


def test_changed_workflow_revises_published_fact_and_preserves_history(monkeypatch, tmp_path):
    provider = _PublishedWorkflowProvider(changes_fact=True)

    error, cursor, original, current, history, receipt = asyncio.run(
        _flush_published_workflow(provider, monkeypatch, tmp_path)
    )

    assert error is None
    assert cursor == receipt.sequence
    assert current.ref.artifact_id == original.ref.artifact_id
    assert current.ref.revision == original.ref.revision + 1
    assert current.artifact.content.text == _UPDATED_WORKFLOW
    assert current.artifact.lineage.sources == (receipt.source_ref,)
    assert current.artifact.lineage.artifacts == (original.ref,)
    assert len(history.items) == 2
    assert not provider.retry_feedback
