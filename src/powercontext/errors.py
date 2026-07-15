from __future__ import annotations


class PowerContextError(Exception):
    """Base exception for stable PowerContext failures."""


class SourceError(PowerContextError):
    """Base exception for Source definition and access failures."""


class SourceDefinitionError(SourceError, ValueError):
    """Raised when a Source value violates the public model."""

    def __init__(self, field: str, value: object, detail: str) -> None:
        self.field = field
        self.value = value
        self.detail = detail
        super().__init__(f"invalid Source {field}: {detail}")


class SourceNotFoundError(SourceError, LookupError):
    """Raised when a Source object is absent from a catalog."""

    def __init__(self, source: object) -> None:
        self.source = source
        super().__init__("source was not found")


class SourceAdapterNotFoundError(SourceError, LookupError):
    """Raised when no adapter owns an exact input or Source type."""

    def __init__(self, route: str, requested_type: type[object]) -> None:
        self.route = route
        self.requested_type = requested_type
        super().__init__(f"no Source adapter is registered for {route} type {_type_name(requested_type)}")


class SourceConflictError(SourceError, ValueError):
    """Raised when immutable catalog routing would be ambiguous."""

    def __init__(self, field: str, value: object) -> None:
        self.field = field
        self.value = value
        rendered = _type_name(value) if isinstance(value, type) else repr(value)
        super().__init__(f"duplicate Source {field}: {rendered}")


class InvalidSourceEntryError(SourceError, TypeError):
    """Raised when a catalog entry is not a Source value."""

    def __init__(self, actual_type: type[object]) -> None:
        self.actual_type = actual_type
        super().__init__(f"catalog entries must be Source values, got {_type_name(actual_type)}")


class InvalidSourceAdapterError(SourceError, TypeError):
    """Raised when an adapter does not satisfy the declared boundary."""

    def __init__(self, adapter_type: type[object], field: str, detail: str) -> None:
        self.adapter_type = adapter_type
        self.field = field
        self.detail = detail
        super().__init__(f"invalid Source adapter {_type_name(adapter_type)} {field}: {detail}")


class InvalidSourceResultError(SourceError, TypeError):
    """Raised when an adapter returns a Source outside its declaration."""

    def __init__(
        self,
        source_type: str,
        operation: str,
        expected_type: type[object],
        actual_type: type[object],
    ) -> None:
        self.source_type = source_type
        self.operation = operation
        self.expected_type = expected_type
        self.actual_type = actual_type
        super().__init__(
            f"Source adapter {source_type!r} returned {_type_name(actual_type)} from {operation}, "
            f"expected {_type_name(expected_type)}"
        )


class SourceDiscoveryError(SourceError, RuntimeError):
    """Raised when a Source adapter entry point cannot be loaded."""

    def __init__(self, entry_point: str, detail: str) -> None:
        self.entry_point = entry_point
        self.detail = detail
        super().__init__(f"could not load Source adapter entry point {entry_point!r}: {detail}")


class ArtifactError(PowerContextError):
    """Base exception for Artifact lookup and lifecycle failures."""


class ArtifactNotFoundError(ArtifactError, LookupError):
    """Raised when an Artifact object is absent from a catalog."""

    def __init__(self, artifact: object) -> None:
        self.artifact = artifact
        super().__init__("artifact was not found")


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
