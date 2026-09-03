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

"""Search projection owned by the managed Skill Artifact Family."""

from __future__ import annotations

from pydantic import BaseModel

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.search import analyze_text
from powercontext.builtin.artifacts.skill.models import SkillContent
from powercontext.builtin.artifacts.skill.package import SkillPackageSnapshot, package_file

_MAX_INDEXED_FILE_BYTES = 128 * 1024


class SkillSearchHit(BaseModel):
    """One relevant approved, active managed Skill head."""

    artifact_ref: ArtifactRef
    content: SkillContent


def skill_search_text(content: SkillContent, package: SkillPackageSnapshot | None = None, /) -> str:
    """Return deterministic user-authored metadata and bounded textual package content."""

    values = [
        content.name,
        content.description,
        content.instructions,
        *(content.validation),
        *(f"{key} {value}" for key, value in sorted(content.metadata.items())),
    ]
    if content.license is not None:
        values.append(content.license)
    if content.compatibility is not None:
        values.append(content.compatibility)
    if content.allowed_tools is not None:
        values.append(content.allowed_tools)
    if package is not None:
        for entry in package.entries:
            values.append(entry.path)
            if entry.size > _MAX_INDEXED_FILE_BYTES or not _is_indexed_text(entry.path):
                continue
            try:
                values.append(package_file(package, entry.path).decode("utf-8"))
            except UnicodeDecodeError:
                continue
    return "\n".join(values)


def skill_searchable_text(content: SkillContent, package: SkillPackageSnapshot | None = None, /) -> str:
    """Build the normalized lexical projection for one managed Skill Revision."""

    return analyze_text(skill_search_text(content, package))


def _is_indexed_text(path: str) -> bool:
    return path == "SKILL.md" or (path.startswith("references/") and path.endswith((".md", ".txt")))


__all__ = ["SkillSearchHit", "skill_search_text", "skill_searchable_text"]
