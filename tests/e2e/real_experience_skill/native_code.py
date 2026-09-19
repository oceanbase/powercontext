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

"""Native Codex repair and Handoff continuation against the running real server."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from powercontext.builtin.code.config import CodeConfig
from powercontext.builtin.code.service import CodeService
from powercontext.client import PowerContextClient
from powercontext.http import CommitHandoffRequest, ContinueHandoffRequest, CreateSourceRequest, FinalizeHandoffRequest

from .test_context_text_assembly import _install_codex_plugin, _user_state


def native_delivery_and_continue(root: Path, url: str, scope_id: str, code: CodeConfig, report: dict[str, Any]) -> None:
    real_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    original_state = _user_state(real_home)
    native = root / "native"
    native.mkdir()
    home = _install_codex_plugin(native, real_home, url, None)
    repository = code.repositories[scope_id]
    preserved_test = (repository / "test_delivery.py").read_bytes()
    trace = native / "injections.jsonl"
    environment = {key: value for key, value in os.environ.items() if not key.startswith("POWERCONTEXT_")}
    environment.update({
        "CODEX_HOME": str(home),
        "NO_COLOR": "1",
        "POWERCONTEXT_CODEX_SCOPE_ID": scope_id,
        "POWERCONTEXT_CODEX_INCLUDE_CODE": "true",
        "POWERCONTEXT_CODEX_CAPTURE_PROMPTS": "false",
        "POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS": "15",
        "POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS": "30",
        "POWERCONTEXT_EVAL_TRACE_PATH": str(trace),
    })
    try:
        initial = asyncio.run(CodeService(code).status(scope_id)).fingerprint
        first = _turn(
            native,
            repository,
            environment,
            "Fix allocate_budget so negative total raises ValueError, while preserving the current half-budget result "
            "for nonnegative totals. Use the current code and historical constraints supplied by the PowerContext hook. "
            "Inspect live files, keep test_delivery.py unchanged, and execute a Python check for both behaviors. "
            "Do not use extra memory/context tools. Return the definition path, exact code fingerprint from the hook, "
            "historical constraint, and verification result.",
            "repair",
        )
        assert first["definition_path"].partition(":")[0] == "budget.py"
        assert initial in re.findall(r"\b[0-9a-f]{64}\b", first["code_fingerprint"])
        assert "test_delivery.py" in first["constraint"]
        check = (
            "from budget import allocate_budget\nfrom delivery import deliver\n"
            "assert deliver(8)==4\ntry:\n allocate_budget(-1)\nexcept ValueError:\n pass\n"
            "else:\n raise AssertionError('negative total accepted')\n"
        )
        assert subprocess.run([sys.executable, "-c", check], cwd=repository, capture_output=True).returncode == 0
        assert (repository / "test_delivery.py").read_bytes() == preserved_test
        continuation = asyncio.run(_handoff(url, scope_id))
        # A later caller edit must be captured anew, independently of the saved Handoff.
        caller = repository / "delivery.py"
        caller.write_text(caller.read_text() + "\ndef preview_budget(total):\n    return allocate_budget(total)\n")
        current = asyncio.run(CodeService(code).index(scope_id)).fingerprint
        assert current != initial
        second = _turn(
            native,
            repository,
            environment,
            "Continue this Handoff and explain the current allocate_budget definition and its preview_budget caller. "
            "Use the newly supplied hook code, inspect current files, and run a check that preview_budget(8)==4 and "
            "negative totals still raise ValueError. Keep test_delivery.py unchanged. Do not use extra memory/context "
            "tools. Return the definition path, exact current code fingerprint, preserved constraint, and verification.\n\n"
            + continuation,
            "continue",
        )
        assert second["definition_path"].partition(":")[0] == "budget.py"
        reported_hashes = re.findall(r"\b[0-9a-f]{64}\b", second["code_fingerprint"])
        assert current in reported_hashes and initial not in reported_hashes
        assert (repository / "test_delivery.py").read_bytes() == preserved_test
        assert (
            subprocess.run(
                [sys.executable, "-c", check + "from delivery import preview_budget\nassert preview_budget(8)==4"],
                cwd=repository,
                capture_output=True,
            ).returncode
            == 0
        )
        injected = [json.loads(line) for line in trace.read_text().splitlines()]
        assert len(injected) >= 2
        for event, fingerprint in zip(injected[-2:], (initial, current), strict=True):
            content = event["injected_text"]
            assert fingerprint in content and "Current code references" in content
            assert "## Memory" in content and len(content.encode()) <= 8000
        report["checks"].append("native_codex_repair_checks_handoff_and_fresh_code_after_edit")
        report["native_codex"] = {
            "repair": first,
            "continue": second,
            "injection_sha256": [hashlib.sha256(e["injected_text"].encode()).hexdigest() for e in injected],
        }
    finally:
        assert _user_state(real_home) == original_state
        report["user_codex_state_unchanged"] = True


async def _handoff(url: str, scope: str) -> str:
    async with PowerContextClient(url, timeout=120) as client:
        evidence = await client.create_source(
            scope,
            CreateSourceRequest(
                content="Verified negative allocate_budget inputs raise ValueError and deliver(8)==4. Preserve test_delivery.py. "
                "Continue by inspecting current callers and running the same checks."
            ),
        )
        citation = {"kind": "source", "source_ref": {"name": "content", "source_id": evidence.source_id}}
        prepared = await client.finalize_handoff(
            FinalizeHandoffRequest.model_validate({
                "scope_id": scope,
                "draft": {
                    "objective": "Continue budget validation",
                    "disposition": "continuable",
                    "state": [
                        {
                            "text": "Negative input fixed; preserve test_delivery.py and the half-budget behavior.",
                            "citations": [citation],
                        }
                    ],
                    "next_action": {"text": "Inspect current callers and rerun checks.", "citations": [citation]},
                    "omissions": [],
                },
            })
        )
        committed = await client.commit_handoff(CommitHandoffRequest(scope_id=scope, handoff=prepared))
        continued = await client.continue_handoff(
            ContinueHandoffRequest.model_validate({
                "scope_id": scope,
                "selection": "exact",
                "revision": committed.reference.model_dump(mode="json"),
            })
        )
        return continued.model_dump_json()


def _turn(root: Path, repository: Path, environment: dict[str, str], prompt: str, name: str) -> dict[str, Any]:
    executable = shutil.which("codex")
    assert executable
    schema = root / "answer-schema.json"
    keys = ["definition_path", "code_fingerprint", "constraint", "validation"]
    schema.write_text(
        json.dumps({
            "type": "object",
            "additionalProperties": False,
            "required": keys,
            "properties": {key: {"type": "string"} for key in keys},
        })
    )
    output = root / f"{name}.json"
    completed = subprocess.run(
        [
            executable,
            "-a",
            "never",
            "--disable",
            "memories",
            "--disable",
            "shell_snapshot",
            "exec",
            "--ephemeral",
            "--dangerously-bypass-hook-trust",
            "--json",
            "--skip-git-repo-check",
            "-s",
            "workspace-write",
            "-C",
            str(repository),
            "--output-schema",
            str(schema),
            "-o",
            str(output),
            prompt,
        ],
        env=environment,
        cwd=root,
        capture_output=True,
        text=True,
        timeout=360,
    )
    (root / f"{name}-events.jsonl").write_text(completed.stdout)
    assert completed.returncode == 0, f"Native {name} failed with exit {completed.returncode}"
    events = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith("{")]
    assert any(
        e.get("type") == "item.completed"
        and e.get("item", {}).get("type") == "command_execution"
        and e["item"].get("exit_code") == 0
        for e in events
    ), "Native agent must execute a real command"
    return json.loads(output.read_text())
