"""Process-local coordination shared by SQLite-backed components."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from threading import Lock, RLock
from weakref import WeakValueDictionary

SQLiteWriteLock = AbstractContextManager[object]

_registry_lock = Lock()
_write_locks: WeakValueDictionary[str, SQLiteWriteLock] = WeakValueDictionary()


def sqlite_write_lock(database: str | Path, /) -> SQLiteWriteLock:
    """Return one process-local writer lock for a SQLite database."""

    name = str(database)
    if name == ":memory:":
        return RLock()
    key = str(Path(name).expanduser().resolve())
    with _registry_lock:
        existing = _write_locks.get(key)
        if existing is not None:
            return existing
        created = RLock()
        _write_locks[key] = created
        return created


__all__ = ["SQLiteWriteLock", "sqlite_write_lock"]
