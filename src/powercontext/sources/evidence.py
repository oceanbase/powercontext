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

"""Typed declarations and exact material facts accepted at Source registration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from powercontext.sources.models import SourceMaterialization, SourceRef


class MemoryEvidenceDeclaration(BaseModel):
    """A trusted adapter's claim, not a score, permission or verification engine.

    ``declaration_version`` identifies this contract, independently of the
    Definition version and Artifact content revision. Missing or unsupported
    declarations resolve to ``untrusted`` / ``unknown``.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    authority: Literal["untrusted", "user_asserted", "repository_attested", "system_attested"] = "untrusted"
    verification: Literal["unknown", "verified", "not_verified"] = "unknown"
    declaration_version: Literal["1"] = "1"


class SourceEvidence(BaseModel):
    """Retain one exact Source material binding without duplicating its body.

    The digest checks equality of canonical Source JSON, excluding only the
    server-owned ContentSource handoff receipt witness. It authenticates neither
    the Source nor its declaration. Each Source retains its own declaration.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_ref: SourceRef
    materialization: SourceMaterialization
    material_digest: str
    definition_name: str
    definition_version: str
    manifest_fingerprint: str | None = None
    declaration: MemoryEvidenceDeclaration = Field(default_factory=MemoryEvidenceDeclaration)


__all__ = ["MemoryEvidenceDeclaration", "SourceEvidence"]
