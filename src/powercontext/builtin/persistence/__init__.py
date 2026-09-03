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

"""SQLAlchemy-backed relational persistence building blocks."""

from powercontext.builtin.persistence.agent_skill_targets import (
    RemoteAgentSkillTarget,
    RemoteAgentSkillTargetRepository,
    RemoteAgentSkillTargetState,
)
from powercontext.builtin.persistence.candidates import CandidateRepository
from powercontext.builtin.persistence.connectors import ConnectorCheckpointRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import (
    DatabaseClosedError,
    GenerationConflictError,
    IdentityMismatchError,
    InvalidRepositoryArgumentError,
    InvalidStoredColumnError,
    InvalidStoredPayloadError,
    PersistenceError,
    RepositoryError,
    RepositoryNotFoundError,
    StoredPayloadConflictError,
)
from powercontext.builtin.persistence.external_skills import ExternalSkillRepository
from powercontext.builtin.persistence.skill_packages import SkillPackageRepository
from powercontext.builtin.persistence.skill_publications import (
    SkillPublication,
    SkillPublicationDesiredState,
    SkillPublicationRepository,
)
from powercontext.builtin.persistence.source_definitions import SourceDefinitionManifestRepository
from powercontext.builtin.persistence.statistics import (
    StatisticsRepository,
    StoredInventoryCounts,
    StoredModelUsage,
    StoredRecallTokenUsage,
)

__all__ = (
    "AsyncDatabase",
    "CandidateRepository",
    "ConnectorCheckpointRepository",
    "DatabaseClosedError",
    "ExternalSkillRepository",
    "GenerationConflictError",
    "IdentityMismatchError",
    "InvalidRepositoryArgumentError",
    "InvalidStoredColumnError",
    "InvalidStoredPayloadError",
    "PersistenceError",
    "RemoteAgentSkillTarget",
    "RemoteAgentSkillTargetRepository",
    "RemoteAgentSkillTargetState",
    "RepositoryError",
    "RepositoryNotFoundError",
    "SkillPackageRepository",
    "SkillPublication",
    "SkillPublicationDesiredState",
    "SkillPublicationRepository",
    "SourceDefinitionManifestRepository",
    "StatisticsRepository",
    "StoredInventoryCounts",
    "StoredModelUsage",
    "StoredPayloadConflictError",
    "StoredRecallTokenUsage",
)
