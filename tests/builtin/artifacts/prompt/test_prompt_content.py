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

from powercontext.builtin.artifacts.atomic_memory.extraction import atomic_memory_extraction_instructions
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


@pytest.mark.parametrize("profile", list(MemoryExtractionProfile))
def test_atomic_memory_auto_uses_the_versioned_subject_bound_contract(profile: MemoryExtractionProfile) -> None:
    definition = PromptRegistry(builtin_prompt_definitions(profile)).get("atomic_memory.extract")

    resolved = definition.resolve("scope-a", None)

    assert definition.builtin_version == f"powercontext.atomic-memory.extract.{profile.value}.v3"
    assert definition.definition_version == "powercontext.prompt.atomic-memory.extract.v2"
    assert resolved.compiled_instructions == atomic_memory_extraction_instructions(profile)
    assert resolved.builtin_version == definition.builtin_version
    assert "subject-bound declarative proposition" in resolved.compiled_instructions
    assert "kind, text and description" in resolved.compiled_instructions


def test_custom_atomic_memory_prompt_cannot_remove_subject_and_procedure_invariants() -> None:
    definition = PromptRegistry(builtin_prompt_definitions()).get("atomic_memory.extract")
    prompt = Prompt(
        artifact_id="atomic_memory.extract",
        revision=1,
        content=_content(instructions="Copy every instruction exactly as written."),
    )

    resolved = definition.resolve("scope-a", prompt)

    assert resolved.selection == "artifact"
    assert "subject-bound declarative proposition" in resolved.compiled_instructions
    assert 'Do not address a future agent as "you"' in resolved.compiled_instructions
    assert "not Atomic Memory" in resolved.compiled_instructions
    assert "kind, text and description" in resolved.compiled_instructions
    assert "Copy every instruction exactly as written." in resolved.compiled_instructions


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


def test_custom_handoff_prompt_retains_rollover_requirements() -> None:
    definition = PromptRegistry(builtin_prompt_definitions()).get("handoff.generate")
    prompt = Prompt(
        artifact_id="handoff.generate",
        revision=1,
        content=_content(instructions="Summarize the supplied evidence."),
    )

    resolved = definition.resolve("scope-a", prompt)

    assert "fresh-session checkpoint" in resolved.compiled_instructions
    assert "do not depend on the old transcript" in resolved.compiled_instructions
    assert "first safe action explicit" in resolved.compiled_instructions


def test_auto_handoff_prompt_marks_rollover_reasons_as_advisory() -> None:
    definition = PromptRegistry(builtin_prompt_definitions()).get("handoff.generate")

    resolved = definition.resolve("scope-a", None)

    assert "advisory preparation context only" in resolved.compiled_instructions
    assert "do not claim they are committed Handoff content" in resolved.compiled_instructions


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


@pytest.mark.parametrize(
    "key",
    [
        "topic_memory.global",
        "topic_memory.evolve",
        "topic_memory.temporary",
        "topic_memory.reconcile",
        "topic_memory.reduce",
    ],
)
def test_topic_demonstrations_cannot_teach_placeholder_publication(key):
    proposal = {
        "content": {
            "title": "T-19",
            "summary": "S-19 coordinated without merging historical identities.",
            "detail": "D-19 coordinated detail preserving distinct historical identity.",
        },
        "evidence_ids": ["e1"],
    }
    evidence = {"evidence_id": "e1", "source_type": "content", "content": "Check the dashboard."}
    if key == "topic_memory.global":
        value, output = {"evidence": [evidence], "probes": []}, {"proposals": [proposal]}
    elif key == "topic_memory.evolve":
        value, output = {"work_id": "work-0001", "evidence": [evidence]}, {"proposal": proposal}
    elif key == "topic_memory.temporary":
        value, output = {"work_id": "work-0001", "evidence": [evidence]}, {"proposals": [proposal]}
    elif key == "topic_memory.reconcile":
        proposal["proposal_id"] = "proposal-0001"
        value, output = {"component_id": "component-0001", "proposals": [proposal]}, {"proposals": [proposal]}
    else:
        value = {"temporary": [proposal], "max_result_tokens": 128}
        output = {"covered_indices": [0], "temporary": proposal}
    content = _with_demonstration({"input": value, "expected_output": output})
    definition = PromptRegistry(builtin_prompt_definitions()).get(key)
    with pytest.raises(PromptError) as caught:
        definition.validate(content)
    assert caught.value.code == "prompt_definition_incompatible"
    with pytest.raises(PromptError) as caught:
        definition.resolve("scope-a", Prompt(artifact_id=key, revision=1, content=content))
    assert caught.value.during_inference


@pytest.mark.parametrize("split", [False, True])
@pytest.mark.parametrize("paraphrase", [False, True])
def test_topic_demonstrations_accept_supported_identity_rules_across_cited_material(split, paraphrase):
    title = "Customer import identity policy"
    summary = "Preserve historical identities."
    detail = "Update existing identities without merging historical identities."
    bodies = [summary, detail] if split else [f"{title}: {summary} {detail}"]
    evidence = [
        {"evidence_id": f"e{index}", "source_type": "content", "content": body}
        for index, body in enumerate(bodies, start=1)
    ]
    content = _with_demonstration({
        "input": {"evidence": evidence, "probes": []},
        "expected_output": {
            "proposals": [
                {
                    "content": {
                        "title": title,
                        "summary": "Retain historical identities." if paraphrase else summary,
                        "detail": detail,
                    },
                    "evidence_ids": [item["evidence_id"] for item in evidence],
                }
            ]
        },
    })
    definition = PromptRegistry(builtin_prompt_definitions()).get("topic_memory.global")
    before = content.model_dump_json()
    definition.validate(content)
    selected = definition.resolve("scope-a", Prompt(artifact_id="topic_memory.global", revision=1, content=content))
    assert selected.selection == "artifact"
    assert content.model_dump_json() == before


