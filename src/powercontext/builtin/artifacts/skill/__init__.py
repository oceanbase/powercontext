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

"""Built-in managed Skill Artifact Family."""

from powercontext.builtin.artifacts.skill.external import (
    MAX_EXTERNAL_SKILL_DESCRIPTION_LENGTH,
    MAX_EXTERNAL_SKILL_FILES,
    MAX_EXTERNAL_SKILL_HOST_ID_LENGTH,
    MAX_EXTERNAL_SKILL_LOCATOR_LENGTH,
    MAX_EXTERNAL_SKILL_MANIFEST_BYTES,
    MAX_EXTERNAL_SKILL_NAME_LENGTH,
    MAX_EXTERNAL_SKILL_PACKAGE_BYTES,
    CodexSkillProvider,
    CodexSkillRoot,
    ExternalSkillInstallationScope,
    ExternalSkillNotFoundError,
    ExternalSkillProvider,
    ExternalSkillProviderScan,
    ExternalSkillRegistration,
    ExternalSkillRegistryUnavailableError,
    ExternalSkillResolution,
    ExternalSkillResolutionStatus,
    ExternalSkillSnapshot,
    ExternalSkillSnapshotUnavailableError,
)
from powercontext.builtin.artifacts.skill.generation import (
    LLMSkillGenerator,
    SkillGenerationOutput,
    SkillGenerator,
)
from powercontext.builtin.artifacts.skill.models import (
    MAX_SKILL_DESCRIPTION_LENGTH,
    MAX_SKILL_INSTRUCTIONS_LENGTH,
    MAX_SKILL_NAME_LENGTH,
    MAX_SKILL_VALIDATION_ITEM_LENGTH,
    MAX_SKILL_VALIDATION_ITEMS,
    Skill,
    SkillContent,
    SkillDraft,
)
from powercontext.builtin.artifacts.skill.prompts import (
    SKILL_GENERATION_INSTRUCTIONS,
    SKILL_GENERATION_INSTRUCTIONS_VERSION,
)

__all__ = [
    "MAX_EXTERNAL_SKILL_DESCRIPTION_LENGTH",
    "MAX_EXTERNAL_SKILL_FILES",
    "MAX_EXTERNAL_SKILL_HOST_ID_LENGTH",
    "MAX_EXTERNAL_SKILL_LOCATOR_LENGTH",
    "MAX_EXTERNAL_SKILL_MANIFEST_BYTES",
    "MAX_EXTERNAL_SKILL_NAME_LENGTH",
    "MAX_EXTERNAL_SKILL_PACKAGE_BYTES",
    "MAX_SKILL_DESCRIPTION_LENGTH",
    "MAX_SKILL_INSTRUCTIONS_LENGTH",
    "MAX_SKILL_NAME_LENGTH",
    "MAX_SKILL_VALIDATION_ITEMS",
    "MAX_SKILL_VALIDATION_ITEM_LENGTH",
    "SKILL_GENERATION_INSTRUCTIONS",
    "SKILL_GENERATION_INSTRUCTIONS_VERSION",
    "CodexSkillProvider",
    "CodexSkillRoot",
    "ExternalSkillInstallationScope",
    "ExternalSkillNotFoundError",
    "ExternalSkillProvider",
    "ExternalSkillProviderScan",
    "ExternalSkillRegistration",
    "ExternalSkillRegistryUnavailableError",
    "ExternalSkillResolution",
    "ExternalSkillResolutionStatus",
    "ExternalSkillSnapshot",
    "ExternalSkillSnapshotUnavailableError",
    "LLMSkillGenerator",
    "Skill",
    "SkillContent",
    "SkillDraft",
    "SkillGenerationOutput",
    "SkillGenerator",
]
