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

"""Failed xdist cases retain their identity and existing structured diagnostics."""

import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest_plugins = ["pytester"]


def test_xdist_failure_is_retained_with_its_existing_error_classification(pytester, monkeypatch):
    scripts = Path(__file__).resolve().parents[1] / ".github" / "scripts"
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(filter(None, (str(scripts), os.environ.get("PYTHONPATH")))))
    monkeypatch.setenv("RUNNER_TEMP", str(pytester.path / "runner-temp"))
    pytester.makepyfile(
        """
        import logging

        def test_failure():
            logging.getLogger("fixture").warning(
                "controlled write failure",
                extra={"error_code": "fixture_timeout", "exception_type": "TimeoutError",
                       "unlisted_detail": "do-not-copy-this-field"},
            )
            assert False, "controlled test failure"
        """
    )
    result = pytester.runpytest_subprocess("-n", "2", "-p", "pytest_ci_diagnostics", "-q")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    evidence = pytester.path / "runner-temp" / "pytest-diagnostics" / "failures.jsonl"
    assert evidence.is_file(), "The controller must retain a failed worker's report before the job exits"
    reports = [json.loads(line) for line in evidence.read_text().splitlines()]
    assert len(reports) == 1
    report = reports[0]
    assert report["nodeid"].endswith("::test_failure")
    assert report["when"] == "call"
    assert "controlled test failure" in report["longrepr"]
    section = next(text for title, text in report["sections"] if title == "CI structured captured logs")
    records = json.loads(section)
    assert records[0]["error_code"] == "fixture_timeout"
    assert records[0]["exception_type"] == "TimeoutError"
    assert "unlisted_detail" not in records[0]
    assert "do-not-copy-this-field" not in section


def test_worker_timeout_retains_live_wait_before_cleanup(pytester, monkeypatch):
    """A controlled timeout validates capture plumbing, not the intermittent worker failure."""
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join((str(root), str(root / ".github" / "scripts"))))
    monkeypatch.setenv("RUNNER_TEMP", str(pytester.path / "runner-temp"))
    monkeypatch.setenv("DIAGNOSTIC_TEST_SECRET", "credential-must-not-appear-in-snapshot")
    pytester.makepyfile(
        """
        import asyncio
        import multiprocessing
        import os

        from powercontext.builtin.runtime.artifact_processing import _SpawnedWorkerHandle
        from tests.e2e.worker_timeout_diagnostics import record_worker_timeout

        def test_timeout(request, tmp_path):
            original = []

            async def scenario():
                context = multiprocessing.get_context("spawn")
                receiver, sender = context.Pipe(duplex=False)
                process = context.Process(target=receiver.recv, name="diagnostic-worker")
                process.start()
                handle = _SpawnedWorkerHandle(process, receiver)

                async def middle_wait():
                    secret = os.environ["DIAGNOSTIC_TEST_SECRET"]
                    await handle._join()
                    assert secret

                async def outer_wait():
                    await middle_wait()

                waiter = asyncio.create_task(outer_wait())
                await asyncio.sleep(0)
                await asyncio.sleep(0)
                try:
                    async with asyncio.timeout(0):
                        await asyncio.sleep(0)
                except TimeoutError as error:
                    original.append(error)
                    record_worker_timeout(request, tmp_path)
                    raise
                finally:
                    sender.send(None)
                    await waiter
                    await handle._close()
                    sender.close()
                    (tmp_path / "cleanup-finished").touch()

            try:
                asyncio.run(scenario())
            except TimeoutError as error:
                assert error is original[0]
                raise
        """
    )
    result = pytester.runpytest_subprocess("-n", "1", "-p", "pytest_ci_diagnostics", "-q")
    assert result.ret == pytest.ExitCode.TESTS_FAILED
    report_dir = pytester.path / "runner-temp" / "pytest-diagnostics"
    reports = [json.loads(line) for line in (report_dir / "failures.jsonl").read_text().splitlines()]
    assert len(reports) == 1
    report = reports[0]
    assert "TimeoutError" in report["longrepr"]
    snapshot = next(text for title, text in report["sections"] if "worker timeout" in title)
    assert "outer_wait" in snapshot
    assert "middle_wait" in snapshot
    assert "_join" in snapshot
    assert "threads" in snapshot
    assert re.search(r"parent_pid=\d+", snapshot)
    assert "name=diagnostic-worker" in snapshot
    assert re.search(r"executor threads=\d+ queued=\d+", snapshot)
    assert "closed=False" in snapshot
    assert "close_lock_locked=False" in snapshot
    assert "cached_returncode=unknown" in snapshot
    assert "join_task=" in snapshot
    if sys.platform == "linux":
        assert re.search(r"proc state=\w ppid=\d+ utime_ticks=\d+ stime_ticks=\d+", snapshot)
    else:
        assert "proc state=unknown" in snapshot
    assert "credential-must-not-appear-in-snapshot" not in snapshot
    saved = list(pytester.path.rglob("worker-timeout.log"))
    assert len(saved) == 1
    assert saved[0].read_text() == snapshot
    assert saved[0].parent.name == "logs"
    assert (saved[0].parent.parent / "cleanup-finished").exists()


def test_worker_diagnostic_failure_does_not_replace_original_timeout(tmp_path, monkeypatch):
    from tests.e2e import worker_timeout_diagnostics

    def diagnostic_failure(*args):
        raise OSError("sensitive exception detail")  # noqa: TRY003

    monkeypatch.setattr(worker_timeout_diagnostics.sys, "_current_frames", diagnostic_failure)
    request = SimpleNamespace(node=SimpleNamespace(add_report_section=diagnostic_failure))
    unwritable = tmp_path / "file"
    unwritable.touch()
    timeout = TimeoutError()
    with pytest.raises(TimeoutError) as raised:
        try:
            raise timeout
        except TimeoutError:
            worker_timeout_diagnostics.record_worker_timeout(request, unwritable)
            raise
    assert raised.value is timeout
