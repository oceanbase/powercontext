# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Real coordinator OS locks and simulated migration connection shutdown.

These tests verify local exclusion and explicit recovery acknowledgment, not
native OceanBase or ODP execution. No remote database or service is contacted.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.migrations import oceanbase
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.oceanbase import oceanbase_migration_lock, pending_run

_DATABASE_ID = "scratch-database-identity"


class _Connection:
    def __init__(self) -> None:
        self.closed = False
        self.invalidated = False
        self.close_error = False
        self.close_started = asyncio.Event()
        self.close_allowed: asyncio.Event | None = None
        self.close_receipt: Path | None = None
        self.physical_session = 101

    async def scalar(self, statement):
        raise RuntimeError("Physical session and global PROCESS inspection are unavailable")  # noqa: TRY003

    async def commit(self) -> None:
        # A proxy may route the next transaction to a different server session.
        self.physical_session += 1

    async def close(self) -> None:
        self.close_started.set()
        if self.close_allowed is not None:
            await self.close_allowed.wait()
        if self.close_error:
            raise OSError("simulated close failure")  # noqa: TRY003
        if self.close_receipt is not None:
            assert self.close_receipt.is_file(), "The durable run receipt disappeared before connection close"
        self.closed = True

    def connection(self) -> AsyncConnection:
        return cast(AsyncConnection, self)

    def sync(self) -> Connection:
        owner = self

        class Sync:
            @property
            def closed(self) -> bool:
                return owner.closed

            @property
            def invalidated(self) -> bool:
                return owner.invalidated

            def scalar(self, statement):
                raise RuntimeError("Physical session and global PROCESS inspection are unavailable")  # noqa: TRY003

        return cast(Connection, Sync())


def _receipt(directory: Path) -> Path:
    return directory / _DATABASE_ID / "active-run.json"


def test_commits_and_proxy_session_changes_keep_the_lock_until_successful_connection_close(tmp_path: Path) -> None:
    owner = _Connection()
    owner.close_receipt = _receipt(tmp_path)

    async def scenario() -> None:
        async with oceanbase_migration_lock(owner.connection(), database_id=_DATABASE_ID, directory=tmp_path) as held:
            await owner.commit()
            await held.verify()
            held.verify_sync(owner.sync())
            assert pending_run(tmp_path, _DATABASE_ID) is None
            assert json.loads(_receipt(tmp_path).read_text())["database_id"] == _DATABASE_ID
            competitor = _Connection()
            with pytest.raises(MigrationError) as locked:
                async with oceanbase_migration_lock(
                    competitor.connection(), database_id=_DATABASE_ID, directory=tmp_path
                ):
                    pytest.fail("A second maintenance operation entered")
            assert locked.value.code == "migration_locked"
        assert owner.closed
        assert not _receipt(tmp_path).exists()
        assert pending_run(tmp_path, _DATABASE_ID) is None
        assert (tmp_path / _DATABASE_ID / "migration.lock").is_file()
        with pytest.raises(MigrationError) as released:
            await held.verify()
        assert released.value.code == "migration_lock_lost"

    asyncio.run(scenario())


_OS_PROBE = """
import os, sys
fd = os.open(sys.argv[1], os.O_RDWR)
try:
    if os.name == 'posix':
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    elif os.name == 'nt':
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        raise RuntimeError('unsupported test host')
except OSError:
    print('locked')
else:
    print('acquired')
finally:
    os.close(fd)
"""


