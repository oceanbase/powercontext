"""Built-in runtime configuration, lifecycle, and application services."""

from powercontext.builtin.artifacts.memory.models import MemoryChange
from powercontext.builtin.components import MemoryFlushResult
from powercontext.builtin.runtime.application import (
    BuiltinRuntime,
    MemoryApplication,
    ScheduledSourceProcessor,
    ScopedMemoryApplication,
    ScopedSourceApplication,
    SourceApplication,
)
from powercontext.builtin.runtime.config import (
    BuiltinConfig,
    DatabaseConfig,
    InferenceConfig,
    RuntimeConfig,
)
from powercontext.builtin.runtime.errors import InvalidRuntimeRequestError
from powercontext.builtin.runtime.instance import (
    BuiltinConfigurationError,
    open_builtin_contexts,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.models import (
    CaptureSource,
    GetMemoryEntryRequest,
    MemoryChangesPage,
    MemoryCitation,
    MemoryEntriesPage,
    MemoryEntryInput,
    MemoryEntryRecord,
    MemoryEntryVersion,
    MemoryHit,
    MemoryMutationResult,
    MemoryRevisionChanges,
    MemorySearchPage,
    RememberMemoryRequest,
    RetireMemoryEntryRequest,
    ReviseMemoryEntryRequest,
    RuntimeCapabilities,
    SearchMemoryRequest,
    SourceReceipt,
)
from powercontext.builtin.runtime.protocols import PowerContextProvider

__all__ = [
    "BuiltinConfig",
    "BuiltinConfigurationError",
    "BuiltinRuntime",
    "CaptureSource",
    "DatabaseConfig",
    "GetMemoryEntryRequest",
    "InferenceConfig",
    "InvalidRuntimeRequestError",
    "MemoryApplication",
    "MemoryChange",
    "MemoryChangesPage",
    "MemoryCitation",
    "MemoryEntriesPage",
    "MemoryEntryInput",
    "MemoryEntryRecord",
    "MemoryEntryVersion",
    "MemoryFlushResult",
    "MemoryHit",
    "MemoryMutationResult",
    "MemoryRevisionChanges",
    "MemorySearchPage",
    "PowerContextProvider",
    "RememberMemoryRequest",
    "RetireMemoryEntryRequest",
    "ReviseMemoryEntryRequest",
    "RuntimeCapabilities",
    "RuntimeConfig",
    "ScheduledSourceProcessor",
    "ScopedMemoryApplication",
    "ScopedSourceApplication",
    "SearchMemoryRequest",
    "SourceApplication",
    "SourceReceipt",
    "open_builtin_contexts",
    "open_builtin_runtime",
]
