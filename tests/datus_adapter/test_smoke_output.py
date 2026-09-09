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

"""CLI output containment uses synthetic sentinels only, never real credentials."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Datus CLI sandbox smoke requires Linux/POSIX libc")

PROBE = r"""
import atexit
import asyncio
import ctypes
import os
import subprocess
import sys
from types import SimpleNamespace
from powercontext_datus import native

phase, failure = sys.argv[1:]
sentinel = "synthetic-do-not-publish"
original_stdout = sys.stdout

def noise(current):
    if current != phase:
        return
    print(sentinel, flush=True)
    print(sentinel, file=sys.stderr, flush=True)
    os.write(1, sentinel.encode())
    os.write(2, sentinel.encode())
    subprocess.run([sys.executable, "-c", "import os; os.write(1, b'synthetic-do-not-publish'); os.write(2, b'synthetic-do-not-publish')"], check=True)
    print(sentinel, end="")
    print(sentinel, end="", file=sys.stderr)
    original_stdout.write(sentinel)
    ctypes.CDLL(None).printf(b"synthetic-do-not-publish")
    atexit.register(lambda: (os.write(1, sentinel.encode()), os.write(2, sentinel.encode())))
    if failure == "yes":
        raise RuntimeError(sentinel)
    if failure == "interrupt":
        raise KeyboardInterrupt(sentinel)
    if failure == "exit":
        raise SystemExit(sentinel)
    if failure == "cancel":
        raise asyncio.CancelledError(sentinel)

class Connector:
    def __init__(self, config):
        noise("construct")
    def execute_query(self, sql, result_format):
        noise("query")
        return SimpleNamespace(success=True, sql_return=[{"adapter_smoke": 1}], row_count=sentinel)
    def close(self):
        noise("close")

original_import = native.importlib.import_module
def importing(name):
    if name == "datus_mysql":
        noise("import")
        return SimpleNamespace(MySQLConnector=Connector)
    if name == "structlog":
        return SimpleNamespace(configure=lambda **kw: None, make_filtering_bound_logger=lambda *args: None)
    return original_import(name)

native.importlib.import_module = importing
native.verify_runtime = lambda: {"synthetic": True}
native.skill_smoke = lambda *args: {"synthetic": True}
sys.argv = ["native", "--skill-root", ".", "--db"]
native.main()
"""


@pytest.mark.parametrize("phase", ["import", "construct", "query", "close"])
@pytest.mark.parametrize("failure", ["yes", "no", "interrupt", "exit", "cancel"])
def test_native_smoke_only_emits_bounded_json(tmp_path, phase, failure):
    source = Path(__file__).resolve().parents[2] / "integrations/datus/src"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(source),
        "PYTHONDONTWRITEBYTECODE": "1",
        "DATUS_DB_HOST": "invalid.example",
        "DATUS_DB_USER": "synthetic",
        "DATUS_DB_NAME": "synthetic",
        "DATUS_DB_PASSWORD": "synthetic-do-not-publish",
    }
    result = subprocess.run(
        [sys.executable, "-B", "-c", PROBE, phase, failure],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert "synthetic-do-not-publish" not in result.stdout + result.stderr
    assert result.stderr == ""
    report = json.loads(result.stdout)
    assert report["native_agent_qa_runs"] == 0
    exit_codes = {"yes": 1, "no": 0, "interrupt": 130, "exit": 1, "cancel": 1}
    assert result.returncode == exit_codes[failure]
    assert report["status"] == ("passed" if failure == "no" else "failed")
    if failure != "no":
        expected_type = {
            "yes": "RuntimeError",
            "interrupt": "KeyboardInterrupt",
            "exit": "SystemExit",
            "cancel": "CancelledError",
        }
        assert report["error_type"] == expected_type[failure]
    else:
        assert report["database"]["row_count"] == 1
