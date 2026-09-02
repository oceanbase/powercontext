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

"""Stable failures raised by PowerContext."""

from __future__ import annotations


class PowerContextError(Exception):
    """Base exception for stable PowerContext failures."""


class SourceError(PowerContextError):
    """Base exception for Source adapter and access failures."""


class SourceNotFoundError(SourceError, LookupError):
    """Raised when a Source object is absent from a catalog."""

    def __init__(self, source: object) -> None:
        self.source = source
        super().__init__("source was not found")


class SourceAdapterNotFoundError(SourceError, LookupError):
    """Raised when no adapter owns an exact input or Source class."""

    def __init__(self, route: str, requested_type: type[object]) -> None:
        self.route = route
        self.requested_type = requested_type
        super().__init__(f"no Source adapter is registered for {route} type {_type_name(requested_type)}")


class InvalidSourceAdapterError(SourceError, TypeError):
    """Raised when an adapter does not satisfy the structural Source contract."""

    def __init__(self, adapter_type: type[object], field: str, detail: str) -> None:
        self.adapter_type = adapter_type
        self.field = field
        self.detail = detail
        super().__init__(f"invalid Source adapter {_type_name(adapter_type)} {field}: {detail}")


class SourceConflictError(SourceError, ValueError):
    """Raised when immutable catalog routing would be ambiguous."""

    def __init__(self, field: str, value: object) -> None:
        self.field = field
        self.value = value
        rendered = _type_name(value) if isinstance(value, type) else repr(value)
        super().__init__(f"duplicate Source {field}: {rendered}")


class InvalidSourceReferenceError(SourceError, ValueError):
    """Raised when a Source cannot produce a reference required by its contract."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid Source reference {field}: {detail}")


class InvalidSourceEntryError(SourceError, TypeError):
    """Raised when a catalog entry is not a Source value."""

    def __init__(self, actual_type: type[object]) -> None:
        self.actual_type = actual_type
        super().__init__(f"catalog entries must be Source values, got {_type_name(actual_type)}")


class InvalidSourceResultError(SourceError, TypeError):
    """Raised when an adapter returns a Source outside its declaration."""

    def __init__(
        self,
        adapter_name: str,
        operation: str,
        expected_type: type[object],
        actual_type: type[object],
    ) -> None:
        self.adapter_name = adapter_name
        self.operation = operation
        self.expected_type = expected_type
        self.actual_type = actual_type
        super().__init__(
            f"Source adapter {adapter_name!r} returned {_type_name(actual_type)} from {operation}, "
            f"expected {_type_name(expected_type)}"
        )


class InvalidSourceDefinitionError(SourceError, TypeError):
    """Raised when a Source Definition violates its registration contract."""

    def __init__(self, definition_type: type[object], field: str, detail: str) -> None:
        self.definition_type = definition_type
        self.field = field
        self.detail = detail
        super().__init__(f"invalid Source Definition {_type_name(definition_type)} {field}: {detail}")


class SourceDefinitionNotFoundError(SourceError, LookupError):
    """Raised when the active registry does not contain a Source Definition."""

    def __init__(self, name: str, version: str | None = None) -> None:
        self.name = name
        self.version = version
        suffix = "" if version is None else f" version {version!r}"
        super().__init__(f"Source Definition {name!r}{suffix} is not registered")


class SourceProjectionNotFoundError(SourceError, LookupError):
    """Raised when a Source Definition does not provide a requested projection."""

    def __init__(self, source_type: str, projection_name: str, projection_version: str) -> None:
        self.source_type = source_type
        self.projection_name = projection_name
        self.projection_version = projection_version
        super().__init__(
            f"Source Definition {source_type!r} does not provide projection "
            f"{projection_name!r} version {projection_version!r}"
        )


class InvalidSourceProjectionError(SourceError, TypeError):
    """Raised when a named Source projection violates its declared contract."""

    def __init__(self, projection_name: str, field: str, detail: str) -> None:
        self.projection_name = projection_name
        self.field = field
        self.detail = detail
        super().__init__(f"invalid Source projection {projection_name!r} {field}: {detail}")


class InvalidSourceObservationError(SourceError, ValueError):
    """Raised when a worker-projected observation violates its registered manifest."""

    def __init__(self, issue: str, detail: str) -> None:
        self.issue = issue
        self.detail = detail
        super().__init__(f"invalid Source observation {issue}: {detail}")


class ConnectorError(PowerContextError):
    """Base exception for Connector contracts and run lifecycle failures."""


class InvalidConnectorError(ConnectorError, TypeError):
    """Raised when a Connector or binding violates its declared contract."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid Connector {field}: {detail}")


class InvalidConnectorRunError(ConnectorError, RuntimeError):
    """Raised when a Connector run would violate replay or checkpoint safety."""

    def __init__(self, issue: str, detail: str) -> None:
        self.issue = issue
        self.detail = detail
        super().__init__(f"invalid Connector run {issue}: {detail}")


class ConnectorSubmissionRejectedError(ConnectorError, ValueError):
    """Raised when one materialized item cannot satisfy the submission contract."""

    def __init__(self, detail: str) -> None:
        if not detail or detail.strip() != detail:
            raise ValueError("Connector submission rejection detail must be non-empty and trimmed")  # noqa: TRY003
        self.detail = detail
        super().__init__(f"Connector submission rejected: {detail}")


class ArtifactError(PowerContextError):
    """Base exception for Artifact lookup and lifecycle failures."""


class ArtifactNotFoundError(ArtifactError, LookupError):
    """Raised when an Artifact object is absent from a catalog."""

    def __init__(self, artifact: object) -> None:
        self.artifact = artifact
        super().__init__("artifact was not found")


class InvalidArtifactReferenceError(ArtifactError, ValueError):
    """Raised when an Artifact reference has an invalid identity or revision."""

    def __init__(self, field: str, detail: str) -> None:
        self.field = field
        self.detail = detail
        super().__init__(f"invalid Artifact reference {field}: {detail}")


class ArtifactFamilyMismatchError(ArtifactError, ValueError):
    """Raised when a Revision and Draft belong to different families."""

    def __init__(self, artifact: object, draft: object) -> None:
        self.artifact = artifact
        self.draft = draft
        super().__init__("artifact and draft families do not match")


class RevisionConflictError(ArtifactError, RuntimeError):
    """Raised when an Artifact write is based on a stale object."""

    def __init__(self, artifact: object, current: object) -> None:
        self.artifact = artifact
        self.current = current
        super().__init__("artifact is not the latest revision")


def _type_name(value: type[object]) -> str:
    return f"{value.__module__}.{value.__qualname__}"
