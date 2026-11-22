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

"""Claude Code and Codex prompt hooks handle the UserPromptSubmit payloads that their hosts actually send.

``tests/fixtures/recorded_host_payloads`` holds a UserPromptSubmit payload recorded from each real host. It replaces
the payload of the characterized prompt case and must lead to the same requests, output, and state. The Codex
payload also carries the permission mode, which the characterized one lacks. The hooks run as in the
characterization harness, against the Server responses of its fixtures.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from tests.test_hook_characterization import FIXTURE_DATA, ROOT, _hook_commands, _run_case

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the characterization harness runs on POSIX hosts only")

RECORDED = {
    host: json.loads((ROOT / f"tests/fixtures/recorded_host_payloads/{host}.json").read_text(encoding="utf-8"))
    for host in FIXTURE_DATA
}
# In this characterized case, the prompt recalls context for the host and is captured as a Source.
PROMPT_CASE = "prompt-recalls-and-captures"


@pytest.fixture(scope="module")
def hook_commands(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[str]]:
    return _hook_commands(tmp_path_factory.mktemp("hook-environments"))


@pytest.mark.parametrize("host", list(FIXTURE_DATA))
def test_recorded_prompt_payload_behaves_as_characterized(
    tmp_path: Path,
    hook_commands: dict[str, list[str]],
    host: str,
) -> None:
    characterized = next(case for case in FIXTURE_DATA[host]["cases"] if case["id"] == PROMPT_CASE)
    (step,) = characterized["steps"]

    observed, unscripted = _run_case(
        host,
        FIXTURE_DATA[host],
        {**characterized, "steps": [{**step, "payload": RECORDED[host]["UserPromptSubmit"]}]},
        tmp_path,
        hook_commands[host],
    )

    assert unscripted == []
    assert observed == [step["expected"]]
