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

"""Built-in Source components."""

from powercontext.builtin.sources.content import (
    CONTENT_SOURCE_ADAPTER,
    CONTENT_SOURCE_DEFINITION,
    CONTENT_SOURCE_NAME,
    ContentCapture,
    ContentSource,
    ContentSourceAdapter,
    ContentTextEvidenceProjection,
)
from powercontext.builtin.sources.external_skill import (
    EXTERNAL_SKILL_SNAPSHOT_SOURCE_ADAPTER,
    EXTERNAL_SKILL_SNAPSHOT_SOURCE_DEFINITION,
    EXTERNAL_SKILL_SNAPSHOT_SOURCE_NAME,
    ExternalSkillImportMode,
    ExternalSkillSnapshotCapture,
    ExternalSkillSnapshotSource,
    ExternalSkillSnapshotSourceAdapter,
)
from powercontext.builtin.sources.journal import (
    SourceCursor,
    SourceJournal,
    SourceJournalEntry,
    validate_scope_id,
)
from powercontext.builtin.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence
from powercontext.builtin.sources.text_file import (
    TEXT_FILE_SNAPSHOT_SOURCE_ADAPTER,
    TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION,
    TEXT_FILE_SNAPSHOT_SOURCE_NAME,
    TextFileEvidenceProjection,
    TextFileSnapshotCapture,
    TextFileSnapshotSource,
    TextFileSnapshotSourceAdapter,
)
from powercontext.sources import SourceDefinitionRegistry

BUILTIN_SOURCE_REGISTRY = SourceDefinitionRegistry((
    CONTENT_SOURCE_DEFINITION,
    EXTERNAL_SKILL_SNAPSHOT_SOURCE_DEFINITION,
    TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION,
))

__all__ = [
    "BUILTIN_SOURCE_REGISTRY",
    "CONTENT_SOURCE_ADAPTER",
    "CONTENT_SOURCE_DEFINITION",
    "CONTENT_SOURCE_NAME",
    "EXTERNAL_SKILL_SNAPSHOT_SOURCE_ADAPTER",
    "EXTERNAL_SKILL_SNAPSHOT_SOURCE_DEFINITION",
    "EXTERNAL_SKILL_SNAPSHOT_SOURCE_NAME",
    "TEXT_EVIDENCE_PROJECTION_KEY",
    "TEXT_FILE_SNAPSHOT_SOURCE_ADAPTER",
    "TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION",
    "TEXT_FILE_SNAPSHOT_SOURCE_NAME",
    "ContentCapture",
    "ContentSource",
    "ContentSourceAdapter",
    "ContentTextEvidenceProjection",
    "ExternalSkillImportMode",
    "ExternalSkillSnapshotCapture",
    "ExternalSkillSnapshotSource",
    "ExternalSkillSnapshotSourceAdapter",
    "SourceCursor",
    "SourceJournal",
    "SourceJournalEntry",
    "TextEvidence",
    "TextFileEvidenceProjection",
    "TextFileSnapshotCapture",
    "TextFileSnapshotSource",
    "TextFileSnapshotSourceAdapter",
    "validate_scope_id",
]
