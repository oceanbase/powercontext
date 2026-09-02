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

"""OpenDAL Connector worker for PowerContext."""

from powercontext_connector_opendal.connector import (
    OPENDAL_TEXT_FILE_CONNECTOR_NAME,
    OpenDALTextFileCheckpoint,
    OpenDALTextFileConnector,
)
from powercontext_connector_opendal.source import (
    TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION,
    TEXT_FILE_SNAPSHOT_SOURCE_NAME,
    TextFileEvidenceProjection,
    TextFileSnapshotCapture,
    TextFileSnapshotSource,
    TextFileSnapshotSourceDefinition,
)

__all__ = [
    "OPENDAL_TEXT_FILE_CONNECTOR_NAME",
    "TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION",
    "TEXT_FILE_SNAPSHOT_SOURCE_NAME",
    "OpenDALTextFileCheckpoint",
    "OpenDALTextFileConnector",
    "TextFileEvidenceProjection",
    "TextFileSnapshotCapture",
    "TextFileSnapshotSource",
    "TextFileSnapshotSourceDefinition",
]
