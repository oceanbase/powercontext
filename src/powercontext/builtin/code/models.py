# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Strict, transport-independent native code query and deployment configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StringConstraints, field_validator, model_validator

from powercontext._code_validation import relative_path, validate_code_operation, validate_code_query
from powercontext.builtin.code.languages import SUPPORTED_LANGUAGES

Fingerprint = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class CodeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)


class CodeLimits(CodeModel):
    max_files: int = Field(default=20_000, ge=1, strict=True)
    max_file_bytes: int = Field(default=2 * 1024 * 1024, ge=1, strict=True)
    max_source_bytes: int = Field(default=512 * 1024 * 1024, ge=1, strict=True)
    max_cache_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=1, strict=True)
    worker_memory_bytes: int = Field(default=1024 * 1024 * 1024, ge=64 * 1024 * 1024, strict=True)
    build_seconds: float = Field(default=600, gt=0)
    parse_seconds: float = Field(default=5, gt=0)
    query_seconds: float = Field(default=5, gt=0)


class CodeRepositoryConfig(CodeModel):
    root: Path
    source_roots: tuple[str, ...] = ("src", ".")
    include_untracked: bool = Field(default=False, strict=True)
    exclude: tuple[str, ...] = ()

    @field_validator("root")
    @classmethod
    def absolute_root(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("repository root must be absolute")  # noqa: TRY003
        return value

    @field_validator("source_roots")
    @classmethod
    def validate_source_roots(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(set(value)) != len(value):
            raise ValueError("source roots must be nonempty and unique")  # noqa: TRY003
        for item in value:
            if item != ".":
                relative_path(item)
        return value


class CodeConfig(CodeModel):
    enabled: bool = Field(default=False, strict=True)
    repositories: dict[str, CodeRepositoryConfig] = Field(default_factory=dict)
    cache_dir: Path = Field(default_factory=lambda: Path.home() / ".cache" / "powercontext" / "code")
    limits: CodeLimits = Field(default_factory=CodeLimits)

    @field_validator("cache_dir")
    @classmethod
    def absolute_cache(cls, value: Path) -> Path:
        if not value.is_absolute():
            raise ValueError("code cache directory must be absolute")  # noqa: TRY003
        return value


class StatusOperation(CodeModel):
    kind: Literal["status"]


class ChangesOperation(CodeModel):
    kind: Literal["changes"]


class PathOperation(CodeModel):
    path_prefix: str = ""
    limit: int = Field(default=20, ge=1, le=50, strict=True)

    @model_validator(mode="after")
    def validate_operation(self) -> Self:
        validate_code_operation(self.model_dump())
        return self


class MapOperation(PathOperation):
    kind: Literal["map"]
    depth: int = Field(default=2, ge=1, le=5, strict=True)


class SearchOperation(PathOperation):
    kind: Literal["symbols", "explore"]
    query: str = Field(min_length=1, max_length=8192, strict=True)


class RelationOperation(PathOperation):
    kind: Literal["callers", "callees", "impact"]
    symbol_id: str = Field(min_length=1, max_length=128, strict=True)
    depth: int = Field(default=2, ge=1, le=5, strict=True)


class TestsOperation(PathOperation):
    kind: Literal["affected_tests", "impact_changes"]
    paths: tuple[str, ...] = Field(min_length=1, max_length=100)


class ReadOperation(CodeModel):
    kind: Literal["read"]
    path: str
    file_sha256: Fingerprint
    start_line: int = Field(ge=1, strict=True)
    end_line: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def validate_operation(self) -> Self:
        validate_code_operation(self.model_dump())
        return self


CodeOperation = Annotated[
    StatusOperation
    | ChangesOperation
    | MapOperation
    | SearchOperation
    | RelationOperation
    | TestsOperation
    | ReadOperation,
    Field(discriminator="kind"),
]


class CodeQueryRequest(CodeModel):
    operation: CodeOperation
    expected_fingerprint: Fingerprint | None = None
    before_fingerprint: Fingerprint | None = None
    max_bytes: int = Field(default=16_000, ge=512, le=32_768, strict=True)

    @model_validator(mode="after")
    def require_identity(self) -> Self:
        validate_code_query(self.operation.kind, self.expected_fingerprint, self.before_fingerprint)
        return self


class CodeQueryResult(CodeModel):
    schema_version: Literal["powercontext.code-query.v1"] = Field(default="powercontext.code-query.v1", alias="schema")
    scope_id: str
    fingerprint: Fingerprint
    before_fingerprint: Fingerprint | None = None
    commit: str | None
    git_object_format: Literal["sha1", "sha256"]
    dirty: bool
    checked_at: str
    operation: str
    status: Literal["ok", "partial"] = "ok"
    items: list[dict[str, JsonValue]] = Field(default_factory=list)
    coverage: dict[str, JsonValue] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)


class CodeStatus(CodeModel):
    schema_version: Literal["powercontext.code-status.v1"] = Field(
        default="powercontext.code-status.v1", alias="schema"
    )
    scope_id: str
    status: Literal["disabled", "missing", "building", "ready", "stale", "failed"]
    freshness: Literal["fresh", "stale", "unknown"] = "unknown"
    fingerprint: Fingerprint | None = None
    engine: str = "powercontext-native-v1"
    languages: tuple[str, ...] = SUPPORTED_LANGUAGES
    operations: tuple[str, ...] = (
        "status",
        "map",
        "symbols",
        "explore",
        "callers",
        "callees",
        "impact",
        "affected_tests",
        "read",
        "changes",
        "impact_changes",
    )
    last_build: dict[str, JsonValue] | None = None
    reason: str | None = None
