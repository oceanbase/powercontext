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

"""Stable public exports for PowerContext clients."""

from powercontext.artifacts import (
    Artifact,
    ArtifactAddress,
    ArtifactCatalog,
    ArtifactDraft,
    ArtifactLineage,
    ArtifactRef,
    ArtifactStore,
)
from powercontext.context import Artifacts, PowerContext, Sources
from powercontext.errors import (
    ArtifactError,
    ArtifactFamilyMismatchError,
    ArtifactNotFoundError,
    ConnectorError,
    ConnectorSubmissionRejectedError,
    InvalidArtifactReferenceError,
    InvalidConnectorError,
    InvalidConnectorRunError,
    InvalidSourceAdapterError,
    InvalidSourceDefinitionError,
    InvalidSourceEntryError,
    InvalidSourceObservationError,
    InvalidSourceProjectionError,
    InvalidSourceReferenceError,
    InvalidSourceResultError,
    PowerContextError,
    RevisionConflictError,
    SourceAdapterNotFoundError,
    SourceConflictError,
    SourceDefinitionNotFoundError,
    SourceError,
    SourceNotFoundError,
    SourceProjectionNotFoundError,
)
from powercontext.sources import (
    AdapterSourceDefinition,
    Source,
    SourceAdapter,
    SourceCatalog,
    SourceCatalogBackend,
    SourceDefinition,
    SourceDefinitionRegistry,
    SourceMaterialization,
    SourceProjection,
    SourceProjectionKey,
    SourceRef,
    SourceStore,
)
from powercontext.triggers import PolicyTransition, Trigger

__all__ = [
    "AdapterSourceDefinition",
    "Artifact",
    "ArtifactAddress",
    "ArtifactCatalog",
    "ArtifactDraft",
    "ArtifactError",
    "ArtifactFamilyMismatchError",
    "ArtifactLineage",
    "ArtifactNotFoundError",
    "ArtifactRef",
    "ArtifactStore",
    "Artifacts",
    "ConnectorError",
    "ConnectorSubmissionRejectedError",
    "InvalidArtifactReferenceError",
    "InvalidConnectorError",
    "InvalidConnectorRunError",
    "InvalidSourceAdapterError",
    "InvalidSourceDefinitionError",
    "InvalidSourceEntryError",
    "InvalidSourceObservationError",
    "InvalidSourceProjectionError",
    "InvalidSourceReferenceError",
    "InvalidSourceResultError",
    "PolicyTransition",
    "PowerContext",
    "PowerContextError",
    "RevisionConflictError",
    "Source",
    "SourceAdapter",
    "SourceAdapterNotFoundError",
    "SourceCatalog",
    "SourceCatalogBackend",
    "SourceConflictError",
    "SourceDefinition",
    "SourceDefinitionNotFoundError",
    "SourceDefinitionRegistry",
    "SourceError",
    "SourceMaterialization",
    "SourceNotFoundError",
    "SourceProjection",
    "SourceProjectionKey",
    "SourceProjectionNotFoundError",
    "SourceRef",
    "SourceStore",
    "Sources",
    "Trigger",
]
