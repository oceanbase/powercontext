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

"""Bounded Source evidence for caller-uploaded standard Skill packages."""

from __future__ import annotations

from pydantic import BaseModel

from powercontext.builtin.artifacts.skill.models import SkillPackageRef
from powercontext.sources import AdapterSourceDefinition, Source, SourceMaterialization

SKILL_PACKAGE_UPLOAD_SOURCE_NAME = "skill-package-upload"


class SkillPackageUploadCapture(BaseModel):
    """Exact package identity selected by an explicit caller upload."""

    package: SkillPackageRef
    name: str
    description: str


class SkillPackageUploadSource(Source):
    """Durable upload evidence that refers to package bytes stored once."""

    package: SkillPackageRef
    skill_name: str
    skill_description: str


class SkillPackageUploadSourceAdapter:
    """Materialize package upload metadata without duplicating archive bytes."""

    input_class = SkillPackageUploadCapture
    name = SKILL_PACKAGE_UPLOAD_SOURCE_NAME
    source_class = SkillPackageUploadSource

    async def resolve(self, value: SkillPackageUploadCapture, /) -> SkillPackageUploadSource:
        return SkillPackageUploadSource(
            name=f"skill_pkg_{value.package.tree_digest}",
            materialization=SourceMaterialization.CAPTURED,
            description="Exact standard Skill package captured by an explicit caller upload.",
            package=value.package,
            skill_name=value.name,
            skill_description=value.description,
        )

    async def read(self, source: SkillPackageUploadSource, /) -> SkillPackageUploadCapture:
        return SkillPackageUploadCapture(
            package=source.package,
            name=source.skill_name,
            description=source.skill_description,
        )


SKILL_PACKAGE_UPLOAD_SOURCE_ADAPTER = SkillPackageUploadSourceAdapter()
SKILL_PACKAGE_UPLOAD_SOURCE_DEFINITION = AdapterSourceDefinition(SKILL_PACKAGE_UPLOAD_SOURCE_ADAPTER)

__all__ = [
    "SKILL_PACKAGE_UPLOAD_SOURCE_ADAPTER",
    "SKILL_PACKAGE_UPLOAD_SOURCE_DEFINITION",
    "SKILL_PACKAGE_UPLOAD_SOURCE_NAME",
    "SkillPackageUploadCapture",
    "SkillPackageUploadSource",
    "SkillPackageUploadSourceAdapter",
]
