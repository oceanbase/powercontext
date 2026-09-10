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
from datetime import UTC, datetime
from typing import TypedDict, cast

import pytest
from pydantic import ValidationError

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.builtin.artifacts.experience import ExperienceContent, ExperienceSearchHit
from powercontext.builtin.artifacts.memory import MemoryHit
from powercontext.builtin.artifacts.profile.models import Profile, ProfileContent, ProfileGeneration
from powercontext.builtin.artifacts.topic_memory import TopicMemorySearchHit
from powercontext.builtin.runtime import ContextAssembly, PrepareContextRequest
from powercontext.builtin.runtime.errors import PreparedContextInvariantError
from powercontext.builtin.runtime.prepared_context import (
    PreparedContextBuilder,
    PreparedExperienceCandidates,
    PreparedMemoryCandidates,
    PreparedProfileCandidate,
)

MEMORY_REF = ArtifactRef(family="memory", artifact_id="memory", revision=3)


class _PreparedArtifactRef(TypedDict):
    family: str


class _PreparedCitation(TypedDict, total=False):
    entry_id: str
    artifact_ref: _PreparedArtifactRef
    memory_ref: object
    memory: object
    artifact: object


class _PreparedItem(TypedDict):
    kind: str
    citation: _PreparedCitation
    content: str
    truncated: bool


def _hit(entry_id: str, text: str, *, memory_ref: ArtifactRef = MEMORY_REF) -> MemoryHit:
    return MemoryHit(
        memory_ref=memory_ref,
        entry_id=entry_id,
        entry_version_id=f"{entry_id}-v1",
        text=text,
        score=1.0,
        matched_by=("fts",),
    )


def _items(content: str | None) -> list[_PreparedItem]:
    assert content is not None
    return cast(list[_PreparedItem], json.loads(content.splitlines()[-2])["items"])


def _experience_hit(artifact_id: str = "experience-1", revision: int = 1) -> ExperienceSearchHit:
    return ExperienceSearchHit(
        artifact_ref=ArtifactRef(family="experience", artifact_id=artifact_id, revision=revision),
        content=ExperienceContent(
            situation="The generated API client is stale after an OpenAPI change.",
            action="Regenerate the client before running contract tests.",
            outcome="The checked-in transport matches the public contract.",
            lesson="Regenerate and inspect the client before contract tests.",
        ),
    )


def test_text_assembly_groups_selected_families_and_preserves_citations() -> None:
    prepared = PreparedContextBuilder().build(
        scope_id="project:test",
        memory_ref=MEMORY_REF,
        hits=(_hit("first", "First constraint"), _hit("second", "Second constraint")),
        experience_hits=(_experience_hit(), _experience_hit("experience-2")),
        request=PrepareContextRequest(
            query="client",
            assembly=ContextAssembly.model_validate({
                "sections": [{"family": "experience", "limit": 1}, {"family": "memory", "limit": 1}],
                "show": ["recall_rank", "confidence"],
            }),
        ),
    )
    assert prepared.status == "ready"
    assert prepared.content is not None
    content = prepared.content
    assert content.index("## Experience") < content.index("## Memory")
    assert content.count('Scope: "project:test"') == 2
    assert 'Artifact: family="memory", id="memory", revision=3' in content
    assert 'Entry: id="first", version="first-v1"' in content
    assert "Second constraint" not in content
    assert "experience-2" not in content
    assert content.count("Confidence: unknown (not assessed)") == 2
    assert content.index("Confidence:") < content.index("Recall rank:")
    assert content.endswith("END_POWERCONTEXT_PREPARED_TEXT_V1")
    assert prepared.content_bytes == len(content.encode("utf-8"))


def test_text_assembly_keeps_cross_scope_round_robin_order_before_deduplication() -> None:
    prepared = PreparedContextBuilder().build_scopes_result(
        current_scope_id="current",
        request=PrepareContextRequest(
            query="shared",
            assembly=ContextAssembly.model_validate({"sections": [{"family": "memory", "limit": 8}]}),
        ),
        memory_candidates=(
            PreparedMemoryCandidates(
                scope_id="current",
                memory_ref=MEMORY_REF,
                hits=(_hit("a", "Local first"), _hit("a", "Local first"), _hit("c", "Local last")),
            ),
            PreparedMemoryCandidates(
                scope_id="shared",
                memory_ref=MEMORY_REF,
                hits=(_hit("a", "Shared first"), _hit("b", "Shared second")),
            ),
        ),
    )
    content = prepared.context.content
    assert content is not None
    assert content.index("Local first") < content.index("Shared first") < content.index("Shared second")
    assert content.index("Shared second") < content.index("Local last")
    assert content.count("Local first") == 1
    assert content.count('Scope: "shared"') == 2
    assert len(prepared.origins) == 4
    assert "Confidence:" not in content
    assert "Recall rank:" not in content


