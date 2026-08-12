from __future__ import annotations

import asyncio
from pathlib import Path

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    ReadinessCheckStatus,
    RuntimeReadinessStatus,
    open_builtin_runtime,
)


def test_builtin_runtime_reports_runtime_and_database_readiness(tmp_path: Path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
        )
        async with open_builtin_runtime(config) as runtime:
            readiness = await runtime.readiness()

        assert readiness.status is RuntimeReadinessStatus.READY
        assert readiness.checks == {
            "runtime": ReadinessCheckStatus.READY,
            "database": ReadinessCheckStatus.READY,
        }

    asyncio.run(scenario())
