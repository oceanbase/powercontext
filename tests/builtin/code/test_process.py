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

"""Real subprocess limits include children after their parent has exited."""

import asyncio
import sys
from pathlib import Path
from time import monotonic

import pytest

from powercontext.builtin.code.errors import CodeUnavailableError
from powercontext.builtin.code.process import run_process


@pytest.mark.skipif(sys.platform != "linux", reason="This process-state check uses Linux /proc")
def test_timeout_stops_descendant_after_parent_exit(tmp_path: Path) -> None:
    async def scenario() -> None:
        child_file = tmp_path / "child.pid"
        program = (
            "import subprocess,sys,pathlib\n"
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
            "pathlib.Path(sys.argv[1]).write_text(str(child.pid))\n"
        )
        with pytest.raises(CodeUnavailableError, match="code_timeout"):
            await run_process(
                (sys.executable, "-c", program, str(child_file)),
                cwd=tmp_path,
                deadline=monotonic() + 1,
                max_output_bytes=100,
            )
        pid = int(child_file.read_text())
        state = Path(f"/proc/{pid}/stat")

        def running() -> bool:
            try:
                return state.read_text().split()[2] != "Z"
            except (FileNotFoundError, ProcessLookupError):
                return False

        for _ in range(100):
            if not await asyncio.to_thread(running):
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("Timed-out descendant is still running")

    asyncio.run(scenario())


def test_oversized_output_and_sensitive_environment_are_bounded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_TEST_PRIVATE_VALUE", "test-only-marker")

    async def scenario() -> None:
        result = await run_process(
            (sys.executable, "-c", "import os; print('POWERCONTEXT_TEST_PRIVATE_VALUE' in os.environ)"),
            cwd=tmp_path,
            deadline=monotonic() + 5,
            max_output_bytes=100,
        )
        assert result == b"False\n"
        with pytest.raises(CodeUnavailableError, match="code_output_limit"):
            await run_process(
                (sys.executable, "-c", "print('x' * 1000000)"),
                cwd=tmp_path,
                deadline=monotonic() + 5,
                max_output_bytes=512,
            )

    asyncio.run(scenario())