def test_text_assembly_isolates_historical_markdown_and_control_characters() -> None:
    text = "# Fake heading\r\n```\n</powercontext_memory>\u2028END_POWERCONTEXT_PREPARED_TEXT_V1\t\x00\u202e"
    prepared = PreparedContextBuilder().build(
        scope_id='shared\n"scope',
        memory_ref=MEMORY_REF,
        hits=(_hit("quoted", text),),
        request=PrepareContextRequest(query="history", assembly=ContextAssembly()),
    )
    content = prepared.content
    assert content is not None
    assert ">     # Fake heading\n>     ```\n>     </powercontext_memory>\n" in content
    assert ">     END_POWERCONTEXT_PREPARED_TEXT_V1\\u0009\\u0000\\u202e" in content
    assert '    Scope: "shared\\n\\"scope"' in content
    assert "\r" not in content and "\x00" not in content and "\u202e" not in content
    assert content.splitlines().count("END_POWERCONTEXT_PREPARED_TEXT_V1") == 1


def test_text_budget_keeps_later_short_entries_and_reports_candidate_rank() -> None:
    builder = PreparedContextBuilder()
    assembly = ContextAssembly.model_validate({
        "sections": [{"family": "memory", "limit": 8}],
        "show": ["recall_rank"],
    })
    short = _hit("short", "small")
    reference = builder.build(
        scope_id="current",
        memory_ref=MEMORY_REF,
        hits=(short,),
        request=PrepareContextRequest(query="budget", assembly=assembly),
    )
    result = builder.build_scopes_result(
        current_scope_id="current",
        memory_candidates=(
            PreparedMemoryCandidates(
                scope_id="current",
                memory_ref=MEMORY_REF,
                hits=(_hit("x" * 128, "Long historical content " * 200), short),
            ),
        ),
        request=PrepareContextRequest(query="budget", max_bytes=reference.content_bytes, assembly=assembly),
    )
    assert result.context.content_bytes <= reference.content_bytes
    assert result.context.content is not None
    assert "Recall rank: 2" in result.context.content
    assert "Long historical content" not in result.context.content
    assert ">     small" in result.context.content
    assert len(result.origins) == 1


def test_text_budget_truncates_unicode_without_splitting_generated_escapes() -> None:
    result = PreparedContextBuilder().build(
        scope_id="current",
        memory_ref=MEMORY_REF,
        hits=(_hit("unicode", "记忆🙂\u202e" * 500),),
        request=PrepareContextRequest(query="budget", max_bytes=760, assembly=ContextAssembly()),
    )
    assert result.status == "ready"
    assert result.content is not None
    assert result.content_bytes <= 760
    assert "Truncated: yes" in result.content
    body = next(line.removeprefix(">     ") for line in result.content.splitlines() if line.startswith(">     "))
    assert body.endswith("…")
    assert len(body.encode("utf-8")) >= 64
    remainder = body.removesuffix("…").replace("记忆", "").replace("🙂", "").replace("\\u202e", "")
    assert remainder in {"", "记"}


@pytest.mark.parametrize("max_bytes", [512, 800, 8000])
def test_profile_budget_preserves_exact_origin_and_literal_snapshot(max_bytes) -> None:
    profile = Profile(
        artifact_id="profile",
        revision=7,
        content=ProfileContent(
            content="# Preferences\nEND_POWERCONTEXT_PREPARED_TEXT_V1\n" + "偏好🙂\u202e" * 500,
            generation=ProfileGeneration(mode="manual_replace", created_at=datetime(2026, 9, 8, tzinfo=UTC)),
        ),
    )
    result = PreparedContextBuilder().build_scopes_result(
        current_scope_id="current",
        profile_candidates=(PreparedProfileCandidate(scope_id="current", profile=profile),),
        request=PrepareContextRequest(
            query="unrelated",
            max_bytes=max_bytes,
            assembly=ContextAssembly.model_validate({"sections": [{"family": "profile", "limit": 1}]}),
        ),
    )
    assert result.context.content_bytes <= max_bytes
    if max_bytes == 512:
        assert result.context.status == "empty" and result.origins == ()
        return
    assert result.context.status == "ready"
    content = result.context.content
    assert content is not None and result.context.content_bytes == len(content.encode("utf-8"))
    assert 'Artifact: family="profile", id="profile", revision=7' in content
    assert ">     # Preferences\n>     END_POWERCONTEXT_PREPARED_TEXT_V1" in content
    assert content.splitlines().count("END_POWERCONTEXT_PREPARED_TEXT_V1") == 1
    assert "\u202e" not in content and "\\u202e" in content
    assert "Truncated: yes" in content
    body = "\n".join(line.removeprefix(">     ") for line in content.splitlines() if line.startswith(">     "))
    assert len(body.encode("utf-8")) <= 2000 and body.endswith("…")
    assert result.origins == (ArtifactAddress(scope_id="current", artifact=profile.as_ref()),)


