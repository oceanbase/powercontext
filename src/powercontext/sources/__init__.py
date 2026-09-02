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

from powercontext.sources.adapters import SourceAdapter
from powercontext.sources.catalog import SourceCatalog
from powercontext.sources.connectors import (
    Connector,
    ConnectorBinding,
    ConnectorItemOutcome,
    ConnectorRunCompletion,
    ConnectorRunResult,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)
from powercontext.sources.definitions import (
    AdapterSourceDefinition,
    SourceDefinition,
    SourceDefinitionRegistry,
    SourceProjection,
)
from powercontext.sources.models import Source, SourceMaterialization, SourceProjectionKey, SourceRef
from powercontext.sources.observations import (
    SourceDefinitionManifest,
    SourceObservation,
    SourceProjectionManifest,
    SourceProjectionValue,
    manifest_for_definition,
    project_source_for_transport,
)
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence
from powercontext.sources.protocols import SourceCatalogBackend, SourceStore

__all__ = [
    "TEXT_EVIDENCE_PROJECTION_KEY",
    "AdapterSourceDefinition",
    "Connector",
    "ConnectorBinding",
    "ConnectorItemOutcome",
    "ConnectorRunCompletion",
    "ConnectorRunResult",
    "ConnectorRunSession",
    "ConnectorRunStatus",
    "ConnectorSubmissionStatus",
    "Source",
    "SourceAdapter",
    "SourceCatalog",
    "SourceCatalogBackend",
    "SourceDefinition",
    "SourceDefinitionManifest",
    "SourceDefinitionRegistry",
    "SourceMaterialization",
    "SourceObservation",
    "SourceProjection",
    "SourceProjectionKey",
    "SourceProjectionManifest",
    "SourceProjectionValue",
    "SourceRef",
    "SourceStore",
    "TextEvidence",
    "manifest_for_definition",
    "project_source_for_transport",
]
