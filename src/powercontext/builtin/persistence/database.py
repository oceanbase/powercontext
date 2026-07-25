"""Async engine ownership and explicit transaction boundaries."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from powercontext.builtin.persistence.errors import DatabaseClosedError


class AsyncDatabase:
    """Own or attach to one SQLAlchemy async engine.

    Repositories receive the yielded ``AsyncConnection`` and never own this
    object. Closing an attached database leaves the caller's engine available.
    """

    def __init__(self, engine: AsyncEngine, *, owns_engine: bool) -> None:
        self._engine = engine
        self._owns_engine = owns_engine
        self._closed = False
        self._closing = False
        self._active_transactions = 0
        self._state_changed = asyncio.Condition()
        self._close_lock = asyncio.Lock()

    @classmethod
    def attach(cls, engine: AsyncEngine, /) -> AsyncDatabase:
        """Use a caller-owned async engine without taking disposal ownership."""

        return cls(engine, owns_engine=False)

    @classmethod
    def own(cls, engine: AsyncEngine, /) -> AsyncDatabase:
        """Take disposal ownership of an already configured async engine."""

        return cls(engine, owns_engine=True)

    @property
    def engine(self) -> AsyncEngine:
        """Return the upstream engine used by this database."""

        return self._engine

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncConnection]:
        """Yield a connection in a transaction owned by the calling use case."""

        async with self._state_changed:
            if self._closed or self._closing:
                raise DatabaseClosedError
            self._active_transactions += 1
        try:
            async with self._engine.begin() as connection:
                yield connection
        finally:
            async with self._state_changed:
                self._active_transactions -= 1
                self._state_changed.notify_all()

    async def close(self) -> None:
        """Drain active transactions, then dispose only an owned engine."""

        async with self._close_lock:
            if self._closed:
                return
            async with self._state_changed:
                self._closing = True
                try:
                    await self._state_changed.wait_for(lambda: self._active_transactions == 0)
                except BaseException:
                    self._closing = False
                    self._state_changed.notify_all()
                    raise
            try:
                if self._owns_engine:
                    await self._engine.dispose()
            except BaseException:
                async with self._state_changed:
                    self._closing = False
                    self._state_changed.notify_all()
                raise
            async with self._state_changed:
                self._closed = True
                self._closing = False
                self._state_changed.notify_all()