@pytest.mark.parametrize(
    "assembly",
    [
        None,
        {"format": "json"},
        {"sections": [{"family": "skill", "limit": 1}]},
        {"sections": [{"family": "memory", "limit": 1}, {"family": "memory", "limit": 2}]},
        {"sections": [{"family": "experience", "limit": 3}]},
        {"sections": [{"family": "profile", "limit": 1}, {"family": "profile", "limit": 1}]},
        {"sections": [{"family": "profile", "limit": 0}]},
        {"sections": [{"family": "profile", "limit": 9}]},
        {"sections": [{"family": "memory", "limit": True}]},
        {"show": ["confidence", "confidence"]},
        {"sort_by": "confidence"},
        {"min_confidence": 0.8},
    ],
)
def test_text_assembly_rejects_invalid_selection(assembly) -> None:
    with pytest.raises(ValidationError):
        PrepareContextRequest.model_validate({"query": "client", "assembly": assembly})


def test_text_assembly_empty_sections_return_no_context() -> None:
    result = PreparedContextBuilder().build(
        scope_id="current",
        memory_ref=MEMORY_REF,
        hits=(_hit("first", "Useful content"),),
        request=PrepareContextRequest(query="client", assembly=ContextAssembly(sections=())),
    )
    assert result.status == "empty"
    assert result.content is None
    assert result.content_bytes == 0


def _topic_hit(artifact_id: str = "topic-1", revision: int = 1) -> TopicMemorySearchHit:
    return TopicMemorySearchHit(
        artifact_ref=ArtifactRef(family="topic-memory", artifact_id=artifact_id, revision=revision),
        title=f"Title {artifact_id}",
        summary=f"Summary {artifact_id}",
        snippet=f"Snippet {artifact_id}",
        score=1.0,
        matched_by=("topic_fts",),
    )


def test_text_assembly_excludes_unselected_topic_memory() -> None:
    prepared = PreparedContextBuilder().build(
        scope_id="current",
        memory_ref=MEMORY_REF,
        hits=(_hit("constraint", "Selected memory constraint"),),
        topic_memory_hits=(_topic_hit("unselected-topic"),),
        request=PrepareContextRequest(query="context", assembly=ContextAssembly()),
    )
    assert prepared.content is not None
    assert "Selected memory constraint" in prepared.content
    assert "unselected-topic" not in prepared.content
    assert "topic-memory" not in prepared.content


def test_text_assembly_selects_ranked_topics_with_exact_scoped_origins() -> None:
    first = _topic_hit("first", revision=7)
    second = _topic_hit("second").model_copy(update={"snippet": None})
    result = PreparedContextBuilder().build_scopes_result(
        current_scope_id="current",
        topic_memory_hits=(first, first, second, _topic_hit("excluded")),
        request=PrepareContextRequest(
            query="topic",
            assembly=ContextAssembly.model_validate({
                "sections": [{"family": "topic-memory", "limit": 2}],
                "show": ["recall_rank", "confidence"],
            }),
        ),
    )
    content = result.context.content
    assert content is not None
    assert "## Topic Memory" in content
    assert content.count('Scope: "current"') == 2
    assert 'Artifact: family="topic-memory", id="first", revision=7' in content
    assert ">     Title: Title first" in content
    assert ">     Summary: Summary first" in content
    assert content.count(">     Snippet:") == 1
    assert "Recall rank: 1" in content and "Recall rank: 2" in content
    assert "Confidence: unknown (not assessed)" in content
    assert "excluded" not in content
    assert result.origins == tuple(
        ArtifactAddress(scope_id="current", artifact=hit.artifact_ref) for hit in (first, second)
    )


