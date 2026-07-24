"""Local Source-to-Memory runtime assembly."""

from powercontext.runtime.application import (
    SOURCE_WINDOW_TRIGGER,
    MemoryApplication,
    MemoryArtifacts,
    MemoryTriggers,
    PowerContextRuntime,
    ScheduledSourceProcessor,
    ScopedMemoryApplication,
    ScopedSourceApplication,
    SourceApplication,
)
from powercontext.runtime.errors import InvalidRuntimeRequestError
from powercontext.runtime.models import (
    GetMemoryEntryRequest,
    MemoryChangesPage,
    MemoryEntriesPage,
    MemoryEntryRecord,
    MemoryFlushResult,
    MemoryMutationResult,
    MemorySearchPage,
    ProcessSourceWindow,
    RememberMemoryRequest,
    RetireMemoryEntryRequest,
    ReviseMemoryEntryRequest,
    RuntimeCapabilities,
    SearchMemoryRequest,
    SourceHighWatermark,
    SourceReceipt,
)
from powercontext.runtime.protocols import (
    MemoryBindingStore,
    RuntimeScopeStorage,
    RuntimeStorage,
    ScopedSourceBackend,
)
from powercontext.runtime.triggers import SourceWindowTrigger
from powercontext.sources.journal import SourceCursor

__all__ = [
    "SOURCE_WINDOW_TRIGGER",
    "GetMemoryEntryRequest",
    "InvalidRuntimeRequestError",
    "MemoryApplication",
    "MemoryArtifacts",
    "MemoryBindingStore",
    "MemoryChangesPage",
    "MemoryEntriesPage",
    "MemoryEntryRecord",
    "MemoryFlushResult",
    "MemoryMutationResult",
    "MemorySearchPage",
    "MemoryTriggers",
    "PowerContextRuntime",
    "ProcessSourceWindow",
    "RememberMemoryRequest",
    "RetireMemoryEntryRequest",
    "ReviseMemoryEntryRequest",
    "RuntimeCapabilities",
    "RuntimeScopeStorage",
    "RuntimeStorage",
    "ScheduledSourceProcessor",
    "ScopedMemoryApplication",
    "ScopedSourceApplication",
    "ScopedSourceBackend",
    "SearchMemoryRequest",
    "SourceApplication",
    "SourceCursor",
    "SourceHighWatermark",
    "SourceReceipt",
    "SourceWindowTrigger",
]
