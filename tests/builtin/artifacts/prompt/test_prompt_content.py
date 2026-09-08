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

import json
from collections.abc import Mapping
from dataclasses import replace

import pytest
from pydantic import BaseModel, ValidationError

from powercontext.builtin.artifacts.memory import MemoryExtractionProfile, memory_extraction_instructions
from powercontext.builtin.artifacts.prompt import PROMPT_KEYS, Prompt, PromptContent, PromptError, PromptRegistry
from powercontext.builtin.artifacts.prompt.builtin import builtin_prompt_definitions


def _content(*, mode: str = "custom", instructions: str = "Keep testing preferences.") -> PromptContent:
    return PromptContent.model_validate_json(
        json.dumps({
            "schema_version": "powercontext.prompt.v1",
            "mode": mode,
            "instructions": instructions,
            "demonstrations": [],
        })
    )


def _with_demonstration(value: Mapping[str, object]) -> PromptContent:
    return PromptContent.model_validate_json(
        json.dumps({
            "schema_version": "powercontext.prompt.v1",
            "mode": "custom",
            "instructions": "Keep testing preferences.",
            "demonstrations": [value],
        })
    )


@pytest.mark.parametrize("profile", list(MemoryExtractionProfile))
def test_auto_preserves_the_deployed_extraction_profile(profile: MemoryExtractionProfile) -> None:
    registry = PromptRegistry(builtin_prompt_definitions(profile))
    definition = registry.get("memory.extract")
    missing = definition.resolve("scope-a", None)
    explicit = definition.resolve(
        "scope-a",
        Prompt(artifact_id="memory.extract", revision=2, content=_content(mode="auto", instructions="")),
    )
    assert missing.compiled_instructions == memory_extraction_instructions(profile)
    assert explicit.compiled_instructions == missing.compiled_instructions
    assert explicit.compiled_digest == missing.compiled_digest
    assert explicit.artifact is None
    assert registry.capabilities["memory.extract"].builtin_profile == profile.value


def test_custom_selection_freezes_exact_revision_and_keeps_scope_identity() -> None:
    definition = PromptRegistry(builtin_prompt_definitions()).get("memory.extract")
    original = Prompt(artifact_id="memory.extract", revision=2, content=_content())
    frozen = definition.resolve("scope-a", original)
    changed = definition.resolve(
        "scope-a",
        Prompt(
            artifact_id="memory.extract", revision=3, content=_content(instructions="Keep only release constraints.")
        ),
    )
    assert frozen.artifact == original.as_ref()
    assert frozen.scope_id == "scope-a"
    assert "Keep testing preferences." in frozen.compiled_instructions
    assert "supplied evidence IDs" in frozen.compiled_instructions
    assert frozen.compiled_digest != changed.compiled_digest
    assert definition.resolve("scope-b", original).scope_id == "scope-b"


def test_demonstrations_validate_without_inserting_defaults() -> None:
    registry = PromptRegistry(builtin_prompt_definitions(), supported=frozenset({"memory.extract"}))
    content = _with_demonstration({
        "input": {
            "evidence": [
                {"evidence_id": "s1", "evidence_type": "source", "content": "I run smoke tests before release."}
            ],
            "current_entries": [],
        },
        "expected_output": {
            "candidates": [
                {"intent": "add", "kind": "preference", "text": "Runs smoke tests.", "evidence_ids": ["s1"]}
            ],
        },
    })
    before = content.model_dump_json()
    registry.validate("memory.extract", content)
    assert content.model_dump_json() == before
    assert "entry_id" not in before


