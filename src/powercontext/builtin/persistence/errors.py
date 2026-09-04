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

"""Persistence configuration and lifecycle failures."""

from __future__ import annotations


class PersistenceError(RuntimeError):
    """Base class for relational persistence failures."""


class DatabaseClosedError(PersistenceError):
    """Raised when an operation uses a closed database owner."""

    def __init__(self) -> None:
        super().__init__("database owner is closed")


class RepositoryError(PersistenceError):
    """Base class for relational repository failures."""


class InvalidStoredPayloadError(RepositoryError):
    """Raised when a canonical payload does not match its persisted model."""

    def __init__(self, kind: str, name: str, issue: str) -> None:
        self.kind = kind
        self.name = name
        self.issue = issue
        super().__init__(f"invalid stored {kind} payload for {name!r}: {issue}")


class IdentityMismatchError(RepositoryError):
    """Raised when indexed identity columns disagree with the decoded value."""

    def __init__(self, kind: str, indexed: object, decoded: object) -> None:
        self.kind = kind
        self.indexed = indexed
        self.decoded = decoded
        super().__init__(f"stored {kind} identity mismatch: indexed={indexed!r}, decoded={decoded!r}")


class StoredPayloadConflictError(RepositoryError):
    """Raised when an idempotent identity is reused with a different payload."""

    def __init__(self, kind: str, identity: object) -> None:
        self.kind = kind
        self.identity = identity
        super().__init__(f"{kind} identity {identity!r} already stores a different payload")


class RepositoryNotFoundError(RepositoryError, LookupError):
    """Raised when an expected persisted object does not exist."""

    def __init__(self, kind: str, identity: object) -> None:
        self.kind = kind
        self.identity = identity
        super().__init__(f"{kind} {identity!r} was not found")


class InvalidRepositoryArgumentError(RepositoryError, ValueError):
    """Raised when a repository operation receives an invalid control value."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid repository argument {field}: {detail}")


class InvalidStoredColumnError(RepositoryError, TypeError):
    """Raised when a database driver returns a column outside its declared type."""

    def __init__(self, column: str, expected: str) -> None:
        self.column = column
        self.expected = expected
        super().__init__(f"stored {column} column is not {expected}")


class InvalidPublicationLineageError(RepositoryError):
    """Raised when persisted publication provenance cannot form one exact chain."""

    def __init__(self, issue: str) -> None:
        self.issue = issue
        detail = "inconsistent content digests" if issue == "digest" else issue
        super().__init__(f"invalid publication lineage: {detail}")


class GenerationConflictError(RepositoryError):
    """Raised when Trigger State compare-and-swap observes another generation."""

    def __init__(self, binding_name: str, expected: int | None, actual: int | None) -> None:
        self.binding_name = binding_name
        self.expected = expected
        self.actual = actual
        super().__init__(f"trigger state {binding_name!r} generation conflict: expected {expected!r}, found {actual!r}")
