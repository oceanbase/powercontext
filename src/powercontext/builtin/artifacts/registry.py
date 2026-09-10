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

"""Single registration point for built-in Artifact families."""

from powercontext.artifacts import ArtifactFamilyDefinition, ArtifactFamilyRegistry
from powercontext.builtin.artifacts.experience import Experience
from powercontext.builtin.artifacts.handoff import Handoff
from powercontext.builtin.artifacts.memory import Memory
from powercontext.builtin.artifacts.profile import Profile
from powercontext.builtin.artifacts.prompt import Prompt
from powercontext.builtin.artifacts.skill import Skill
from powercontext.builtin.artifacts.topic_memory import TopicMemory

BUILTIN_ARTIFACT_FAMILY_REGISTRY = ArtifactFamilyRegistry((
    ArtifactFamilyDefinition(Memory, standard_write=True, supports_tags=True),
    ArtifactFamilyDefinition(Experience, standard_write=True, supports_tags=True),
    ArtifactFamilyDefinition(Skill, standard_write=True, supports_tags=True),
    ArtifactFamilyDefinition(Handoff, standard_write=True, supports_tags=True),
    ArtifactFamilyDefinition(Profile, standard_write=True),
    ArtifactFamilyDefinition(Prompt, standard_write=True),
    ArtifactFamilyDefinition(TopicMemory, list_order="published_at:desc"),
))

__all__ = ["BUILTIN_ARTIFACT_FAMILY_REGISTRY"]
