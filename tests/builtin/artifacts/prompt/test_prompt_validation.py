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


from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any

import pytest
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from powercontext.builtin.artifacts.prompt import (
    PROMPT_KEYS,
    GeneratePromptDemonstrations,
    PromptContent,
    PromptError,
    PromptRegistry,
)
from powercontext.builtin.artifacts.prompt.builtin import builtin_prompt_definitions
from powercontext.builtin.inference.prompt_demonstrations import PromptDemonstrationGenerator
from powercontext.builtin.inference.pydantic_ai import InferenceLimits

_EXPERIENCE = {
    "situation": "Port occupied.",
    "action": "Checked its owner.",
    "outcome": "Tests passed.",
    "lesson": "Check ports first.",
}


def _case(key: str) -> dict[str, Any]:
    if key == "memory.extract":
        return {
            "input": {
                "evidence": [{"evidence_id": "source:1", "evidence_type": "source", "content": "Prefers Chinese."}],
                "current_entries": [{"entry_id": "entry:1", "kind": "preference", "text": "Prefers English."}],
            },
            "expected_output": {
                "candidates": [
                    {"intent": "add", "kind": "preference", "text": "Prefers Chinese.", "evidence_ids": ["source:1"]}
                ]
            },
        }
    if key == "memory.rerank":
        return {
            "input": {
                "query": "ports",
                "max_results": 1,
                "candidates": [{"rank": 1, "text": "Check ports."}, {"rank": 2, "text": "Run tests."}],
            },
            "expected_output": {"selected_ranks": [1]},
        }
    if key == "experience.incubate":
        return {
            "input": {"evidence": [{"evidence_id": "source:1", "content": "Verified port preflight."}]},
            "expected_output": {"candidates": [{"proposal": _EXPERIENCE, "evidence_ids": ["source:1"]}]},
        }
    if key in {"experience.generate", "skill.generate"}:
        proposal = (
            _EXPERIENCE
            if key == "experience.generate"
            else {
                "name": "port-preflight",
                "description": "Check ports before a release.",
                "instructions": "Check port availability, then run the smoke tests.",
                "validation": ["The tests pass."],
            }
        )
        return {
            "input": {"evidence": [{"evidence_id": "source:1", "kind": "source", "content": "Verified preflight."}]},
            "expected_output": {"proposal": proposal},
        }
    return {
        "input": {
            "objective": "Continue the port investigation.",
            "max_bytes": 8192,
            "evidence": [{"evidence_id": "source:1", "evidence_type": "source", "content": "Tests passed."}],
        },
        "expected_output": {
            "state": [{"text": "The smoke tests passed.", "evidence_ids": ["source:1"]}],
            "disposition": "complete",
        },
    }


def _content(value: dict[str, Any]) -> PromptContent:
    return PromptContent.model_validate_json(
        json.dumps({
            "schema_version": "powercontext.prompt.v1",
            "mode": "custom",
            "instructions": "Follow verified evidence.",
            "demonstrations": [value],
        })
    )


@pytest.mark.parametrize("key", PROMPT_KEYS)
def test_valid_demonstrations_preserve_their_original_json(key: str) -> None:
    content = _content(_case(key))
    original = content.model_dump_json()
    PromptRegistry(builtin_prompt_definitions(), supported=frozenset(PROMPT_KEYS)).validate(key, content)
    assert content.model_dump_json() == original


@pytest.mark.parametrize(
    ("key", "path", "invalid"),
    [
        ("memory.extract", ("expected_output", "candidates", 0, "evidence_ids"), ["source:99"]),
        ("memory.extract", ("expected_output", "candidates", 0, "evidence_ids"), []),
        ("memory.extract", ("expected_output", "candidates", 0, "entry_id"), "entry:1"),
        ("memory.rerank", ("expected_output", "selected_ranks"), [99]),
        ("memory.rerank", ("expected_output", "selected_ranks"), [1, 1]),
        ("memory.rerank", ("expected_output", "selected_ranks"), [1, 2]),
        ("memory.rerank", ("expected_output", "selected_ranks"), []),
        ("experience.incubate", ("expected_output", "candidates", 0, "evidence_ids"), ["source:99"]),
        ("experience.generate", ("input", "target_evidence_id"), "artifact:99"),
        ("experience.generate", ("input", "target_evidence_id"), "source:1"),
        ("skill.generate", ("expected_output", "proposal", "name"), "Invalid Package Name"),
        ("skill.generate", ("expected_output", "proposal", "license"), ""),
        ("handoff.generate", ("expected_output", "state"), []),
        ("handoff.generate", ("expected_output", "state", 0, "evidence_ids"), ["source:99"]),
        ("handoff.generate", ("expected_output", "omissions"), [{"text": "Unknown.", "evidence_id": "source:99"}]),
    ],
)
def test_demonstrations_reject_semantically_impossible_outputs(
    key: str, path: tuple[str | int, ...], invalid: object
) -> None:
    case = deepcopy(_case(key))
    target: Any = case
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = invalid
    content = _content(case)
    definition = PromptRegistry(builtin_prompt_definitions()).get(key)
    with pytest.raises(PromptError) as caught:
        definition.validate(content)
    assert caught.value.code == "prompt_definition_incompatible"
    assert not caught.value.during_inference


def test_generated_demonstrations_retry_invalid_references_within_request_budget() -> None:
    observed = []

    async def respond(messages, info) -> ModelResponse:
        demonstration = _case("memory.rerank")
        if not observed:
            demonstration["expected_output"]["selected_ranks"] = [99]
        observed.append(demonstration)
        return ModelResponse(parts=[TextPart(json.dumps({"demonstrations": [demonstration]}))])

    async def scenario() -> None:
        generator = PromptDemonstrationGenerator(
            FunctionModel(respond), limits=InferenceLimits(max_requests=2), model_settings=None
        )
        definition = PromptRegistry(builtin_prompt_definitions()).get("memory.rerank")
        result = await generator(
            definition,
            GeneratePromptDemonstrations(instructions="Select relevant supplied memories.", demonstration_count=1),
        )
        assert len(result.demonstrations) == 1
        assert result.demonstrations[0].expected_output == {"selected_ranks": [1]}
        assert len(observed) == 2

    asyncio.run(scenario())
