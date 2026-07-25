"""Built-in Source components."""

from powercontext.builtin.sources.content import (
    CONTENT_SOURCE_ADAPTER,
    CONTENT_SOURCE_NAME,
    ContentCapture,
    ContentSource,
    ContentSourceAdapter,
)
from powercontext.builtin.sources.journal import (
    SourceCursor,
    SourceJournal,
    validate_scope_id,
)

__all__ = [
    "CONTENT_SOURCE_ADAPTER",
    "CONTENT_SOURCE_NAME",
    "ContentCapture",
    "ContentSource",
    "ContentSourceAdapter",
    "SourceCursor",
    "SourceJournal",
    "validate_scope_id",
]
