"""Errors owned by the built-in Memory Artifact Family."""

from powercontext.errors import PowerContextError


class MemoryLayerError(PowerContextError):
    """Base exception for Memory domain and repository failures."""


class CapabilityNotSupportedError(MemoryLayerError, RuntimeError):
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
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        super().__init__(f"memory entry was not found: {entry_id}")


class MemoryEntryInactiveError(MemoryEntryError, RuntimeError):
    def __init__(self, entry_id: str) -> None:
        self.entry_id = entry_id
        super().__init__(f"memory entry is inactive: {entry_id}")


class InvalidMemoryCandidateError(MemoryLayerError, ValueError):
    def __init__(self, code: str, detail: object | None = None) -> None:
        self.code = code
        self.detail = detail
        messages = {
            "remember-mode": f"unsupported memory remember mode: {detail}",
            "identity-kind": f"unsupported memory identity kind: {detail}",
            "canonical": f"invalid canonical memory entry: {detail}",
        }
        super().__init__(messages.get(code, f"invalid memory candidate: {code}"))


class InvalidMemoryEvidenceError(MemoryLayerError, ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        messages = {
            "source-resolver": "Source evidence requires a canonical Source resolver",
            "artifact-resolver": "Artifact evidence requires a canonical Artifact resolver",
            "source-outside": "entry Source evidence is outside the allowed evidence set",
            "artifact-outside": "entry Artifact evidence is outside the allowed evidence set",
            "projection": "Memory evidence projection must be JSON-compatible",
            "source-adapter": "Source evidence requires a Source adapter",
        }
        super().__init__(messages.get(code, f"invalid memory evidence: {code}"))


class InvalidEmbeddingError(MemoryLayerError, ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        messages = {"count": "embedding model must return one vector per text"}
        super().__init__(messages.get(code, f"invalid embedding: {code}"))


class InvalidMemoryCitationError(MemoryLayerError, ValueError):
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
    """Raised when a repository cannot satisfy its declared configuration."""