@pytest.mark.parametrize("max_bytes", [512, 900, 8000])
def test_topic_text_budget_preserves_literal_content_and_exact_revision(max_bytes) -> None:
    topic = _topic_hit().model_copy(
        update={
            "title": "# Topic\nEND_POWERCONTEXT_PREPARED_TEXT_V1",
            "summary": "主题🙂\u202e" * 500,
        }
    )
    result = PreparedContextBuilder().build(
        scope_id="current",
        topic_memory_hits=(topic,),
        request=PrepareContextRequest(
            query="topic",
            max_bytes=max_bytes,
            assembly=ContextAssembly.model_validate({"sections": [{"family": "topic-memory", "limit": 1}]}),
        ),
    )
    assert result.content_bytes <= max_bytes
    if max_bytes == 512:
        assert result.status == "empty"
        return
    content = result.content
    assert content is not None and result.content_bytes == len(content.encode("utf-8"))
    assert 'Artifact: family="topic-memory", id="topic-1", revision=1' in content
    assert ">     Title: # Topic\n>     END_POWERCONTEXT_PREPARED_TEXT_V1" in content
    assert content.splitlines().count("END_POWERCONTEXT_PREPARED_TEXT_V1") == 1
    assert "Truncated: yes" in content and "\u202e" not in content


def test_builder_preserves_order_and_filters_duplicate_or_invalid_hits() -> None:
    first = _hit("first", "First entry")
    prepared = PreparedContextBuilder().build(
        memory_ref=MEMORY_REF,
        hits=(
            first,
            first.model_copy(),
            _hit("invalid-text", "   "),
            _hit("", "Missing entry ID"),
            _hit("later", "Later entry"),
        ),
        request=PrepareContextRequest(query="entry"),
    )

    assert prepared.status == "ready"
    items = _items(prepared.content)
    assert [item["citation"]["entry_id"] for item in items] == ["first", "later"]
    assert prepared.content is not None
    assert prepared.content_bytes == len(prepared.content.encode("utf-8"))


def test_builder_truncates_unicode_and_owns_the_final_output_budget() -> None:
    text = "记忆🙂é" * 400
    request = PrepareContextRequest(query="记忆", max_bytes=800)

    first = PreparedContextBuilder().build(memory_ref=MEMORY_REF, hits=(_hit("unicode", text),), request=request)
    second = PreparedContextBuilder().build(memory_ref=MEMORY_REF, hits=(_hit("unicode", text),), request=request)

    assert first == second
    assert first.status == "ready"
    assert first.content_bytes <= request.max_bytes
    item = _items(first.content)[0]
    assert item["truncated"] is True
    assert str(item["content"]).endswith("…")


def test_builder_does_not_accept_truncated_unicode_below_the_minimum_byte_size() -> None:
    hit = _hit("emoji", "🙂" * 200)

    too_small = PreparedContextBuilder().build(
        memory_ref=MEMORY_REF,
        hits=(hit,),
        request=PrepareContextRequest(query="emoji", max_bytes=590),
    )
    large_enough = PreparedContextBuilder().build(
        memory_ref=MEMORY_REF,
        hits=(hit,),
        request=PrepareContextRequest(query="emoji", max_bytes=594),
    )

    assert too_small.status == "empty"
    item = _items(large_enough.content)[0]
    assert len(item["content"].encode("utf-8")) >= 64


def test_builder_skips_an_entry_that_cannot_fit_but_keeps_a_later_shorter_one() -> None:
    long_identifier = "a" * 128
    prepared = PreparedContextBuilder().build(
        memory_ref=MEMORY_REF,
        hits=(
            _hit(long_identifier, "long content " * 200),
            _hit("short", "small"),
        ),
        request=PrepareContextRequest(query="entry", max_bytes=620),
    )

    assert prepared.status == "ready"
    assert [item["citation"]["entry_id"] for item in _items(prepared.content)] == ["short"]
    assert prepared.content_bytes <= 620


def test_builder_rejects_a_hit_from_a_different_memory_head() -> None:
    other_ref = MEMORY_REF.model_copy(update={"revision": 4})

    with pytest.raises(PreparedContextInvariantError, match="memory-ref-mismatch"):
        PreparedContextBuilder().build(
            memory_ref=MEMORY_REF,
            hits=(_hit("other", "Other head", memory_ref=other_ref),),
            request=PrepareContextRequest(query="head"),
        )


def test_builder_prepares_experience_without_a_memory_head_and_keeps_v1_envelope() -> None:
    prepared = PreparedContextBuilder().build(
        experience_hits=(_experience_hit(),),
        request=PrepareContextRequest(query="Regenerate client contract tests"),
    )

    assert prepared.status == "ready"
    assert prepared.schema_version == "powercontext.prepared-context.v1"
    assert prepared.content is not None
    assert "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1" in prepared.content
    item = _items(prepared.content)[0]
    assert item["kind"] == "experience"
    assert item["citation"] == {
        "artifact_ref": {
            "family": "experience",
            "artifact_id": "experience-1",
            "revision": 1,
        }
    }
    assert item["content"].endswith("Lesson: Regenerate and inspect the client before contract tests.")


