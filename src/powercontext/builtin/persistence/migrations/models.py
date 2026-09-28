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

"""Reviewable migration plans and payload-free operator errors."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


def digest(value: Any) -> str:
    """Hash a canonical JSON value without including it in diagnostics."""
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


class MigrationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


class MigrationPlan(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    plan_id: str
    database_id: str
    source_revision: str | None
    target_revision: str
    schema_fingerprint: str
    bundle_checksum: str
    configuration_digest: str
    state: Literal["uninitialized", "migration_required", "ready", "recovery_required"]
    revisions: tuple[str, ...]
    adopt_baseline: bool = False
    resume_run_id: str | None = None


class MigrationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    state: Literal["ready"] = "ready"
    revision: str
    changed: bool
    run_id: str | None = None
    backup_ref: str | None = None
