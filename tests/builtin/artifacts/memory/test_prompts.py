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

"""Behavioral contracts for built-in Memory extraction profiles."""

from powercontext.builtin.artifacts.memory import (
    CONVERSATION_MEMORY_EXTRACTION_INSTRUCTIONS,
    MEMORY_EXTRACTION_INSTRUCTIONS,
    MemoryExtractionProfile,
    memory_extraction_instructions,
    memory_extraction_instructions_version,
)
from powercontext.builtin.runtime import RuntimeConfig


def test_runtime_defaults_to_the_existing_coding_profile() -> None:
    config = RuntimeConfig()

    assert config.memory_extraction_profile is MemoryExtractionProfile.CODING
    assert memory_extraction_instructions(config.memory_extraction_profile) == MEMORY_EXTRACTION_INSTRUCTIONS
    assert memory_extraction_instructions_version(config.memory_extraction_profile) == "powercontext.memory.extract.v1"


def test_conversation_profile_selects_its_versioned_policy() -> None:
    config = RuntimeConfig.model_validate({"memory_extraction_profile": "conversation"})

    assert config.memory_extraction_profile is MemoryExtractionProfile.CONVERSATION
    assert (
        memory_extraction_instructions(config.memory_extraction_profile) == CONVERSATION_MEMORY_EXTRACTION_INSTRUCTIONS
    )
    assert memory_extraction_instructions_version(config.memory_extraction_profile).endswith("conversation.v1")
    assert CONVERSATION_MEMORY_EXTRACTION_INSTRUCTIONS != MEMORY_EXTRACTION_INSTRUCTIONS
