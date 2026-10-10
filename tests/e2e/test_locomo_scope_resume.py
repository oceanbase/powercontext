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

"""LoCoMo run namespaces survive runtime restarts through registered Scopes."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from pydantic_ai.embeddings import TestEmbeddingModel
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models import Model
from pydantic_ai.models.function import FunctionModel

from evaluation.memory.locomo import runner
from evaluation.memory.locomo.dataset import load_locomo
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, InferenceConfig, open_builtin_runtime
from powercontext.builtin.scope import ScopeNotFoundError
from powercontext.server.settings import ServerSettings


def test_locomo_ingestion_registers_and_resumes_its_scope_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dataset = load_locomo(Path(__file__).parents[2] / "evaluation" / "memory" / "locomo" / "dataset" / "locomo10.json")
    conversation = replace(
        dataset.conversations[0],
        sessions=dataset.conversations[0].sessions[:1],
        questions=dataset.conversations[0].questions[:1],
    )
    dataset = replace(dataset, conversations=(conversation,))
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'state.sqlite3'}"),
        inference=InferenceConfig(
            generation_model="openai:locomo-test",
            embedding_model="test",
            embedding_profile_id="locomo-test",
            embedding_dimension=8,
        ),
    )

    async def respond(messages, info):
        prompt = next(part.content for part in messages[-1].parts if part.part_kind == "user-prompt")
        value = json.loads(prompt)
        if "evidence" in value and "proposal" not in value:
            output = {
                "candidates": [
                    {
                        "kind": "fact",
                        "text": "Caroline went to the LGBTQ support group on 7 May 2023.",
                        "evidence_ids": [value["evidence"][0]["evidence_id"]],
                    }
                ]
            }
        elif "proposal" in value:
            proposal = value["proposal"]
            output = {
                "action": "create",
                "compared_ids": [item["item_id"] for item in value["related"]],
                "content": {"kind": proposal["kind"], "text": proposal["text"]},
                "evidence_ids": proposal["evidence_ids"],
                "reason": "Preserve the supplied session evidence.",
            }
        else:
            output = {"label": "CORRECT"} if "gold_answer" in value else {"answer": "7 May 2023"}
        return ModelResponse(parts=[TextPart(json.dumps(output))])

    def infer_model(model, **kwargs):
        return model if isinstance(model, Model) else FunctionModel(respond)

    monkeypatch.setattr("pydantic_ai.models.infer_model", infer_model)
    monkeypatch.setattr(runner, "infer_model", infer_model)

    monkeypatch.setattr(
        "pydantic_ai.embeddings.infer_embedding_model",
        lambda model, **kwargs: TestEmbeddingModel() if isinstance(model, str) else model,
    )

    async def ingest(run_id: str):
        return await runner.ingest_dataset(
            dataset,
            settings=settings,
            run_id=run_id,
            output_directory=tmp_path / run_id,
            concurrency=1,
        )

    first = asyncio.run(ingest("scope-resume"))
    first_conversation = first["conversations"][conversation.sample_id]
    registered_scope = first_conversation["scope_id"]
    assert first["newly_processed_session_count"] == 1
    assert first["atomic_memory_count"] == 1
    assert first_conversation["namespace"] == runner.scope_id("scope-resume", conversation.sample_id)

    async def read_persisted_memory():
        async with open_builtin_runtime(
            BuiltinConfig(database=settings.database, inference=settings.inference)
        ) as runtime:
            assert runtime.scopes is not None
            descriptor = await runtime.scopes.get(registered_scope)
            assert runtime.atomic_memory is not None
            page = await runtime.atomic_memory.for_scope(descriptor.scope_id).list()
            assert len(page.items) == 1
            assert page.items[0].artifact.lineage.sources[0].source_id == "D1"

    asyncio.run(read_persisted_memory())
    resumed = asyncio.run(ingest("scope-resume"))
    assert resumed["conversations"][conversation.sample_id]["scope_id"] == registered_scope
    assert resumed["resumed_session_count"] == 1
    assert resumed["newly_processed_session_count"] == 0
    assert resumed["atomic_memory_count"] == 1

    missing_database_settings = settings.model_copy(
        update={"database": SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'missing-state.sqlite3'}")}
    )
    ingestion_path = tmp_path / "scope-resume" / "ingestion.json"
    frozen_ingestion = ingestion_path.read_bytes()
    with pytest.raises(ScopeNotFoundError):
        asyncio.run(
            runner.evaluate_dataset(
                dataset,
                settings=missing_database_settings,
                run_id="scope-resume",
                output_directory=tmp_path / "scope-resume",
                top_k=1,
            )
        )
    with pytest.raises(ScopeNotFoundError):
        asyncio.run(
            runner.ingest_dataset(
                dataset,
                settings=missing_database_settings,
                run_id="scope-resume",
                output_directory=tmp_path / "scope-resume",
            )
        )
    assert ingestion_path.read_bytes() == frozen_ingestion
    assert not (tmp_path / "scope-resume" / "observations.jsonl").exists()

    summary = asyncio.run(
        runner.evaluate_dataset(
            dataset,
            settings=settings,
            run_id="scope-resume",
            output_directory=tmp_path / "scope-resume",
            top_k=1,
            concurrency=1,
        )
    )
    assert summary["metrics"]["overall"]["error_count"] == 0
    assert summary["metrics"]["overall"]["evidence_hit"] == 1
    assert summary["metrics"]["overall"]["llm_judge"] == 1

    separate = asyncio.run(ingest("another-run"))
    assert separate["conversations"][conversation.sample_id]["scope_id"] != registered_scope
    assert separate["newly_processed_session_count"] == 1


@pytest.mark.parametrize("legacy_ingestion", [False, True])
def test_locomo_requires_registered_ingestion_before_evaluation(tmp_path: Path, legacy_ingestion: bool) -> None:
    dataset = load_locomo(Path(__file__).parents[2] / "evaluation" / "memory" / "locomo" / "dataset" / "locomo10.json")
    database_path = tmp_path / "unopened.sqlite3"
    settings = ServerSettings(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"),
        inference=InferenceConfig(generation_model="openai:unused-test-model"),
    )
    parameters: dict[str, Any] = {
        "dataset": dataset,
        "settings": settings,
        "run_id": "missing-ingestion",
        "output_directory": tmp_path,
    }
    message = "run ingestion first"
    if legacy_ingestion:
        message = "legacy logical scopes require a new run"
        ingestion_path = tmp_path / "ingestion.json"
        ingestion_path.write_text(
            json.dumps({
                "run_id": "missing-ingestion",
                "conversations": {
                    conversation.sample_id: {"scope_id": runner.scope_id("missing-ingestion", conversation.sample_id)}
                    for conversation in dataset.conversations
                },
            })
        )
        frozen_ingestion = ingestion_path.read_bytes()
        with pytest.raises(ValueError, match=message):
            asyncio.run(runner.ingest_dataset(**parameters))
        assert ingestion_path.read_bytes() == frozen_ingestion
    with pytest.raises(ValueError, match=message):
        asyncio.run(runner.evaluate_dataset(**parameters, top_k=1))
    assert not database_path.exists()
    assert not (tmp_path / "observations.jsonl").exists()
