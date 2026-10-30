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

"""Standard named projection schemas understood without Source plugin code."""

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from powercontext.sources.models import SourceLocator, SourceProjectionKey

TEXT_EVIDENCE_PROJECTION_KEY = SourceProjectionKey(name="powercontext.text-evidence", version="1")


class TextEvidenceV1(BaseModel):
    """Canonical JSON shape consumed as textual Artifact evidence."""

    # Definitions already registered with this shape keep their immutable
    # manifest identity when consumers gain support for richer text evidence.
    model_config = ConfigDict(title="TextEvidence")

    source_type: str
    source_id: str
    content: str
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class TextEvidence(TextEvidenceV1):
    """Canonical JSON shape consumed as textual Artifact evidence."""

    locator: SourceLocator | None = Field(default=None, exclude_if=lambda value: value is None)


__all__ = ["TEXT_EVIDENCE_PROJECTION_KEY", "TextEvidence", "TextEvidenceV1"]
