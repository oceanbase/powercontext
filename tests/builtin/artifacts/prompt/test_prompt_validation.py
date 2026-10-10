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
    Prompt,
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


def _case(key: str) -> dict[str, Any]:  # noqa: C901 - one branch per prompt key fixture
    if key in {"atomic_memory.extract", "atomic_memory.reconcile"}:
        evidence = {
            "evidence_id": "source:content/turn-1",
            "source_ref": {"source_type": "content", "source_id": "turn-1"},
            "journal_position": 1,
            "content": "Prefers Chinese.",
            "source_metadata": {},
        }
        candidate = {"kind": "preference", "text": "Prefers Chinese.", "evidence_ids": [evidence["evidence_id"]]}
        if key == "atomic_memory.extract":
            return {"input": {"evidence": [evidence]}, "expected_output": {"candidates": [candidate]}}
        return {
            "input": {
                "proposal": {"item_id": "candidate:1", "original_refs": [], **candidate},
                "related": [],
                "evidence": [evidence],
            },
            "expected_output": {
                "action": "create",
                "compared_ids": [],
                "content": {"kind": "preference", "text": "Prefers Chinese."},
                "evidence_ids": [evidence["evidence_id"]],
                "reason": "The supplied Source supports this independent preference.",
            },
        }
    if key == "profile.generate":
        return {
            "input": {
                "previous_content": "# Profile\n\n- Uses Python.",
                "sources": ['{"speaker":"user","text":"Prefers Chinese."}'],
            },
            "expected_output": {"content": "# Profile\n\n- Uses Python.\n- Prefers Chinese."},
        }
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
    if key == "topic_memory.probe":
        return {
            "input": {"evidence": [{"evidence_id": "e1", "source_type": "source", "content": "Ports are checked."}]},
            "expected_output": {"probes": [{"query": "port checks", "evidence_ids": ["e1"]}]},
        }
    if key == "topic_memory.global":
        return {
            "input": {
                "evidence": [{"evidence_id": "e1", "source_type": "source", "content": "Ports are checked."}],
                "probes": [],
            },
            "expected_output": {"proposals": []},
        }
    if key == "topic_memory.planner":
        return {
            "input": {
                "probes": [{"probe_id": "p1", "query": "port checks", "evidence_ids": ["e1"]}],
            },
            "expected_output": {"items": [{"probe_ids": ["p1"]}]},
        }
    if key == "topic_memory.evolve":
        return {
            "input": {
                "work_id": "w1",
                "evidence": [{"evidence_id": "e1", "source_type": "source", "content": "Ports are checked."}],
            },
            "expected_output": {"proposal": None},
        }
    if key == "topic_memory.temporary":
        proposal = {
            "content": {"title": "Port checks", "summary": "Ports are checked.", "detail": "Ports are checked."},
            "evidence_ids": ["e1"],
        }
        return {
            "input": {
                "work_id": "w1",
                "evidence": [{"evidence_id": "e1", "source_type": "source", "content": "Ports are checked."}],
            },
            "expected_output": {"proposals": [proposal]},
        }
    if key == "topic_memory.reduce":
        return {
            "input": {
                "probes": [{"query": "port checks", "evidence_ids": ["e1"]}],
                "max_result_tokens": 128,
            },
            "expected_output": {"covered_indices": [0], "probe": {"query": "port checks", "evidence_ids": ["e1"]}},
        }
    if key == "topic_memory.reconcile":
        proposal = {
            "content": {"title": "Port checks", "summary": "Ports are checked.", "detail": "Ports are checked."},
            "evidence_ids": ["e1"],
        }
        return {
            "input": {"component_id": "c1", "proposals": [proposal]},
            "expected_output": {"proposals": []},
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


def _atomic_artifact_case() -> dict[str, Any]:
    case = _case("atomic_memory.reconcile")
    ref = {"family": "atomic-memory", "artifact_id": "memory-language", "revision": 2}
    evidence = {
        "evidence_id": "artifact:memory-language@2",
        "artifact_ref": dict(ref),
        "content": {"kind": "preference", "text": "Prefers English."},
    }
    case["input"]["evidence"].append(evidence)
    case["input"]["related"] = [
        {
            "item_id": "memory:language",
            **evidence["content"],
            "original_refs": [{"ref": dict(ref), "state": "active", "state_version": 0}],
            "evidence_ids": [evidence["evidence_id"]],
        }
    ]
    case["expected_output"].update({
        "action": "revise",
        "compared_ids": ["memory:language"],
        "target_ids": ["memory:language"],
        "evidence_ids": [item["evidence_id"] for item in case["input"]["evidence"]],
    })
    return case


def test_atomic_reconciliation_demonstration_accepts_current_published_artifact_evidence() -> None:
    content = _content(_atomic_artifact_case())
    PromptRegistry(builtin_prompt_definitions()).get("atomic_memory.reconcile").validate(content)


@pytest.mark.parametrize("schema_field", ["schema", "schema_"])
def test_atomic_reconciliation_preserves_legacy_demonstration_metadata(schema_field: str) -> None:
    case = _atomic_artifact_case()
    case["expected_output"]["content"].update({schema_field: "powercontext.atomic-memory.v1", "creation": None})
    content = _content(case)
    original = content.model_dump_json()
    definition = PromptRegistry(builtin_prompt_definitions()).get("atomic_memory.reconcile")
    definition.validate(content)
    resolved = definition.resolve("scope-a", Prompt(artifact_id="atomic_memory.reconcile", revision=1, content=content))
    assert content.model_dump_json() == original
    assert resolved.demonstrations == content.demonstrations
    assert resolved.demonstrations[0].expected_output == case["expected_output"]


@pytest.mark.parametrize(
    "metadata",
    [
        {"creation": {"type": "merge", "input_artifact_ids": ["memory-a", "memory-b"]}},
        {"schema": "unsupported"},
        {"schema_": "unsupported"},
        {"revision": 1},
    ],
)
def test_atomic_reconciliation_demonstrations_reject_invalid_metadata(metadata: dict[str, Any]) -> None:
    case = _atomic_artifact_case()
    case["expected_output"]["content"].update(metadata)
    with pytest.raises(PromptError) as caught:
        PromptRegistry(builtin_prompt_definitions()).get("atomic_memory.reconcile").validate(_content(case))
    assert caught.value.code == "prompt_definition_incompatible"


def test_atomic_reconciliation_demonstration_allows_artifact_support_without_consuming_identity() -> None:
    case = _atomic_artifact_case()
    case["input"]["related"] = []
    case["input"]["proposal"]["evidence_ids"] = case["expected_output"]["evidence_ids"]
    case["expected_output"].update({"action": "create", "compared_ids": [], "target_ids": []})
    PromptRegistry(builtin_prompt_definitions()).get("atomic_memory.reconcile").validate(_content(case))


def test_atomic_reconciliation_demonstration_rejects_unavailable_artifact_evidence() -> None:
    case = _atomic_artifact_case()
    case["expected_output"]["evidence_ids"] = ["source:content/turn-1", "artifact:unavailable-memory@2"]
    with pytest.raises(PromptError) as caught:
        PromptRegistry(builtin_prompt_definitions()).get("atomic_memory.reconcile").validate(_content(case))
    assert caught.value.code == "prompt_definition_incompatible"


def test_atomic_extraction_demonstration_rejects_published_artifact_evidence() -> None:
    case = _case("atomic_memory.extract")
    case["input"]["evidence"].append(_atomic_artifact_case()["input"]["evidence"][-1])
    with pytest.raises(PromptError) as caught:
        PromptRegistry(builtin_prompt_definitions()).get("atomic_memory.extract").validate(_content(case))
    assert caught.value.code == "prompt_definition_incompatible"


@pytest.mark.parametrize(
    ("key", "path", "invalid"),
    [
        ("atomic_memory.extract", ("expected_output", "candidates", 0, "evidence_ids"), ["source:99"]),
        ("atomic_memory.reconcile", ("expected_output", "evidence_ids"), ["source:99"]),
        ("atomic_memory.reconcile", ("expected_output", "compared_ids"), ["unknown-item"]),
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
        ("profile.generate", ("expected_output", "content"), "   "),
        ("topic_memory.probe", ("expected_output", "probes", 0, "evidence_ids"), ["e99"]),
        (
            "topic_memory.global",
            ("expected_output", "proposals"),
            [{"content": {"title": "t", "summary": "s", "detail": "d"}, "evidence_ids": ["e99"]}],
        ),
        ("topic_memory.planner", ("expected_output", "items", 0, "probe_ids"), ["p99"]),
        (
            "topic_memory.evolve",
            ("expected_output", "proposal"),
            {
                "content": {"title": "t", "summary": "s", "detail": "d"},
                "evidence_ids": ["e1"],
                "candidate_id": "cand-99",
            },
        ),
        ("topic_memory.temporary", ("expected_output", "proposals", 0, "candidate_id"), "cand-1"),
        ("topic_memory.reduce", ("expected_output", "covered_indices"), [99]),
        ("topic_memory.reduce", ("expected_output", "covered_indices"), [1]),
        ("topic_memory.reduce", ("expected_output", "probe"), {"query": "port checks", "evidence_ids": ["e1", "e99"]}),
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


def test_profile_noop_demonstration_uses_null_content() -> None:
    case = _case("profile.generate")
    case["expected_output"] = {"content": None}
    definition = PromptRegistry(builtin_prompt_definitions()).get("profile.generate")
    definition.validate(_content(case))


def test_planner_demonstrations_group_shared_candidates() -> None:
    definition = PromptRegistry(builtin_prompt_definitions()).get("topic_memory.planner")

    case = deepcopy(_case("topic_memory.planner"))
    case["input"]["probes"] = [
        {"probe_id": "p1", "query": "port checks", "evidence_ids": ["e1"], "candidate_ids": ["c1"]},
        {"probe_id": "p2", "query": "port conflicts", "evidence_ids": ["e1"], "candidate_ids": ["c1"]},
    ]
    case["expected_output"]["items"] = [{"probe_ids": ["p1"]}, {"probe_ids": ["p2"]}]
    with pytest.raises(PromptError) as caught:
        definition.validate(_content(case))
    assert caught.value.code == "prompt_definition_incompatible"

    case["expected_output"]["items"] = [{"probe_ids": ["p1", "p2"]}]
    definition.validate(_content(case))


def test_reconcile_demonstrations_preserve_distinct_historical_identities() -> None:
    proposal = {
        "proposal_id": "r1",
        "candidate_id": "cand-1",
        "content": {"title": "Port checks", "summary": "Ports are checked.", "detail": "Ports are checked."},
        "evidence_ids": ["e1"],
    }
    definition = PromptRegistry(builtin_prompt_definitions()).get("topic_memory.reconcile")

    case = deepcopy(_case("topic_memory.reconcile"))
    case["input"]["proposals"] = [dict(proposal)]
    case["expected_output"]["proposals"] = [dict(proposal)]
    definition.validate(_content(case))

    case["expected_output"]["proposals"] = [dict(proposal, candidate_id="cand-2")]
    with pytest.raises(PromptError) as caught:
        definition.validate(_content(case))
    assert caught.value.code == "prompt_definition_incompatible"

    case["expected_output"]["proposals"] = [dict(proposal, proposal_id="r99")]
    with pytest.raises(PromptError) as caught:
        definition.validate(_content(case))
    assert caught.value.code == "prompt_definition_incompatible"


def test_reconcile_demonstrations_may_bind_unbound_proposals_to_supplied_history() -> None:
    definition = PromptRegistry(builtin_prompt_definitions()).get("topic_memory.reconcile")
    unbound = {
        "proposal_id": "r1",
        "content": {"title": "Port checks", "summary": "Ports are checked.", "detail": "Ports are checked."},
        "evidence_ids": ["e1"],
    }
    case = deepcopy(_case("topic_memory.reconcile"))
    case["input"] = {
        "component_id": "c1",
        "proposals": [dict(unbound)],
        "historical": [{"candidate_id": "cand-1", "title": "Ports", "summary": "Ports.", "detail": "Ports."}],
    }
    case["expected_output"]["proposals"] = [dict(unbound, candidate_id="cand-1")]
    definition.validate(_content(case))

    case["expected_output"]["proposals"] = [dict(unbound, candidate_id="cand-99")]
    with pytest.raises(PromptError) as caught:
        definition.validate(_content(case))
    assert caught.value.code == "prompt_definition_incompatible"


@pytest.mark.parametrize(
    ("key", "contract"),
    [
        ("topic_memory.probe", "Each probe cites one or more supplied evidence_id values."),
        ("topic_memory.planner", "Partition every supplied probe exactly once"),
        ("topic_memory.reduce", "exact union of the supplied evidence_ids"),
        ("topic_memory.reconcile", "never merge two distinct historical identities"),
    ],
)
def test_custom_prompts_retain_stage_operation_contracts(key: str, contract: str) -> None:
    definition = PromptRegistry(builtin_prompt_definitions()).get(key)
    prompt = Prompt(artifact_id=key, revision=1, content=_content(_case(key)))
    resolved = definition.resolve("scope-a", prompt)
    assert resolved.selection == "artifact"
    assert contract in resolved.compiled_instructions
    assert contract in definition.invariant_instructions


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


def test_generated_profile_demonstrations_retry_blank_markdown() -> None:
    observed = []

    async def respond(messages, info) -> ModelResponse:
        demonstration = _case("profile.generate")
        if not observed:
            demonstration["expected_output"]["content"] = "   "
        observed.append(demonstration)
        return ModelResponse(parts=[TextPart(json.dumps({"demonstrations": [demonstration]}))])

    async def scenario() -> None:
        generator = PromptDemonstrationGenerator(
            FunctionModel(respond), limits=InferenceLimits(max_requests=2), model_settings=None
        )
        definition = PromptRegistry(builtin_prompt_definitions()).get("profile.generate")
        result = await generator(
            definition,
            GeneratePromptDemonstrations(instructions="Keep verified lasting facts.", demonstration_count=1),
        )
        assert result.demonstrations[0].expected_output == {
            "content": "# Profile\n\n- Uses Python.\n- Prefers Chinese."
        }
        assert len(observed) == 2

    asyncio.run(scenario())