def test_the_os_lock_excludes_a_separate_process_and_reuses_its_inode(tmp_path: Path) -> None:
    owner = _Connection()
    path = tmp_path / _DATABASE_ID / "migration.lock"

    def probe() -> str:
        result = subprocess.run(
            [sys.executable, "-c", _OS_PROBE, str(path)], capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    async def scenario() -> tuple[int, int]:
        async with oceanbase_migration_lock(owner.connection(), database_id=_DATABASE_ID, directory=tmp_path):
            assert probe() == "locked"
            information = path.stat()
            return information.st_dev, information.st_ino

    inode = asyncio.run(scenario())
    assert probe() == "acquired"
    information = path.stat()
    assert (information.st_dev, information.st_ino) == inode


@pytest.mark.parametrize("replacement", ["file", "parent"])
@pytest.mark.skipif(os.name == "nt", reason="Windows prevents renaming an open locked file")
def test_replacing_the_lock_file_or_directory_invalidates_ownership(tmp_path: Path, replacement: str) -> None:
    owner = _Connection()

    def replace(path: Path) -> None:
        if replacement == "file":
            path.rename(path.with_suffix(".original"))
            path.touch()
        else:
            path.parent.rename(tmp_path / "old-database-directory")
            path.parent.mkdir()

    async def scenario() -> None:
        with pytest.raises(MigrationError) as changed:
            async with oceanbase_migration_lock(
                owner.connection(), database_id=_DATABASE_ID, directory=tmp_path
            ) as held:
                replace(Path(held.name))
                await held.verify()
        assert changed.value.code == "migration_lock_lost"
        assert owner.closed
        retained = _receipt(tmp_path) if replacement == "file" else tmp_path / "old-database-directory/active-run.json"
        assert retained.is_file()

    asyncio.run(scenario())


def test_the_evidence_directory_is_bound_to_the_fixed_coordinator_host(tmp_path: Path, monkeypatch) -> None:
    owner = _Connection()
    monkeypatch.setattr(oceanbase.socket, "gethostname", lambda: "maintenance-host")

    async def bind() -> None:
        async with oceanbase_migration_lock(owner.connection(), database_id=_DATABASE_ID, directory=tmp_path):
            pass

    asyncio.run(bind())
    binding = (tmp_path / "coordinator.json").read_bytes()
    monkeypatch.setattr(oceanbase.socket, "gethostname", lambda: "another-host")
    other_owner = _Connection()

    async def reject() -> None:
        with pytest.raises(MigrationError) as wrong_host:
            async with oceanbase_migration_lock(other_owner.connection(), database_id=_DATABASE_ID, directory=tmp_path):
                pytest.fail("A different coordinator host entered")
        assert wrong_host.value.code == "recovery_required"
        with pytest.raises(MigrationError) as wrong_host_plan:
            pending_run(tmp_path, _DATABASE_ID)
        assert wrong_host_plan.value.code == "recovery_required"

    asyncio.run(reject())
    assert (tmp_path / "coordinator.json").read_bytes() == binding


def test_interrupt_retains_the_run_and_only_exact_acknowledgment_permits_recovery(tmp_path: Path) -> None:
    owner = _Connection()
    interruption = KeyboardInterrupt("operator interrupted after committed DDL")

    async def interrupted() -> None:
        async with oceanbase_migration_lock(owner.connection(), database_id=_DATABASE_ID, directory=tmp_path):
            await owner.commit()
            raise interruption

    with pytest.raises(KeyboardInterrupt) as preserved:
        asyncio.run(interrupted())
    assert preserved.value is interruption
    assert owner.closed
    original_receipt = _receipt(tmp_path).read_bytes()
    token = pending_run(tmp_path, _DATABASE_ID)
    assert token is not None

    async def retry() -> None:
        for acknowledgment in (None, "0" * 32):
            with pytest.raises(MigrationError) as unresolved:
                async with oceanbase_migration_lock(
                    _Connection().connection(),
                    database_id=_DATABASE_ID,
                    directory=tmp_path,
                    acknowledged_run=acknowledgment,
                ):
                    pytest.fail("Recovery entered without acknowledgment of the exact interrupted run")
            assert unresolved.value.code == "recovery_required"
            assert _receipt(tmp_path).read_bytes() == original_receipt
        recovered = _Connection()
        async with oceanbase_migration_lock(
            recovered.connection(), database_id=_DATABASE_ID, directory=tmp_path, acknowledged_run=token
        ) as held:
            await held.verify()
            assert pending_run(tmp_path, _DATABASE_ID) == token
            new_token = json.loads(_receipt(tmp_path).read_text())["token"]
            assert new_token != token
        assert recovered.closed
        assert pending_run(tmp_path, _DATABASE_ID) is None
        with pytest.raises(MigrationError) as stale:
            async with oceanbase_migration_lock(
                _Connection().connection(), database_id=_DATABASE_ID, directory=tmp_path, acknowledged_run=token
            ):
                pytest.fail("An acknowledgment was accepted after its run had been resolved")
        assert stale.value.code == "stale_ack"
        assert not _receipt(tmp_path).exists()

    asyncio.run(retry())


@pytest.mark.parametrize("body_interrupt", [False, True])
def test_failed_close_retains_the_receipt_and_preserves_an_original_interruption(
    tmp_path: Path, body_interrupt: bool
) -> None:
    owner = _Connection()
    owner.close_error = True
    interruption = KeyboardInterrupt("operator interrupted")

    async def maintain() -> None:
        async with oceanbase_migration_lock(owner.connection(), database_id=_DATABASE_ID, directory=tmp_path):
            if body_interrupt:
                raise interruption

    if body_interrupt:
        with pytest.raises(KeyboardInterrupt) as preserved:
            asyncio.run(maintain())
        assert preserved.value is interruption
        assert "OSError" in " ".join(getattr(interruption, "__notes__", []))
    else:
        with pytest.raises(OSError, match="simulated close failure"):
            asyncio.run(maintain())
    token = pending_run(tmp_path, _DATABASE_ID)
    assert token is not None

    async def reject() -> None:
        with pytest.raises(MigrationError) as blocked:
            async with oceanbase_migration_lock(
                _Connection().connection(), database_id=_DATABASE_ID, directory=tmp_path
            ):
                pytest.fail("Unclean shutdown was ignored")
        assert blocked.value.code == "recovery_required"
        assert pending_run(tmp_path, _DATABASE_ID) == token

    asyncio.run(reject())


def test_guard_rejects_a_removed_run_receipt_before_further_work(tmp_path: Path) -> None:
    owner = _Connection()

    async def scenario() -> None:
        with pytest.raises(MigrationError) as missing:
            async with oceanbase_migration_lock(
                owner.connection(), database_id=_DATABASE_ID, directory=tmp_path
            ) as held:
                _receipt(tmp_path).unlink()
                held.verify_sync(owner.sync())
        assert missing.value.code == "recovery_required"
        assert owner.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("during_close", [False, True])
def test_cancellation_holds_the_lock_through_close_and_retains_the_run(tmp_path: Path, during_close: bool) -> None:
    owner = _Connection()

    async def scenario() -> None:
        entered = asyncio.Event()
        owner.close_allowed = asyncio.Event()

        async def maintain() -> None:
            async with oceanbase_migration_lock(owner.connection(), database_id=_DATABASE_ID, directory=tmp_path):
                entered.set()
                if not during_close:
                    await asyncio.Event().wait()

        task = asyncio.create_task(maintain())
        await entered.wait()
        if not during_close:
            task.cancel()
        await owner.close_started.wait()
        task.cancel()
        await asyncio.sleep(0)
        with pytest.raises(MigrationError) as locked:
            async with oceanbase_migration_lock(
                _Connection().connection(), database_id=_DATABASE_ID, directory=tmp_path
            ):
                pytest.fail("Cancellation released the lock before connection close")
        assert locked.value.code == "migration_locked"
        token = pending_run(tmp_path, _DATABASE_ID)
        assert token is not None
        owner.close_allowed.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert owner.closed
        assert pending_run(tmp_path, _DATABASE_ID) == token
        async with oceanbase_migration_lock(
            _Connection().connection(), database_id=_DATABASE_ID, directory=tmp_path, acknowledged_run=token
        ):
            pass
        assert pending_run(tmp_path, _DATABASE_ID) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("previously_interrupted", [False, True])
def test_locked_plan_recheck_sees_the_prior_run_while_independent_inspection_sees_the_current_run(
    tmp_path: Path, previously_interrupted: bool
) -> None:
    async def scenario() -> None:
        if previously_interrupted:
            with pytest.raises(RuntimeError, match="committed DDL interrupted"):
                async with oceanbase_migration_lock(
                    _Connection().connection(), database_id=_DATABASE_ID, directory=tmp_path
                ):
                    raise RuntimeError("committed DDL interrupted")  # noqa: TRY003
        prior_token = pending_run(tmp_path, _DATABASE_ID)
        # Create the inspection task before entering, matching an independent
        # operator's context rather than inheriting the admitted run context.
        inspect = asyncio.Event()

        async def independent_inspection() -> str | None:
            await inspect.wait()
            return pending_run(tmp_path, _DATABASE_ID)

        observer = asyncio.create_task(independent_inspection())
        async with oceanbase_migration_lock(
            _Connection().connection(), database_id=_DATABASE_ID, directory=tmp_path, acknowledged_run=prior_token
        ):
            assert pending_run(tmp_path, _DATABASE_ID) == prior_token
            inspect.set()
            current_token = await observer
            assert current_token is not None
            assert current_token == json.loads(_receipt(tmp_path).read_text())["token"]
        assert pending_run(tmp_path, _DATABASE_ID) is None

    asyncio.run(scenario())


def test_readonly_inspection_is_side_effect_free_and_rejects_unknown_run_records(tmp_path: Path) -> None:
    directory = tmp_path / "absent-coordinator"
    assert pending_run(directory, _DATABASE_ID) is None
    assert not directory.exists()
    receipt = _receipt(tmp_path)
    receipt.parent.mkdir()
    receipt.write_text(json.dumps({"version": 1, "database_id": _DATABASE_ID, "token": "0" * 32}))
    original = receipt.read_bytes()
    with pytest.raises(MigrationError) as unknown:
        pending_run(tmp_path, _DATABASE_ID)
    assert unknown.value.code == "recovery_required"
    assert receipt.read_bytes() == original