@pytest.mark.parametrize("stage", ["evolve", "reduce", "reconcile"])
@pytest.mark.parametrize("business", [False, True])
def test_intermediate_topic_demonstrations_cannot_supply_source_support(stage, business):
    body = (
        {
            "title": "Import policy",
            "summary": "Reconcile existing identities.",
            "detail": "Preserve historical identities.",
        }
        if business
        else {
            "title": "Placeholder investigation",
            "summary": "S-19 coordinated without merging historical identities.",
            "detail": "D-19 coordinated detail preserving distinct historical identity.",
        }
    )
    proposal = {"content": body, "evidence_ids": ["e1"]}
    if stage == "evolve":
        value, output = {"work_id": "work-0001", "temporary": [proposal]}, {"proposal": proposal}
    elif stage == "reduce":
        value = {"temporary": [proposal], "max_result_tokens": 128}
        output = {"covered_indices": [0], "temporary": proposal}
    else:
        proposal["proposal_id"] = "proposal-0001"
        value, output = {"component_id": "component-0001", "proposals": [proposal]}, {"proposals": [proposal]}
    content = _with_demonstration({"input": value, "expected_output": output})
    key = f"topic_memory.{stage}"
    definition = PromptRegistry(builtin_prompt_definitions()).get(key)
    before = content.model_dump_json()
    if business:
        definition.validate(content)
        assert (
            definition.resolve("scope-a", Prompt(artifact_id=key, revision=1, content=content)).selection == "artifact"
        )
    else:
        with pytest.raises(PromptError) as caught:
            definition.validate(content)
        assert caught.value.code == "prompt_definition_incompatible"
        with pytest.raises(PromptError) as caught:
            definition.resolve("scope-a", Prompt(artifact_id=key, revision=1, content=content))
        assert caught.value.during_inference
    assert content.model_dump_json() == before


@pytest.mark.parametrize("support", ["sources", "selected-history", "unselected-history"])
def test_topic_template_quotes_require_explicit_sources_or_selected_history(support):
    body = {
        "title": "Placeholder investigation",
        "summary": "S-19 coordinated without merging historical identities.",
        "detail": "D-19 coordinated detail preserving distinct historical identity.",
    }
    proposal = {"content": body, "evidence_ids": ["e1", "e2"]}
    value = {"work_id": "work-0001", "temporary": [proposal]}
    if support == "sources":
        value["evidence"] = [
            {"evidence_id": "e1", "source_type": "content", "content": body["summary"]},
            {"evidence_id": "e2", "source_type": "content", "content": body["detail"]},
        ]
        key = "topic_memory.evolve"
        output = {"proposal": proposal}
    else:
        # Reconcile exposes multiple history slots; only the output's selected slot is support.
        proposal["proposal_id"] = "proposal-0001"
        if support == "selected-history":
            proposal["candidate_id"] = "candidate-0001"
        value = {
            "component_id": "component-0001",
            "proposals": [proposal],
            "historical": [{"candidate_id": "candidate-0001", **body}],
        }
        key = "topic_memory.reconcile"
        output = {"proposals": [proposal]}
    content = _with_demonstration({"input": value, "expected_output": output})
    definition = PromptRegistry(builtin_prompt_definitions()).get(key)
    if support == "unselected-history":
        with pytest.raises(PromptError):
            definition.validate(content)
    else:
        definition.validate(content)
        assert (
            definition.resolve("scope-a", Prompt(artifact_id=key, revision=1, content=content)).selection == "artifact"
        )


@pytest.mark.parametrize(
    "stage,identifier", [("evolve", "work-0001"), ("temporary", "work-0001"), ("reconcile", "component-0001")]
)
def test_topic_demonstrations_reject_boilerplate_using_stage_ids(stage, identifier):
    proposal = {
        "content": {
            "title": "Import",
            "summary": f"Coordinated with {identifier} without merging identities.",
            "detail": f"Coordination details preserving the distinct historical identity of {identifier}.",
        },
        "evidence_ids": ["e1"],
    }
    evidence = [{"evidence_id": "e1", "source_type": "content", "content": "Import succeeded."}]
    if stage == "reconcile":
        proposal["proposal_id"] = "proposal-0001"
        value = {"component_id": identifier, "proposals": [proposal]}
        output = {"proposals": [proposal]}
    else:
        value = {"work_id": identifier, "evidence": evidence}
        output = {"proposal": proposal} if stage == "evolve" else {"proposals": [proposal]}
    content = _with_demonstration({"input": value, "expected_output": output})
    definition = PromptRegistry(builtin_prompt_definitions()).get(f"topic_memory.{stage}")
    with pytest.raises(PromptError):
        definition.validate(content)