def test_builder_qualifies_only_cross_scope_citations() -> None:
    builder = PreparedContextBuilder()
    prepared = builder.build_scopes_result(
        request=PrepareContextRequest(query="shared evidence"),
        current_scope_id="current",
        memory_candidates=(
            PreparedMemoryCandidates(scope_id="current", memory_ref=MEMORY_REF, hits=(_hit("local", "Local"),)),
            PreparedMemoryCandidates(scope_id="shared", memory_ref=MEMORY_REF, hits=(_hit("shared", "Shared"),)),
        ),
        experience_candidates=(PreparedExperienceCandidates(scope_id="shared", hits=(_experience_hit(),)),),
    ).context

    local, experience, shared = _items(prepared.content)
    assert local["citation"]["memory_ref"] == MEMORY_REF.model_dump(mode="json")
    assert shared["citation"]["memory"] == {
        "scope_id": "shared",
        "artifact": MEMORY_REF.model_dump(mode="json"),
    }
    assert experience["citation"]["artifact"] == {
        "scope_id": "shared",
        "artifact": {
            "family": "experience",
            "artifact_id": "experience-1",
            "revision": 1,
        },
    }


def test_builder_keeps_memory_primary_and_bounds_experience_share() -> None:
    experiences = tuple(_experience_hit(f"experience-{index}") for index in range(1, 3))
    prepared = PreparedContextBuilder().build(
        memory_ref=MEMORY_REF,
        hits=(_hit("first", "First Memory entry"), _hit("second", "Second Memory entry")),
        topic_memory_hits=(_topic_hit("topic-1"), _topic_hit("topic-2")),
        experience_hits=experiences,
        request=PrepareContextRequest(query="client"),
    )

    items = _items(prepared.content)
    assert [item.get("kind", "memory") for item in items] == [
        "memory",
        "topic-memory",
        "experience",
        "memory",
        "topic-memory",
        "experience",
    ]


def test_builder_allows_eight_topic_memories_without_a_global_entry_limit_or_detail() -> None:
    prepared = PreparedContextBuilder().build(
        topic_memory_hits=tuple(_topic_hit(f"topic-{index}") for index in range(8)),
        request=PrepareContextRequest(query="topic", max_bytes=32768),
    )

    items = _items(prepared.content)
    assert len(items) == 8
    assert all(item["kind"] == "topic-memory" for item in items)
    assert all("detail" not in item["content"] for item in items)
    assert all(item["citation"]["artifact_ref"]["family"] == "topic-memory" for item in items)


def test_builder_interleaves_families_within_the_global_eight_entry_limit() -> None:
    prepared = PreparedContextBuilder().build(
        memory_ref=MEMORY_REF,
        hits=tuple(_hit(f"memory-{index}", f"Memory {index}") for index in range(8)),
        topic_memory_hits=tuple(_topic_hit(f"topic-{index}") for index in range(8)),
        experience_hits=tuple(_experience_hit(f"experience-{index}") for index in range(2)),
        request=PrepareContextRequest(query="context", max_bytes=32768),
    )

    families = [item.get("kind", "memory") for item in _items(prepared.content)]
    assert len(families) == 8
    assert families[:6] == ["memory", "topic-memory", "experience"] * 2
    assert families.count("memory") == 3
    assert families.count("topic-memory") == 3
    assert families.count("experience") == 2


def test_builder_rejects_non_experience_recall_hits() -> None:
    hit = _experience_hit().model_copy(
        update={"artifact_ref": ArtifactRef(family="skill", artifact_id="skill-1", revision=1)}
    )

    with pytest.raises(PreparedContextInvariantError, match="experience-family-mismatch"):
        PreparedContextBuilder().build(
            experience_hits=(hit,),
            request=PrepareContextRequest(query="client"),
        )


@pytest.mark.parametrize(
    "value",
    [
        {"query": "   "},
        {"query": "query", "max_bytes": True},
        {"query": "query", "max_bytes": 511},
        {"query": "query", "max_bytes": 32769},
    ],
)
def test_prepare_request_rejects_values_outside_the_runtime_contract(value: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PrepareContextRequest.model_validate(value)


def test_empty_context_has_no_source_specific_status_or_content() -> None:
    prepared = PreparedContextBuilder().empty()

    assert prepared.status == "empty"
    assert prepared.content is None
    assert prepared.content_bytes == 0
