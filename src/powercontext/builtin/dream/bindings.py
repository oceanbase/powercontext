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

from dataclasses import dataclass

from powercontext.builtin.artifacts.experience import EXPERIENCE_INCUBATION_CURSOR_NAME
from powercontext.builtin.artifacts.profile.models import PROFILE_SOURCE_WINDOW_BINDING
from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.dream.models import DreamOperation
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME

SKILL_DREAM_BINDING = "skill.dream.v1"


@dataclass(frozen=True)
class DreamOperationSpec:
    operation: DreamOperation
    family: str
    binding: str


# An operation belongs here only after its resolver, generator, Candidate and
# approval writer are implemented. This registry controls worker dispatch as
# well as admission; a readable Artifact Family alone does not enable Dream.
DREAM_OPERATIONS = (
    DreamOperationSpec("refine_experience", "experience", EXPERIENCE_INCUBATION_CURSOR_NAME),
    DreamOperationSpec("derive_skill", "skill", SKILL_DREAM_BINDING),
    DreamOperationSpec("revise_profile", "profile", PROFILE_SOURCE_WINDOW_BINDING),
    DreamOperationSpec("revise_memory", "memory", SOURCE_WINDOW_TRIGGER_NAME),
    DreamOperationSpec("revise_topic_memory", "topic-memory", TOPIC_MEMORY_SOURCE_WINDOW_BINDING),
)
DREAM_BINDINGS = {spec.operation: spec.binding for spec in DREAM_OPERATIONS}


def operations_for_binding(binding: str) -> tuple[DreamOperation, ...]:
    return tuple(spec.operation for spec in DREAM_OPERATIONS if spec.binding == binding)


DREAM_PROVIDERS = frozenset({"openai", "openai-chat", "openai-responses", "anthropic"})
