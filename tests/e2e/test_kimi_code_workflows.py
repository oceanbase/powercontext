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

"""Real Kimi native MCP workflows against Server/SQLite with controlled agent inference."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.cli.app import create_cli
from powercontext.cli.installation import setup as install_plugin
from powercontext.cli.kimi_code import KimiInstaller
from powercontext.server.factory import create_server_app
from powercontext.server.settings import ServerSettings
from tests.e2e.test_kimi_code_host import REPOSITORY, ModelGateway, _objects, serving


@pytest.mark.skipif(not os.environ.get("KIMI_CLI_BIN"), reason="Set KIMI_CLI_BIN to the real Kimi CLI")
def test_real_kimi_profiles_handoff_review_and_external_skills(tmp_path: Path, monkeypatch) -> None:  # noqa: C901 - preserve one real-host journey across sessions.
    node = shutil.which("node")
    assert node
    cli = Path(os.environ["KIMI_CLI_BIN"])
    assert cli.is_file()
    command = [node, str(cli)] if cli.suffix in {".mjs", ".js", ".cjs"} else [str(cli)]
    workspace = tmp_path / "workspace"
    external = workspace / ".agents/skills/external-contract"
    external.mkdir(parents=True)
    (external / "SKILL.md").write_text(
        "---\nname: external-contract\ndescription: Use when checking contracts.\n---\n\nRun contract tests.\n",
        encoding="utf-8",
    )
    home = tmp_path / "kimi home"
    home.mkdir()
    monkeypatch.setenv("KIMI_CODE_HOME", str(home))
    monkeypatch.delenv("POWERCONTEXT_KIMI_TOKEN", raising=False)
    gateway = ModelGateway(None)
    thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    thread.start()
    app = create_server_app(
        settings=ServerSettings(
            workspace=workspace,
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'workflows.db'}"),
        )
    )
    state: dict[str, Any] = {}
    replies: list[dict[str, Any]] = []
    steps: list[tuple[str, Any]] = []
    errors: list[str] = []

    def choose(body, scope_id, messages):
        index = len(messages)
        if messages:
            result = next(
                (
                    item
                    for item in _objects(messages[-1]["content"])
                    if any(
                        key in item
                        for key in (
                            "scope_id",
                            "boundary",
                            "candidate_id",
                            "receipt",
                            "source",
                            "skills",
                            "registrations",
                            "status",
                            "reference",
                            "artifact",
                            "records",
                        )
                    )
                ),
                None,
            )
            if result is None:
                errors.append(json.dumps(messages[-1]["content"])[:2000])
                return None, None
            if len(replies) < index:
                replies.append(result)
        if index >= len(steps):
            return None, None
        name, arguments = steps[index]
        try:
            value = arguments() if callable(arguments) else arguments
        except (KeyError, IndexError, TypeError) as error:
            errors.append(f"{name}: {error}: {replies}")
            return None, None
        return name, {"scope_id": scope_id, **value}

    gateway.choose_tool = choose
    try:
        with (
            serving(app, startup_diagnostic=tmp_path / "server-startup.json") as (_, endpoint, _),
            httpx.Client(base_url=endpoint, timeout=10) as client,
        ):
            scope = client.post(
                "/v1/scopes",
                json={"title": "Kimi workflows", "summary": "Native acceptance", "idempotency_key": "kimi-workflows"},
            )
            scope.raise_for_status()
            scope_id = scope.json()["scope_id"]
            config = home / "config.toml"
            config.write_text(
                'default_model = "fixture"\ntelemetry = false\nauto_session_title = false\nbuiltin_product_skills = false\n'
                '[providers.fixture]\ntype = "openai"\napi_key = "synthetic"\n'
                f'base_url = "http://127.0.0.1:{gateway.server_port}/v1"\n'
                '[models.fixture]\nprovider = "fixture"\nmodel = "kimi-fixture"\nmax_context_size = 131072\ncapabilities = ["tool_use"]\n'
                '[[permission.rules]]\ndecision = "allow"\npattern = "mcp__plugin-powercontext_powercontext__*"\n',
                encoding="utf-8",
            )

            def invoke(plan, prompt, *, cwd=workspace, expected_scope=None):
                steps[:] = plan
                replies.clear()
                errors.clear()
                start = len(gateway.requests)
                result = subprocess.run(
                    [*command, "-p", prompt, "--output-format", "stream-json"],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    timeout=60,
                    check=False,
                )
                assert result.returncode == 0, result.stderr[-3000:] + result.stdout[-3000:]
                assert not errors, errors
                assert "PC_KIMI_OK" in result.stdout, result.stdout[-4000:]
                assert len(replies) == len(plan), (replies, result.stdout[-4000:])
                installed_manifest = home / "plugins/managed/powercontext/kimi.plugin.json"
                enabled = json.loads(installed_manifest.read_text(encoding="utf-8"))["mcpServers"]["powercontext"][
                    "enabledTools"
                ]
                visible = {
                    tool["function"]["name"].rsplit("__", 1)[-1]
                    for request in gateway.requests[start:]
                    for tool in request.get("tools", [])
                    if tool["function"]["name"].startswith("mcp__")
                }
                # Kimi hashes long qualified tool names to meet provider limits.
                assert len(visible) == len(enabled)
                for request in gateway.requests[start:]:
                    for obj in _objects(request["messages"]):
                        if obj.get("schema") == "powercontext.kimi-code.request-binding.v1":
                            assert obj["scope_id"] == (expected_scope or scope_id)
                return list(replies)

            install_plugin(
                "kimi-code", source=str(REPOSITORY), server_url=endpoint, scope_id=scope_id, profile="recommended"
            )
            diagnostics = KimiInstaller().verify(mcp=True, cwd=workspace)
            assert diagnostics["mcp_discovery"].ok, diagnostics
            doctor = CliRunner().invoke(
                create_cli(), ["doctor", "kimi-code", "--mcp", "--cwd", str(workspace), "--json"]
            )
            assert doctor.exit_code == 0, doctor.output
            assert json.loads(doctor.output)["hosts"]["kimi-code"]["checks"]["mcp_discovery"]["status"] == "ok"
            claim = {
                "text": "Contract tests passed in this controlled acceptance.",
                "basis": "declared",
                "evidence": [],
            }
            handoff = {
                "schema": "powercontext.current-work-handoff.v1",
                "trust": "untrusted_input",
                "objective": "Verify Kimi workflows",
                "state": [claim],
                "disposition": "continuable",
                "next_action": {**claim, "text": "Inspect the exact references."},
                "omissions": [],
            }
            result = invoke(
                [
                    (
                        "create_work_contract",
                        {
                            "source_id": "workflow-contract",
                            "contract": {
                                "schema": "powercontext.work-contract.v1",
                                "trust": "untrusted_input",
                                "objective": "Verify Kimi workflows",
                                "facts": [],
                                "in_scope": ["Native MCP workflows"],
                                "exclusions": [],
                                "completion_criteria": ["Exact references survive continuation"],
                                "authorization_notes": [],
                                "open_questions": [],
                            },
                        },
                    ),
                    ("handoff_current_work", {"source_id": "temporary-handoff", "handoff": handoff}),
                    ("continue_handoff", lambda: {"selection": "prepared", "prepared": replies[1]["handoff"]}),
                ],
                "Prepare a temporary work handoff and inspect it without committing a durable milestone.",
            )
            assert result[2]["content"] == result[1]["handoff"]["content"]
            state["temporary"] = result[1]["handoff"]
            artifacts = client.get(f"/v1/scopes/{scope_id}/artifacts/handoff")
            artifacts.raise_for_status()
            assert artifacts.json()["items"] == []
            result = invoke(
                [
                    ("handoff_current_work", {"source_id": "durable-handoff", "handoff": handoff}),
                    ("commit_handoff", lambda: {"handoff": replies[0]["handoff"]}),
                ],
                "Explicitly commit a durable work handoff milestone.",
            )
            state["committed"] = result[1]
            result = invoke(
                [
                    ("continue_handoff", {"selection": "exact", "revision": state["committed"]["reference"]}),
                    (
                        "acknowledge_handoff",
                        {
                            "source_id": "receiver",
                            "receiver": "kimi-new-session",
                            "selection": "exact",
                            "revision": state["committed"]["reference"],
                            "status": "accepted",
                            "receiver_checks": {
                                "live_state": "confirmed",
                                "capability": "confirmed",
                                "authorization": "confirmed",
                            },
                        },
                    ),
                    (
                        "record_task_outcome",
                        lambda: {
                            "source_id": "outcome",
                            "outcome": {
                                "schema": "powercontext.task-outcome.v1",
                                "trust": "untrusted_observation",
                                "objective": "Verify Kimi workflows",
                                "status": "succeeded",
                                "summary": "Verified exact handoff continuity",
                                "handoff_receipt_ref": replies[1]["receipt"]["source"],
                                "observations": [claim],
                                "checks": [],
                                "produced_artifacts": [state["committed"]["reference"]],
                                "remaining_work": [],
                            },
                        },
                    ),
                ],
                "Continue the exact committed handoff, confirm receipt and record the completed task outcome.",
            )
            assert result[0]["selected_revision"] == state["committed"]["reference"]
            assert result[1]["resolution"]["selected_revision"] == state["committed"]["reference"]
            assert result[2]["kind"] == "task-outcome"
            outcome_ref = result[2]["source"]
            saved_outcome = client.get(
                f"/v1/scopes/{scope_id}/sources/{outcome_ref['name']}/{outcome_ref['source_id']}"
            )
            saved_outcome.raise_for_status()
            outcome_content = saved_outcome.json()["content"]
            if isinstance(outcome_content, str):
                outcome_content = json.loads(outcome_content)
            receipt_ref = result[1]["receipt"]["source"]
            assert outcome_content["handoff_receipt_ref"] == {
                "source_type": receipt_ref["name"],
                "source_id": receipt_ref["source_id"],
            }
            assert outcome_content["produced_artifacts"] == [state["committed"]["reference"]]

            install_plugin("kimi-code", source=str(REPOSITORY), profile="full")
            assert KimiInstaller().verify(mcp=True, cwd=workspace)["mcp_discovery"].ok
            proposal = {
                "situation": "API changes",
                "action": "Run contract tests",
                "outcome": "Tests pass",
                "lesson": "Verify exact contracts",
            }
            result = invoke(
                [
                    (
                        "capture_content_source",
                        {"source_id": "workflow-evidence", "content": "Contract tests pass after regeneration."},
                    ),
                    (
                        "propose_experience",
                        lambda: {
                            "proposal": proposal,
                            "source_refs": [replies[0]["source"]],
                            "artifact_refs": [],
                            "reason": "User-requested reusable lesson",
                        },
                    ),
                    ("get_candidate", lambda: {"candidate_id": replies[1]["candidate_id"]}),
                    (
                        "revise_candidate",
                        lambda: {
                            "candidate_id": replies[2]["candidate_id"],
                            "expected_version": replies[2]["version"],
                            "proposal": {**proposal, "lesson": "Regenerate before verifying exact contracts"},
                            "source_refs": [replies[0]["source"]],
                            "artifact_refs": [],
                        },
                    ),
                ],
                "Propose and inspect an Experience, then explicitly revise its lesson.",
            )
            state["candidate"] = result[3]
            state["evidence"] = result[0]["source"]
            # A stale decision must fail through the real native MCP transport. Inspect it below before any retry.
            steps[:] = [
                (
                    "approve_candidate",
                    {"candidate_id": state["candidate"]["candidate_id"], "expected_version": result[2]["version"]},
                )
            ]
            replies.clear()
            errors.clear()
            stale = subprocess.run(
                [
                    *command,
                    "-p",
                    "Attempt the approved decision at the previously inspected version.",
                    "--output-format",
                    "stream-json",
                ],
                cwd=workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=45,
                check=False,
            )
            assert stale.returncode == 0
            assert errors and any("409" in item or "conflict" in item.lower() for item in errors), (
                errors,
                stale.stdout[-3000:],
            )
            inspected = client.post(
                "/v1/candidates/get", json={"scope_id": scope_id, "candidate_id": state["candidate"]["candidate_id"]}
            )
            inspected.raise_for_status()
            assert inspected.json()["status"] == "pending"
            result = invoke(
                [
                    ("get_candidate", {"candidate_id": state["candidate"]["candidate_id"]}),
                    (
                        "approve_candidate",
                        lambda: {"candidate_id": replies[0]["candidate_id"], "expected_version": replies[0]["version"]},
                    ),
                    ("get_experience", lambda: {"artifact": replies[1]["result_artifact"]}),
                    (
                        "propose_skill",
                        lambda: {
                            "proposal": {
                                "name": "verify-contract",
                                "description": "Use when changing an API",
                                "instructions": "Regenerate and run contract tests",
                                "validation": ["Contract tests pass"],
                            },
                            "source_refs": [state["evidence"]],
                            "artifact_refs": [replies[1]["result_artifact"]],
                            "reason": "User-requested procedure",
                        },
                    ),
                    (
                        "approve_candidate",
                        lambda: {"candidate_id": replies[3]["candidate_id"], "expected_version": replies[3]["version"]},
                    ),
                    ("get_skill", lambda: {"artifact": replies[4]["result_artifact"]}),
                    ("list_managed_skills", {}),
                ],
                "Reinspect the revised candidate; explicitly approve that version, read it, then propose and approve the requested Skill and read it back.",
            )
            assert result[2]["artifact"] == result[1]["result_artifact"]
            assert result[5]["artifact"] == result[4]["result_artifact"]
            assert result[6]["skills"]
            result = invoke(
                [
                    ("scan_external_skills", {}),
                    ("list_external_skills", {}),
                    (
                        "resolve_external_skill",
                        lambda: {
                            key: replies[0]["registrations"][0][key] for key in ("external_skill_id", "fingerprint")
                        },
                    ),
                    (
                        "import_external_skill",
                        lambda: {
                            "mode": "import",
                            **{
                                key: replies[0]["registrations"][0][key] for key in ("external_skill_id", "fingerprint")
                            },
                        },
                    ),
                ],
                "Inspect the external Skill registration and explicitly import its exact fingerprint as a pending candidate.",
            )
            assert result[2]["status"] == "available"
            assert result[3]["status"] == "pending"
            assert result[3]["candidate"]["result_artifact"] is None
            # The full toolset must follow the same workspace route as the automatic Hook.
            other_workspace = tmp_path / "second workspace"
            other_workspace.mkdir()
            other = client.post(
                "/v1/scopes",
                json={"title": "Other Kimi project", "summary": "Isolated route", "idempotency_key": "kimi-other"},
            )
            other.raise_for_status()
            other_scope = other.json()["scope_id"]
            installed = install_plugin("kimi-code", source=str(REPOSITORY), workspace_scope=True)
            scope_script = Path(installed["plugin_path"]) / "scripts/scope.mjs"
            for cwd, target in ((workspace, scope_id), (other_workspace, other_scope)):
                bound = subprocess.run(
                    [node, str(scope_script), "bind", "--cwd", str(cwd), "--scope-id", target],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    check=True,
                    timeout=6,
                )
                assert json.loads(bound.stdout)["scope_id"] == target
            result = invoke(
                [
                    ("remember_memory", {"kind": "fact", "text": "Full profile belongs to the second workspace."}),
                    ("get_artifact_revision", lambda: replies[0]["records"][0]["artifact"]),
                ],
                "Save the second project's decision and read its exact Artifact back.",
                cwd=other_workspace,
                expected_scope=other_scope,
            )
            assert result[1]["scope_id"] == other_scope
            isolated = client.post(
                "/v1/memory/search", json={"scope_id": scope_id, "query": "second workspace", "mode": "fts"}
            )
            isolated.raise_for_status()
            assert "Full profile belongs to the second workspace." not in isolated.text
            install_plugin("kimi-code", source=str(REPOSITORY), scope_id=scope_id)
            result = invoke(
                [
                    (
                        "remember_memory",
                        {"kind": "fact", "text": "Explicit Scope overrides the second workspace binding."},
                    ),
                    ("get_artifact_revision", lambda: replies[0]["records"][0]["artifact"]),
                ],
                "Save this decision in the explicitly configured Scope and read it back.",
                cwd=other_workspace,
            )
            assert result[1]["scope_id"] == scope_id
            # Host approvals and profile filtering must not become successful writes.
            config_text = config.read_text(encoding="utf-8")
            deny_script = tmp_path / "deny-memory.mjs"
            deny_script.write_text(
                "process.stderr.write('Kimi acceptance denied this tool'); process.exit(2);\n", encoding="utf-8"
            )
            config.write_text(
                config_text
                + '\n[[hooks]]\nevent = "PreToolUse"\nmatcher = "remember_memory$"\ncommand = '
                + json.dumps(f'node "{deny_script.as_posix()}"')
                + "\ntimeout = 5\n",
                encoding="utf-8",
            )
            steps[:] = [("remember_memory", {"kind": "fact", "text": "This denied memory must not exist."})]
            replies.clear()
            errors.clear()
            denied = subprocess.run(
                [*command, "-p", "Save this memory explicitly.", "--output-format", "stream-json"],
                cwd=workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=45,
                check=False,
            )
            assert denied.returncode == 0
            assert "Kimi acceptance denied this tool" in denied.stdout or any(
                "Kimi acceptance denied this tool" in item for item in errors
            ), (errors, denied.stdout)
            stored = client.post(
                "/v1/memory/search", json={"scope_id": scope_id, "query": "denied memory", "mode": "fts"}
            )
            stored.raise_for_status()
            assert "This denied memory must not exist." not in stored.text
            config.write_text(config_text, encoding="utf-8")
            install_plugin("kimi-code", source=str(REPOSITORY), profile="minimal")
            steps[:] = [("handoff_current_work", {"source_id": "unavailable-handoff", "handoff": handoff})]
            replies.clear()
            errors.clear()
            missing = subprocess.run(
                [*command, "-p", "Prepare a temporary work handoff.", "--output-format", "stream-json"],
                cwd=workspace,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=45,
                check=False,
            )
            assert missing.returncode == 0
            assert "PC_KIMI_MISSING_TOOL:handoff_current_work" in missing.stdout
            assert replies == []
            (tmp_path / "evidence.json").write_text(
                json.dumps(
                    {
                        "host_version": subprocess.check_output([*command, "--version"], text=True).strip(),
                        "platform": os.name,
                        "inference": "controlled",
                        "native_profiles": ["recommended", "full"],
                        "cross_session_handoff": True,
                        "exact_receipt_outcome": True,
                        "experience_skill_review": True,
                        "candidate_version_conflict": True,
                        "external_skill_fingerprint": True,
                        "full_workspace_isolation_and_override": True,
                        "missing_and_denied_tools": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
    finally:
        gateway.shutdown()
        thread.join(timeout=5)
        gateway.server_close()
