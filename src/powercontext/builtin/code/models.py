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

"""Ephemeral query contracts and code citations; none are Artifacts."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from powercontext.builtin.code.errors import InvalidCodeRequestError

CODE_QUERY_SCHEMA = "powercontext.code-query.v1"
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$", strict=True)]
Line = Annotated[int, Field(ge=1, strict=True)]
ListLimit = Annotated[int, Field(ge=1, le=50, strict=True)]
Depth = Annotated[int, Field(ge=1, le=5, strict=True)]
QueryText = Annotated[str, Field(min_length=1, max_length=8192, strict=True)]


def relative_path(value: str) -> str:
    """Require a losslessly represented repository-relative path."""

    if (
        not value
        or len(value) > 4096
        or "\x00" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or value.startswith("/")
        or "\\" in value
        or any(0xD800 <= ord(char) <= 0xDFFF for char in value)
    ):
        raise InvalidCodeRequestError("invalid_code_path")
    return value


class CodeValue(BaseModel):
    """Strict immutable values used across code query boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, populate_by_name=True)


class CodeStatusOperation(CodeValue):
    kind: Literal["status"]


class CodeTreeOperation(CodeValue):
    kind: Literal["tree"]
    path: str | None = None
    depth: Depth = 2
    limit: ListLimit = 20

    @field_validator("path")
    @classmethod
    def check_path(cls, value: str | None) -> str | None:
        return None if value is None else relative_path(value)


class CodeSymbolsOperation(CodeValue):
    kind: Literal["symbols"]
    query: QueryText
    path: str | None = None
    limit: ListLimit = 20

    @field_validator("path")
    @classmethod
    def check_path(cls, value: str | None) -> str | None:
        return None if value is None else relative_path(value)

    @field_validator("query")
    @classmethod
    def check_query(cls, value: str) -> str:
        if not value.strip():
            raise InvalidCodeRequestError("empty_code_query")
        return value


class CodeSymbolTarget(CodeValue):
    path: str
    qualified_name: Annotated[str, Field(min_length=1, max_length=4096)]
    start_line: Line
    limit: ListLimit = 20

    @field_validator("path")
    @classmethod
    def check_path(cls, value: str) -> str:
        return relative_path(value)


class CodeTargetOperation(CodeSymbolTarget):
    kind: Literal["callers", "callees"]


class CodeImpactOperation(CodeSymbolTarget):
    kind: Literal["impact"]
    depth: Depth = 2


class CodeAffectedTestsOperation(CodeValue):
    kind: Literal["affected_tests"]
    changed_paths: Annotated[tuple[str, ...], Field(min_length=1, max_length=100, strict=False)]
    limit: ListLimit = 20

    @field_validator("changed_paths")
    @classmethod
    def check_paths(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(relative_path(value) for value in values)


class CodeReadOperation(CodeValue):
    kind: Literal["read"]
    path: str
    file_sha256: Digest
    start_line: Line
    end_line: Line

    @field_validator("path")
    @classmethod
    def check_path(cls, value: str) -> str:
        return relative_path(value)

    @model_validator(mode="after")
    def check_range(self) -> CodeReadOperation:
        if not 0 <= self.end_line - self.start_line < 200:
            raise InvalidCodeRequestError("invalid_code_line_range")
        return self


CodeOperation = Annotated[
    CodeStatusOperation
    | CodeTreeOperation
    | CodeSymbolsOperation
    | CodeTargetOperation
    | CodeImpactOperation
    | CodeAffectedTestsOperation
    | CodeReadOperation,
    Field(discriminator="kind"),
]


class CodeQueryRequest(CodeValue):
    operation: CodeOperation
    expected_fingerprint: Digest | None = None
    max_bytes: Annotated[int, Field(ge=512, le=32768)] = 16000

    @model_validator(mode="after")
    def require_continuation(self) -> CodeQueryRequest:
        if self.operation.kind not in {"status", "tree", "symbols"} and self.expected_fingerprint is None:
            raise InvalidCodeRequestError("code_fingerprint_required")
        return self


class CodeLocation(CodeValue):
    path: str
    qualified_name: str | None = None
    start_line: Line
    end_line: Line

    @field_validator("path")
    @classmethod
    def check_path(cls, value: str) -> str:
        return relative_path(value)


class CodeRelationship(CodeValue):
    kind: Literal["calls", "imports", "references", "inherits", "unknown"]
    method: Literal["tree-sitter", "scip", "heuristic", "unknown"] = "unknown"
    source: CodeLocation | None = None
    target: CodeLocation | None = None
    call_line: Line | None = None
    missing: str | None = None


class CodeItem(CodeValue):
    kind: Literal["definition", "relationship", "test", "file", "directory", "snippet"]
    path: str
    location: CodeLocation | None = None
    file_sha256: Digest | None = None
    content: str | None = None
    content_sha256: Digest | None = None
    signature: str | None = None
    relationships: tuple[CodeRelationship, ...] = ()
    truncated: bool = False


class CodeCoverage(CodeValue):
    included_files: int = Field(default=0, ge=0)
    indexed_files: int = Field(default=0, ge=0)
    omitted_files: int = Field(default=0, ge=0)
    parse_failures: int = Field(default=0, ge=0)
    unresolved_references: int = Field(default=0, ge=0)
    truncated: bool = False


class CodeQueryResponse(CodeValue):
    schema_version: Literal["powercontext.code-query.v1"] = Field(default=CODE_QUERY_SCHEMA, alias="schema")
    scope_id: str
    fingerprint: Digest
    commit: str
    git_object_format: Literal["sha1", "sha256"]
    dirty: bool
    checked_at: str
    operation: str
    status: Literal["ok", "partial"]
    items: tuple[CodeItem, ...] = ()
    coverage: CodeCoverage
    limitations: tuple[str, ...] = ()


class CodeStatusResponse(CodeValue):
    schema_version: Literal["powercontext.code-status.v1"] = Field(
        default="powercontext.code-status.v1", alias="schema"
    )
    scope_id: str
    status: Literal["disabled", "missing", "building", "ready", "stale", "failed"]
    fingerprint: Digest | None = None
    protocol: Literal["powercontext.code-query.v1"] = CODE_QUERY_SCHEMA
    languages: tuple[str, ...] = ("python",)
    capabilities: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
