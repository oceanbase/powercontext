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

from powercontext.builtin.artifacts.skill.compatibility import (
    SkillCompatibilityAssessment,
    SkillCompatibilityState,
    SkillRuntimeManifest,
    SkillRuntimeRequirements,
    SkillRuntimeVariant,
    assess_skill_compatibility,
    target_environment_fingerprint,
)
from powercontext.builtin.artifacts.skill.external import (
    MAX_EXTERNAL_SKILL_DESCRIPTION_LENGTH,
    MAX_EXTERNAL_SKILL_FILES,
    MAX_EXTERNAL_SKILL_HOST_ID_LENGTH,
    MAX_EXTERNAL_SKILL_LOCATOR_LENGTH,
    MAX_EXTERNAL_SKILL_MANIFEST_BYTES,
    MAX_EXTERNAL_SKILL_NAME_LENGTH,
    MAX_EXTERNAL_SKILL_PACKAGE_BYTES,
    AgentEnvironmentProfile,
    AgentKind,
    AgentSkillProvider,
    AgentSkillTarget,
    CapturedExternalSkillPackage,
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
    MAX_SKILL_COMPATIBILITY_LENGTH,
    MAX_SKILL_DESCRIPTION_LENGTH,
    MAX_SKILL_INSTRUCTIONS_LENGTH,
    MAX_SKILL_NAME_LENGTH,
    MAX_SKILL_VALIDATION_ITEM_LENGTH,
    MAX_SKILL_VALIDATION_ITEMS,
    Skill,
    SkillContent,
    SkillDraft,
    SkillPackageRef,
)
from powercontext.builtin.artifacts.skill.package import (
    MAX_SKILL_ARCHIVE_BYTES,
    MAX_SKILL_ENTRYPOINT_BYTES,
    MAX_SKILL_MANIFEST_BYTES,
    MAX_SKILL_PACKAGE_BYTES,
    MAX_SKILL_PACKAGE_FILES,
    MAX_SKILL_PATH_BYTES,
    SKILL_ENTRYPOINT,
    SkillPackageEntry,
    SkillPackageError,
    SkillPackageMetadata,
    SkillPackageSnapshot,
    build_instruction_skill_package,
    capture_skill_archive,
    capture_skill_directory,
    materialize_skill_package,
    package_file,
)
from powercontext.builtin.artifacts.skill.prompts import (
    SKILL_GENERATION_INSTRUCTIONS,
    SKILL_GENERATION_INSTRUCTIONS_VERSION,
)
from powercontext.builtin.artifacts.skill.provenance import SkillOrigin, SkillOriginKind
from powercontext.builtin.artifacts.skill.search import SkillSearchHit, skill_search_text, skill_searchable_text

__all__ = [
    "MAX_EXTERNAL_SKILL_DESCRIPTION_LENGTH",
    "MAX_EXTERNAL_SKILL_FILES",
    "MAX_EXTERNAL_SKILL_HOST_ID_LENGTH",
    "MAX_EXTERNAL_SKILL_LOCATOR_LENGTH",
    "MAX_EXTERNAL_SKILL_MANIFEST_BYTES",
    "MAX_EXTERNAL_SKILL_NAME_LENGTH",
    "MAX_EXTERNAL_SKILL_PACKAGE_BYTES",
    "MAX_SKILL_ARCHIVE_BYTES",
    "MAX_SKILL_COMPATIBILITY_LENGTH",
    "MAX_SKILL_DESCRIPTION_LENGTH",
    "MAX_SKILL_ENTRYPOINT_BYTES",
    "MAX_SKILL_INSTRUCTIONS_LENGTH",
    "MAX_SKILL_MANIFEST_BYTES",
    "MAX_SKILL_NAME_LENGTH",
    "MAX_SKILL_PACKAGE_BYTES",
    "MAX_SKILL_PACKAGE_FILES",
    "MAX_SKILL_PATH_BYTES",
    "MAX_SKILL_VALIDATION_ITEMS",
    "MAX_SKILL_VALIDATION_ITEM_LENGTH",
    "SKILL_ENTRYPOINT",
    "SKILL_GENERATION_INSTRUCTIONS",
    "SKILL_GENERATION_INSTRUCTIONS_VERSION",
    "AgentEnvironmentProfile",
    "AgentKind",
    "AgentSkillProvider",
    "AgentSkillTarget",
    "CapturedExternalSkillPackage",
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
    "SkillCompatibilityAssessment",
    "SkillCompatibilityState",
    "SkillContent",
    "SkillDraft",
    "SkillGenerationOutput",
    "SkillGenerator",
    "SkillOrigin",
    "SkillOriginKind",
    "SkillPackageEntry",
    "SkillPackageError",
    "SkillPackageMetadata",
    "SkillPackageRef",
    "SkillPackageSnapshot",
    "SkillRuntimeManifest",
    "SkillRuntimeRequirements",
    "SkillRuntimeVariant",
    "SkillSearchHit",
    "assess_skill_compatibility",
    "build_instruction_skill_package",
    "capture_skill_archive",
    "capture_skill_directory",
    "materialize_skill_package",
    "package_file",
    "skill_search_text",
    "skill_searchable_text",
    "target_environment_fingerprint",
]
