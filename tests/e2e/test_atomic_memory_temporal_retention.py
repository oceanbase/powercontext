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

"""Source flush retains effective dates through correction, publication and restart."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic_ai.embeddings import TestEmbeddingModel
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, RetryPromptPart, TextPart, UserPromptPart
from pydantic_ai.models.function import AgentInfo, FunctionModel

from powercontext.builtin.inference import InvalidInferenceOutputError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_runtime
from powercontext.builtin.runtime.config import InferenceConfig
from powercontext.builtin.scope import ScopeDraft

_DESCRIPTION = "BorealisLedger production PostgreSQL port policy"
_MAY = "Effective 2026-05-01, BorealisLedger's standing production PostgreSQL port is 6432."
_MARCH = (
    "BorealisLedger's standing production PostgreSQL port was 5432 effective 2026-03-01; "
    "effective 2026-05-01 the current port is 6432."
)
_APRIL = (
    "BorealisLedger's standing production PostgreSQL port was 5432 effective 2026-03-01, "
    "then 6000 effective 2026-04-01; effective 2026-05-01 the current port is 6432."
)
_JULY = "Effective 2026-07-01, BorealisLedger's standing production PostgreSQL port is 7432."


class _Provider:
    def __init__(self, *, omit_extracted_date: bool = False, corrects: bool = True) -> None:
        self.omit_extracted_date = omit_extracted_date
        self.corrects = corrects

    async def respond(self, messages: list[ModelMessage], _info: AgentInfo) -> ModelResponse:
        request = messages[-1]
        assert isinstance(request, ModelRequest)
        retry = any(isinstance(part, RetryPromptPart) for part in request.parts)
        (prompt,) = (
            part.content
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, UserPromptPart)
        )
        value = json.loads(str(prompt))
        if "proposal" not in value:
            (evidence,) = value["evidence"]
            source = json.loads(evidence["content"]["content"])
            text = f"Effective {source['effective_at'][:10]}, {source['statement']}"
            if self.omit_extracted_date and (not retry or not self.corrects):
                text = source["statement"]
            return _json({
                "candidates": [
                    {
                        "kind": "decision",
                        "text": text,
                        "description": _DESCRIPTION,
                        "evidence_ids": [evidence["evidence_id"]],
                    }
                ]
            })
        related = [item["item_id"] for item in value["related"]]
        text = value["proposal"]["text"]
        evidence_ids = value["proposal"]["evidence_ids"]
        if related and "2026-03-01" in text:
            # The first proposal discards the May date despite citing its Artifact.
            text = _MARCH if retry and self.corrects else text
            evidence_ids = [item["evidence_id"] for item in value["evidence"]]
        elif related and "2026-04-01" in text:
            text = _APRIL
            evidence_ids = [item["evidence_id"] for item in value["evidence"]]
        return _json({
            "action": "revise" if related else "create",
            "compared_ids": related,
            "target_ids": related[:1],
            "content": {"kind": "decision", "text": text, "description": _DESCRIPTION},
            "evidence_ids": evidence_ids,
            "reason": "Retain the effective policy using the cited evidence.",
        })


def _json(value: object) -> ModelResponse:
    return ModelResponse(parts=[TextPart(json.dumps(value))])


def _config(tmp_path: Path, *, related_mode="fts") -> BuiltinConfig:
    return BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'temporal.db'}"),
        runtime=RuntimeConfig(atomic_memory_related_mode=related_mode, memory_rerank_enabled=False),
        inference=InferenceConfig(
            generation_model="provider:temporal-test",
            embedding_model="test",
            embedding_profile_id="temporal-retention",
            embedding_dimension=3,
        ),
    )


def _source(month: str, port: int, recorded: str, *, year: int = 2026) -> dict[str, str]:
    return {
        "subject": _DESCRIPTION,
        "effective_at": f"{year}-{month}-01T00:00:00Z",
        "recorded_at": recorded,
        "statement": f"BorealisLedger's standing production PostgreSQL port is {port}.",
    }


def test_extraction_corrects_an_omitted_effective_date_before_publication(monkeypatch, tmp_path: Path) -> None:
    provider = _Provider(omit_extracted_date=True)
    monkeypatch.setattr("pydantic_ai.models.infer_model", lambda _name, **_kwargs: FunctionModel(provider.respond))
    monkeypatch.setattr(
        "pydantic_ai.embeddings.infer_embedding_model", lambda *_args, **_kwargs: TestEmbeddingModel(dimensions=3)
    )

    async def scenario() -> None:
        async with open_builtin_runtime(_config(tmp_path), scheduler_path=tmp_path / "scheduler.db") as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Port policy", summary="Effective database port", idempotency_key="policy")
            )
            await runtime.records.for_scope(scope.scope_id).create_source(
                "content", _source("05", 6432, "2026-05-02T09:00:00Z")
            )
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            assert (await memory.flush()).current_cursor == 1
            (published,) = (await memory.list()).items
            assert published.artifact.content.text == _MAY
            assert published.ref.revision == 1

    asyncio.run(scenario())


def test_unrepaired_effective_date_loss_leaves_the_source_unprocessed(monkeypatch, tmp_path: Path) -> None:
    provider = _Provider(omit_extracted_date=True, corrects=False)
    monkeypatch.setattr("pydantic_ai.models.infer_model", lambda _name, **_kwargs: FunctionModel(provider.respond))
    monkeypatch.setattr(
        "pydantic_ai.embeddings.infer_embedding_model", lambda *_args, **_kwargs: TestEmbeddingModel(dimensions=3)
    )

    async def scenario() -> None:
        async with open_builtin_runtime(_config(tmp_path), scheduler_path=tmp_path / "scheduler.db") as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Port policy", summary="Effective database port", idempotency_key="policy")
            )
            await runtime.records.for_scope(scope.scope_id).create_source(
                "content", _source("05", 6432, "2026-05-02T09:00:00Z")
            )
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            with pytest.raises(InvalidInferenceOutputError) as failure:
                await memory.flush()
            assert failure.value.operation == "atomic-memory-extract"
            assert (await memory.cursor()).sequence == 0
            assert (await memory.list()).items == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("related_mode", ("fts", "vector"))
def test_temporal_reconciliation_keeps_dates_across_restart_then_accepts_newer_correction(
    monkeypatch, tmp_path: Path, related_mode: str
) -> None:
    provider = _Provider()
    monkeypatch.setattr("pydantic_ai.models.infer_model", lambda _name, **_kwargs: FunctionModel(provider.respond))
    monkeypatch.setattr(
        "pydantic_ai.embeddings.infer_embedding_model", lambda *_args, **_kwargs: TestEmbeddingModel(dimensions=3)
    )
    config = _config(tmp_path, related_mode=related_mode)

    async def scenario() -> None:
        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Port policy", summary="Effective database port", idempotency_key="policy")
            )
            sources = runtime.records.for_scope(scope.scope_id)
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            originals = []
            for month, port, recorded, expected in (
                ("05", 6432, "2026-05-02T09:00:00Z", _MAY),
                ("03", 5432, "2026-09-01T09:00:00Z", _MARCH),
            ):
                await sources.create_source("content", _source(month, port, recorded))
                assert (await memory.flush()).current_cursor == len(originals) + 1
                (published,) = (await memory.list()).items
                assert published.artifact.content.text == expected
                originals.append(published)
            assert originals[1].ref.artifact_id == originals[0].ref.artifact_id
            assert originals[1].ref.revision == 2

        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            assert runtime.atomic_memory is not None
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            assert (await memory.list()).items == (originals[-1],)
            sources = runtime.records.for_scope(scope.scope_id)
            for month, port, recorded, expected in (
                ("04", 6000, "2026-10-01T09:00:00Z", _APRIL),
                ("07", 7432, "2026-07-02T09:00:00Z", _JULY),
            ):
                await sources.create_source("content", _source(month, port, recorded))
                assert (await memory.flush()).current_cursor == len(originals) + 1
                (published,) = (await memory.list()).items
                assert published.artifact.content.text == expected
                assert published.ref.artifact_id == originals[0].ref.artifact_id
                assert published.ref.revision == len(originals) + 1
                originals.append(published)
                for mode in ("text", "vector"):
                    found = await memory.search("PostgreSQL", mode=mode)
                    assert [(hit.hit.artifact_ref, hit.text) for hit in found.hits] == [(published.ref, expected)]
            for original in originals:
                historical = await memory.get(original.ref.artifact_id, revision=original.ref.revision)
                assert historical.artifact == original.artifact
            assert (await memory.cursor()).sequence == 4

    asyncio.run(scenario())


def test_unrepaired_reconciliation_date_loss_preserves_published_revision_and_cursor(
    monkeypatch, tmp_path: Path
) -> None:
    provider = _Provider(corrects=False)
    monkeypatch.setattr("pydantic_ai.models.infer_model", lambda _name, **_kwargs: FunctionModel(provider.respond))
    monkeypatch.setattr(
        "pydantic_ai.embeddings.infer_embedding_model", lambda *_args, **_kwargs: TestEmbeddingModel(dimensions=3)
    )

    async def scenario() -> None:
        async with open_builtin_runtime(_config(tmp_path), scheduler_path=tmp_path / "scheduler.db") as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Port policy", summary="Effective database port", idempotency_key="policy")
            )
            sources = runtime.records.for_scope(scope.scope_id)
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            await sources.create_source("content", _source("05", 6432, "2026-05-02T09:00:00Z"))
            assert (await memory.flush()).current_cursor == 1
            (original,) = (await memory.list()).items
            assert original.artifact.content.text == _MAY
            await sources.create_source("content", _source("03", 5432, "2026-09-01T09:00:00Z"))

            with pytest.raises(InvalidInferenceOutputError) as failure:
                await memory.flush()

            assert failure.value.operation == "atomic-memory-reconcile"
            assert (await memory.cursor()).sequence == 1
            assert (await memory.list()).items == (original,)
            historical = await memory.get(original.ref.artifact_id, revision=original.ref.revision)
            assert historical.artifact == original.artifact

    asyncio.run(scenario())


def test_newer_corrections_keep_current_content_compact_and_exact_history_readable(monkeypatch, tmp_path: Path) -> None:
    provider = _Provider()
    monkeypatch.setattr("pydantic_ai.models.infer_model", lambda _name, **_kwargs: FunctionModel(provider.respond))
    monkeypatch.setattr(
        "pydantic_ai.embeddings.infer_embedding_model", lambda *_args, **_kwargs: TestEmbeddingModel(dimensions=3)
    )
    config = _config(tmp_path)
    config = config.model_copy(
        update={"runtime": config.runtime.model_copy(update={"atomic_memory_input_tokens_limit": 4096})}
    )

    async def scenario() -> None:
        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Port policy", summary="Effective database port", idempotency_key="policy")
            )
            sources = runtime.records.for_scope(scope.scope_id)
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            originals = []
            for month in range(1, 13):
                date = f"2027-{month:02}-01"
                source = _source(f"{month:02}", 6432 + month, f"2027-{month:02}-02T09:00:00Z", year=2027)
                await sources.create_source("content", source)
                assert (await memory.flush()).current_cursor == month
                (published,) = (await memory.list()).items
                assert published.artifact.content.text == f"Effective {date}, {source['statement']}"
                assert published.ref.revision == month
                if originals:
                    assert published.ref.artifact_id == originals[0].ref.artifact_id
                originals.append(published)
            assert (await memory.cursor()).sequence == 12

        async with open_builtin_runtime(config, scheduler_path=tmp_path / "scheduler.db") as runtime:
            assert runtime.atomic_memory is not None
            memory = runtime.atomic_memory.for_scope(scope.scope_id)
            assert (await memory.list()).items == (originals[-1],)
            for original in originals:
                historical = await memory.get(original.ref.artifact_id, revision=original.ref.revision)
                assert historical.artifact == original.artifact
            for mode in ("text", "vector"):
                found = await memory.search("PostgreSQL", mode=mode)
                assert [(hit.hit.artifact_ref, hit.text) for hit in found.hits] == [
                    (originals[-1].ref, originals[-1].artifact.content.text)
                ]

    asyncio.run(scenario())
