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

"""Explicit pytest plugin; never automatically selected by the production package."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from . import _native
from ._monitor import NativeMonitor, PathViolation

_MONITOR: NativeMonitor | None = None


def pytest_configure(config: pytest.Config) -> None:
    global _MONITOR
    if config.getoption("numprocesses", default=0):
        message = "This acceptance selector requires one monitored process; pass -n 0"
        raise pytest.UsageError(message)
    _MONITOR = NativeMonitor()
    _MONITOR.install()


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    if _MONITOR is None:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
        return
    try:
        report = _MONITOR.finish()
    except PathViolation:
        report = _MONITOR.report()
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    report.update({
        "binary_sha256": hashlib.sha256(Path(_native.__file__).read_bytes()).hexdigest(),
        "tests_collected": session.testscollected,
        "exitstatus": int(session.exitstatus),
    })
    output = os.environ.get("PC_TOPIC_STATS")
    if output:
        Path(output).write_text(json.dumps(report, sort_keys=True, indent=2))
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line("PC_TOPIC_NATIVE " + json.dumps(report, sort_keys=True))
