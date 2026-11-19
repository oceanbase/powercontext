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

"""Claude Code and Codex SessionStart hooks handle what their hosts actually send, and a host that stops reading.

``tests/fixtures/recorded_host_payloads`` holds SessionStart payloads recorded from each real host, one per
lifecycle. Each replaces the payload of the characterized bootstrap case and must lead to the same requests,
output, and state, apart from the lifecycle that the bootstrap request names. Recorded payloads carry fields that
the characterized one lacks, such as the model after a Claude Code compaction. The hooks run as in the
characterization harness, against the Server responses of its fixtures.
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.test_hook_characterization import FIXTURE_DATA, ROOT, _hook_commands, _run_case

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the recorded state includes POSIX file modes")

RECORDED = {
    host: json.loads((ROOT / f"tests/fixtures/recorded_host_payloads/{host}.json").read_text(encoding="utf-8"))
    for host in FIXTURE_DATA
}
RECORDED_CASES = [(host, lifecycle) for host, recorded in RECORDED.items() for lifecycle in recorded["payloads"]]
# In this characterized case, SessionStart injects the package and the first prompt reports its receipt.
BOOTSTRAP_CASE = "session-start-injects-bootstrap-and-prompt-reports-its-receipt"
# Runs the hook with a stdout pipe that has no reader, as when the host stops reading before the hook writes.
_UNREAD_STDOUT = """
import os, sys
reader, writer = os.pipe()
os.close(reader)
os.dup2(writer, 1)
os.close(writer)
os.execv(sys.argv[1], sys.argv[1:])
"""


@pytest.fixture(scope="module")
def hook_commands(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[str]]:
    return _hook_commands(tmp_path_factory.mktemp("hook-environments"))


@pytest.mark.parametrize(
    ("host", "lifecycle"), RECORDED_CASES, ids=[f"{host}:{lifecycle}" for host, lifecycle in RECORDED_CASES]
)
def test_recorded_session_start_payload_behaves_as_characterized(
    tmp_path: Path,
    hook_commands: dict[str, list[str]],
    host: str,
    lifecycle: str,
) -> None:
    payload = RECORDED[host]["payloads"][lifecycle]
    characterized = _case(host, BOOTSTRAP_CASE)
    session_start, *later = characterized["steps"]
    expected = copy.deepcopy([step["expected"] for step in characterized["steps"]])
    for request in expected[0]["requests"]:
        if request["path"] == "/v1/context/bootstrap":
            request["body"]["lifecycle"] = lifecycle

    observed, unscripted = _run_case(
        host,
        FIXTURE_DATA[host],
        {**characterized, "steps": [{**session_start, "payload": payload}, *later]},
        tmp_path,
        hook_commands[host],
    )

    assert payload["source"] == lifecycle
    assert unscripted == []
    assert observed == expected


@pytest.mark.parametrize("host", list(FIXTURE_DATA))
def test_session_start_reports_a_package_that_the_host_did_not_read(
    tmp_path: Path,
    hook_commands: dict[str, list[str]],
    host: str,
) -> None:
    injected = _case(host, BOOTSTRAP_CASE)["steps"][0]
    failed_claim = _case(host, "failed-receipt-claim-is-reported-and-recorded")["steps"][0]["expected"]

    (observed,), unscripted = _run_case(
        host,
        FIXTURE_DATA[host],
        {"steps": [injected]},
        tmp_path,
        [sys.executable, "-I", "-c", _UNREAD_STDOUT, *hook_commands[host]],
    )

    assert unscripted == []
    # The Server recorded the delivery before the write failed.
    assert observed["requests"] == injected["expected"]["requests"]
    # The hook reports the failed delivery as a failed claim does, and Python exits without another error.
    assert observed["exit_code"] == 0
    assert observed["stdout"] == []
    assert observed["stderr"] == failed_claim["stderr"]
    # No receipt is kept, so the first prompt cannot report a package that the model never received.
    directories = {path: entry for path, entry in injected["expected"]["state"].items() if path.endswith("/")}
    assert observed["state"] == directories | failed_claim["state"]


def _case(host: str, case_id: str) -> dict[str, Any]:
    return next(case for case in FIXTURE_DATA[host]["cases"] if case["id"] == case_id)
