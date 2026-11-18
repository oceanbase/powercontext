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

"""Map enabled Dream operations to their owning Family processing binding."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.artifacts.experience import EXPERIENCE_INCUBATION_CURSOR_NAME, ExperienceContent
from powercontext.builtin.artifacts.handoff.models import HandoffContent
from powercontext.builtin.artifacts.profile.models import PROFILE_SOURCE_WINDOW_BINDING, ProfileWriteContent
from powercontext.builtin.artifacts.prompt.models import PromptContent
from powercontext.builtin.artifacts.skill import SkillContent
from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.artifacts.topic_memory.models import TopicMemoryContent
from powercontext.builtin.catalog_changes.models import TagChangeProposal
from powercontext.builtin.dream.models import CreateDreamRunRequest, DreamError, DreamOperation, DreamPlan
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME

SKILL_DREAM_BINDING = "skill.dream.v1"
HANDOFF_DREAM_BINDING = "handoff.dream.v1"
PROMPT_DREAM_BINDING = "prompt.dream.v1"

DreamEffect = Literal[
    "review_then_publish",
    "review_then_publish_configuration",
    "review_then_replace_tags",
]


@dataclass(frozen=True)
class DreamOperationSpec:
    """Server-owned adapters and contracts; callers cannot supply executable behavior."""

    operation: DreamOperation
    family: str | None
    binding: str | None
    target_kind: str
    allowed_evidence: tuple[str, ...]
    proposal_schema: type[BaseModel]
    review_adapter: str
    commit_adapter: str
    effect: DreamEffect = "review_then_publish"
    spec_version: str = "powercontext.dream.operation.v1"
    validator: Callable[[DreamPlan, CreateDreamRunRequest], None] = DreamPlan.validate_operation
    output_kind: Literal["candidate", "tag_candidate"] = "candidate"
    # Whether this operation may ever run on a pinned cross-Scope input set. Default
    # deny: a Family appearing in ``allowed_evidence`` is not authority to share, and a
    # new operation is not shared until it is deliberately declared here.
    shared_maintenance: bool = False

    @property
    def processing_family(self) -> str | None:
        return "memory" if self.family == "atomic-memory" else self.family


DREAM_OPERATIONS = (
    DreamOperationSpec(
        "refine_experience",
        "experience",
        EXPERIENCE_INCUBATION_CURSOR_NAME,
        "optional_artifact",
        ("experience", "atomic-memory", "source"),
        ExperienceContent,
        "approve",
        "experience.revise",
        shared_maintenance=True,
    ),
    DreamOperationSpec(
        "derive_skill",
        "skill",
        SKILL_DREAM_BINDING,
        "none",
        ("experience", "source"),
        SkillContent,
        "approve",
        "skill.create",
        shared_maintenance=True,
    ),
    DreamOperationSpec(
        "revise_skill",
        "skill",
        SKILL_DREAM_BINDING,
        "artifact",
        ("skill", "experience", "atomic-memory", "source"),
        SkillContent,
        "approve",
        "skill.revise",
        shared_maintenance=True,
    ),
    DreamOperationSpec(
        "revise_profile",
        "profile",
        PROFILE_SOURCE_WINDOW_BINDING,
        "artifact",
        ("profile", "experience", "atomic-memory", "source"),
        ProfileWriteContent,
        "decide_profile",
        "profile.revise",
        shared_maintenance=True,
    ),
    DreamOperationSpec(
        # A first Profile has no prior revision: the same Family, evidence and approval
        # path with an exact absence-to-create, on the existing Profile Worker binding.
        "initialize_profile",
        "profile",
        PROFILE_SOURCE_WINDOW_BINDING,
        "none",
        ("experience", "atomic-memory", "source"),
        ProfileWriteContent,
        "decide_profile",
        "profile.create",
        shared_maintenance=True,
    ),
    DreamOperationSpec(
        "revise_memory",
        "atomic-memory",
        SOURCE_WINDOW_TRIGGER_NAME,
        "artifact",
        ("atomic-memory", "experience", "source"),
        AtomicMemoryContent,
        "_approve_memory_dream",
        "atomic_memory.commit",
    ),
    DreamOperationSpec(
        "revise_topic_memory",
        "topic-memory",
        TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
        "artifact",
        ("topic-memory", "experience", "atomic-memory", "source"),
        TopicMemoryContent,
        "_approve_topic_memory_dream",
        "topic_memory.publish_revision",
    ),
    DreamOperationSpec(
        "refresh_handoff",
        "handoff",
        HANDOFF_DREAM_BINDING,
        "artifact",
        ("handoff", "experience", "atomic-memory", "source"),
        HandoffContent,
        "_approve_handoff_dream",
        "handoff.commit_reviewed",
    ),
    DreamOperationSpec(
        "revise_prompt",
        "prompt",
        PROMPT_DREAM_BINDING,
        "prompt_key",
        ("prompt", "experience", "atomic-memory", "source"),
        PromptContent,
        "_approve_prompt_dream",
        "prompt.commit_reviewed",
        "review_then_publish_configuration",
    ),
    # A Catalog job belongs to the target Artifact's existing processing Family.
    DreamOperationSpec(
        "revise_tags",
        None,
        None,
        "tag_target",
        ("artifact", "atomic-memory", "source"),
        TagChangeProposal,
        "catalog.approve",
        "catalog.replace_tags",
        "review_then_replace_tags",
        # Require complete supporting references and exclude the exact Tag basis from support.
        spec_version="powercontext.dream.operation.v3",
        output_kind="tag_candidate",
    ),
)
DREAM_SPECS = {spec.operation: spec for spec in DREAM_OPERATIONS}
DREAM_BINDINGS = {spec.operation: spec.binding for spec in DREAM_OPERATIONS if spec.binding is not None}
FAMILY_DREAM_BINDINGS = {spec.family: spec.binding for spec in DREAM_OPERATIONS if spec.family is not None}


def operation_spec(operation: DreamOperation) -> DreamOperationSpec:
    return DREAM_SPECS[operation]


def binding_for_request(request: CreateDreamRunRequest) -> str:
    if request.operation == "revise_tags":
        family = None if request.tag_target is None else request.tag_target.target.family
        binding = FAMILY_DREAM_BINDINGS.get(family)
    else:
        binding = DREAM_BINDINGS.get(request.operation)
    if binding is None:
        raise DreamError("capability_unavailable")
    return binding


def operations_for_binding(binding: str) -> tuple[DreamOperation, ...]:
    return tuple(
        spec.operation
        for spec in DREAM_OPERATIONS
        if spec.binding == binding or (spec.operation == "revise_tags" and binding in FAMILY_DREAM_BINDINGS.values())
    )


DREAM_PROVIDERS = frozenset({"openai", "openai-chat", "openai-responses", "anthropic"})