def test_schema_incompatibility_is_separate_from_immutable_content_decoding() -> None:
    content = _with_demonstration({
        "input": {"evidence": [], "current_entries": []},
        "expected_output": {"candidates": []},
    })

    class ChangedInput(BaseModel):
        required_new_field: str

    original = builtin_prompt_definitions()[0]
    incompatible = replace(original, input_type=ChangedInput, definition_version="incompatible-test")
    restored = PromptContent.model_validate_json(content.model_dump_json())
    with pytest.raises(PromptError) as error:
        incompatible.validate(restored)
    assert error.value.code == "prompt_definition_incompatible"
    with pytest.raises(PromptError) as runtime_error:
        incompatible.resolve("scope-a", Prompt(artifact_id="memory.extract", revision=1, content=restored))
    assert runtime_error.value.during_inference


@pytest.mark.parametrize("field", ("input", "expected_output"))
def test_demonstrations_cannot_add_authority_fields_to_the_operation_contract(field: str) -> None:
    demonstration = {
        "input": {"evidence": [], "current_entries": []},
        "expected_output": {"candidates": []},
    }
    demonstration[field]["tools"] = ["execute_shell"]
    content = _with_demonstration(demonstration)
    with pytest.raises(PromptError) as caught:
        builtin_prompt_definitions()[0].validate(content)
    assert caught.value.code == "prompt_definition_incompatible"
    assert "execute_shell" not in str(caught.value)


def test_unavailable_components_reject_custom_but_allow_explicit_auto() -> None:
    registry = PromptRegistry(
        builtin_prompt_definitions(),
        injected=frozenset({"memory.extract"}),
        disabled=frozenset({"memory.rerank"}),
    )
    assert set(registry.capabilities) == set(PROMPT_KEYS)
    assert registry.capabilities["memory.extract"].status == "unsupported"
    assert registry.capabilities["memory.extract"].reason == "injected_component"
    assert registry.capabilities["memory.rerank"].reason == "operation_disabled"
    for key in PROMPT_KEYS:
        with pytest.raises(PromptError, match="effective component"):
            registry.validate(key, _content())
        registry.validate(key, _content(mode="auto", instructions=""))
    with pytest.raises(PromptError) as unknown:
        registry.get("unregistered")
    assert unknown.value.code == "unknown_prompt_key"


@pytest.mark.parametrize(
    "overrides",
    [
        {"mode": "auto"},
        {"instructions": "   "},
        {"instructions": "a" * 32_769},
        {"model": "caller-selected"},
        {"demonstrations": [{"input": {}, "expected_output": {}, "label": "positive"}]},
        {"demonstrations": [{"input": "a" * 65_536, "expected_output": {}}]},
        {"demonstrations": [{"input": {}, "expected_output": {}}] * 51},
        {"demonstrations": [{"input": "a" * 60_000, "expected_output": {}}] * 5},
    ],
)
def test_invalid_or_oversized_content_is_rejected(overrides: dict[str, object]) -> None:
    payload = {
        "schema_version": "powercontext.prompt.v1",
        "mode": "custom",
        "instructions": "Keep preferences.",
        "demonstrations": [],
    } | overrides
    with pytest.raises(ValidationError):
        PromptContent.model_validate_json(json.dumps(payload))


def test_instructions_have_one_canonical_unicode_representation() -> None:
    assert _content(instructions="  Cafe\u0301  ").instructions == "Café"


def test_inconsistent_definitions_and_duplicate_keys_fail_before_composition() -> None:
    extraction = builtin_prompt_definitions()[0]
    with pytest.raises(ValueError, match="versions"):
        replace(extraction, builtin_version="")
    with pytest.raises(ValueError, match="classification"):
        replace(extraction, noop_field="proposal")
    with pytest.raises(ValueError, match="unique"):
        PromptRegistry((extraction, extraction))


def test_all_registered_auto_selections_preserve_existing_guidance() -> None:
    for definition in builtin_prompt_definitions():
        absent = definition.resolve("scope", None)
        explicit = definition.resolve(
            "scope", Prompt(artifact_id=definition.key, revision=1, content=_content(mode="auto", instructions=""))
        )
        assert absent.compiled_instructions == definition.default_instructions
        assert explicit.compiled_instructions == definition.default_instructions
        assert absent.compiled_digest == explicit.compiled_digest
