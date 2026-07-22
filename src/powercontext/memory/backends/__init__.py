"""Concrete Memory storage adapters, loaded only for the selected extra."""

from typing import TYPE_CHECKING

from powercontext.memory.backends.base import DatabaseMemoryBackend

if TYPE_CHECKING:
    from powercontext.memory.backends.oceanbase import OceanBaseMemoryBackend
    from powercontext.memory.backends.sqlite import SQLiteMemoryBackend

__all__ = ["DatabaseMemoryBackend", "OceanBaseMemoryBackend", "SQLiteMemoryBackend"]


def __getattr__(name: str) -> object:
    if name == "OceanBaseMemoryBackend":
        from powercontext.memory.backends.oceanbase import OceanBaseMemoryBackend

        return OceanBaseMemoryBackend
    if name == "SQLiteMemoryBackend":
        from powercontext.memory.backends.sqlite import SQLiteMemoryBackend

        return SQLiteMemoryBackend
    raise AttributeError(name)
