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


class MemoryLayerError(PowerContextError):
    """Base exception for Memory domain and backend failures."""


class CapabilityNotSupportedError(MemoryLayerError, RuntimeError):
    """Raised when an explicitly requested Memory capability is unavailable."""

    def __init__(self, capability: str, detail: str | None = None) -> None:
        self.capability = capability
        self.detail = detail
        message = f"memory capability is not supported: {capability}"
        if detail is not None:
            message = f"{message} ({detail})"
        super().__init__(message)


class MemoryEntryError(MemoryLayerError):
    """Base exception for logical Memory entry failures."""


class MemoryEntryNotFoundError(MemoryEntryError, LookupError):
    """Raised when a logical entry is absent from the selected Memory."""

    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        super().__init__(f"memory entry was not found: {entry_id}")


class MemoryEntryInactiveError(MemoryEntryError, RuntimeError):
    """Raised when a content revision targets an inactive entry."""

    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        super().__init__(f"memory entry is inactive: {entry_id}")


class InvalidMemoryCandidateError(MemoryLayerError, ValueError):
    """Raised when untrusted candidate content violates Memory invariants."""

    def __init__(self, code: str, detail: object | None = None) -> None:
        self.code = code
        self.detail = detail
        messages = {
            "remember-mode": f"unsupported memory remember mode: {detail}",
            "identity-kind": f"unsupported memory identity kind: {detail}",
        }
        super().__init__(messages.get(code, f"invalid memory candidate: {code}"))


class InvalidMemoryEvidenceError(MemoryLayerError, ValueError):
    """Raised when entry evidence is not allowed by the current operation."""

    def __init__(self, code: str) -> None:
        self.code = code
        messages = {
            "source-resolver": "Source evidence requires a canonical Source resolver",
            "artifact-resolver": "Artifact evidence requires a canonical Artifact resolver",
            "source-outside": "entry Source evidence is outside the allowed evidence set",
            "artifact-outside": "entry Artifact evidence is outside the allowed evidence set",
            "projection": "Memory evidence projection must be JSON-compatible",
            "source-codec": "Source evidence requires a MemoryEvidenceCodec",
            "artifact-codec": "Artifact evidence requires a MemoryEvidenceCodec",
            "source-changed": "Source evidence changed while the Memory operation was in progress",
            "artifact-changed": "Artifact evidence changed while the Memory operation was in progress",
        }
        super().__init__(messages.get(code, f"invalid memory evidence: {code}"))


class InvalidEmbeddingError(MemoryLayerError, ValueError):
    """Raised when an embedding does not match the deployment profile."""

    def __init__(self, code: str) -> None:
        self.code = code
        messages = {
            "count": "embedding model must return one vector per text",
        }
        super().__init__(messages.get(code, f"invalid embedding: {code}"))


class InvalidMemoryCitationError(MemoryLayerError, ValueError):
    """Raised when a citation does not identify exact authoritative content."""

    def __init__(self, code: str) -> None:
        self.code = code
        messages = {
            "base-mismatch": "memory value does not match its exact stored Revision",
            "duplicate-versions": "backend returned duplicate entry version identities",
            "missing-version": "manifest entry version is missing",
            "cross-identity": "entry version crosses Memory or logical entry identity",
            "hash-mismatch": "manifest content hash does not match canonical entry bytes",
            "expand-count": "invalid memory citation expansion count",
            "expand-anchor": "invalid memory citation anchor",
        }
        super().__init__(messages.get(code, f"invalid memory citation: {code}"))


class MemoryBackendConfigurationError(MemoryLayerError, RuntimeError):
    """Raised when a backend cannot satisfy its declared configuration."""


def _type_name(value: type[object]) -> str:
    return f"{value.__module__}.{value.__qualname__}"
