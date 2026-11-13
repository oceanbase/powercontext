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

"""Publish single-case pytest phase boundaries to the external CI supervisor."""

from __future__ import annotations

import faulthandler
import json
import os
import signal
import time
from pathlib import Path
from typing import BinaryIO

import pytest

# These single-case interpreters exit after pytest. Keep the descriptor alive
# through atexit handlers too: cleanup has not finished until the process exits.
_STACK_FILES: list[BinaryIO] = []


def _phase(name: str) -> None:
    value = os.environ.get("POWERCONTEXT_CI_PHASE_FILE")
    if value is None:
        return
    path = Path(value)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"phase": name, "started": time.monotonic(), "pid": os.getpid()}))
    temporary.replace(path)


def pytest_configure() -> None:
    if value := os.environ.get("POWERCONTEXT_CI_PHASE_FILE"):
        # Signal-triggered stacks avoid CPython's background faulthandler watchdog.
        # Registration precedes publication, so the supervisor never signals an
        # interpreter that has not opted in. Stacks omit locals and SQL values.
        # pytest redirects stderr during each phase. A separate artifact keeps
        # the stack even when a timed-out process cannot flush captured output.
        output = Path(value).with_name("python-stacks.log").open("wb")
        _STACK_FILES.append(output)
        faulthandler.register(signal.SIGUSR1, file=output)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup() -> None:
    _phase("initialization")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call() -> None:
    _phase("execution")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown() -> None:
    _phase("cleanup")
