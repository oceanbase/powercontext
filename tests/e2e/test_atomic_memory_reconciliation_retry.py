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

from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, RetryPromptPart, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from powercontext.builtin.inference import InvalidInferenceOutputError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, open_builtin_runtime
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
    async with open_builtin_runtime(config) as runtime:
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
