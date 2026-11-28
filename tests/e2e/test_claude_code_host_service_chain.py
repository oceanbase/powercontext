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

"""Actual headless `claude -p` with the PowerContext plugin against a real PowerContext Server.

`powercontext setup claude-code` installs the plugin from this checkout into an isolated Claude configuration directory
through Claude Code's own Marketplace commands. The host model is a streamed Messages API fake and the Server's
generation model a Chat Completions fake. These cases establish the local Claude Code, plugin, and Server chain and the
plugin's documented behavior; they do not qualify live-model behavior, the interactive TUI, or Claude Desktop.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from contextlib import ExitStack
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import AnyHttpUrl, SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig, RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import BearerAuthConfig, ServerSettings
from tests.e2e.host_models.messages import MessagesModelFixture
from tests.e2e.host_models.script import FINAL_ANSWER
from tests.e2e.zcode_acceptance.protocol import HostModelFixture, ObserveServer, WireEvidence
from tests.e2e.zcode_acceptance.runner import serve
from tests.git_worktree_support import make_git_layout

REPOSITORY = Path(__file__).resolve().parents[2]
TOKEN = "claude-code-host-service-chain-token"  # noqa: S105 - non-secret test credential.
MODEL = "fixture-main"
FACT_MARKER = "amber-341"
FACT = f"In the synthetic heron project, the verification color is {FACT_MARKER}."
QUESTION = "Which verification color does the synthetic heron project use?"


@dataclass
class ClaudeTurn:
    events: list[dict[str, Any]]

    @property
    def init(self) -> dict[str, Any]:
        (init,) = [event for event in self.events if event["type"] == "system" and event.get("subtype") == "init"]
        return init

    @property
    def session_id(self) -> str:
        return self.init["session_id"]

    @property
    def result(self) -> dict[str, Any]:
        (result,) = [event for event in self.events if event["type"] == "result"]
        return result

    def tool_results(self) -> dict[str, dict[str, Any]]:
        """Tool results Claude Code returned to the model, by tool use id."""
        return {
            block["tool_use_id"]: block
            for event in self.events
            if event["type"] == "user"
            for block in event["message"]["content"]
            if isinstance(block, dict) and block.get("type") == "tool_result"
        }


@dataclass
class ClaudeStack:
    executable: str
    workspace: Path
    environment: dict[str, str]
    settings: Path
    log: Path
    plugin_root: Path
    model: MessagesModelFixture
    generation: HostModelFixture
    wire: WireEvidence
    server: httpx.Client

    def run(self, prompt: str, *, resume: str | None = None, **settings: str) -> ClaudeTurn:
        """Run one headless `claude -p` turn, in a new session or resuming `resume`."""
        environment = {**self.environment, **settings}
        command = [
            self.executable,
            "-p",
            "--output-format",
            "stream-json",
            "--verbose",
            "--settings",
            str(self.settings),
        ]
        if resume is not None:
            command += ["--resume", resume]
        command += ["--", prompt]
        try:
            completed = subprocess.run(
                command,
                cwd=self.workspace,
                env=environment,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired as timeout:
            # Keep what Claude Code printed before it hung; CI uploads this log on failure.
            with self.log.open("a", encoding="utf-8") as log:
                log.write(f"$ claude {' '.join(command[1:])}\n{_text(timeout.stdout)}\n{_text(timeout.stderr)}\n")
            raise
        with self.log.open("a", encoding="utf-8") as log:
            log.write(f"$ claude {' '.join(command[1:])}\n{completed.stdout}\n{completed.stderr}\n")
        try:
            events = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith("{")]
        except ValueError:
            pytest.fail(f"claude_invalid_event_exit_{completed.returncode}; see {self.log}", pytrace=False)
        # Claude Code ends a turn whose model request failed, such as the fake refusing a tool it was not offered, with
        # an error result and exit status 1; report the error, not only the status.
        for result in (event for event in events if event["type"] == "result"):
            assert result["is_error"] is False, (
                f"claude_turn_error_{result.get('api_error_status')}: {result.get('result')}; see {self.log}"
            )
        assert completed.returncode == 0, f"claude_exit_{completed.returncode}; see {self.log}"
        turn = ClaudeTurn(events)
        assert turn.result["subtype"] == "success", f"claude_turn_failed; see {self.log}"
        assert turn.result["result"] == FINAL_ANSWER, f"claude_turn_unfinished; see {self.log}"
        return turn

    def allow(self, *rules: str) -> None:
        """Replace the test-only permission rules that every later turn loads."""
        self.settings.write_text(json.dumps({"permissions": {"allow": list(rules)}}), encoding="utf-8")

    def resolver_command(self) -> str:
        """The plugin's documented host-binding resolver, as the bundled Skill tells Claude to run it."""
        return f"python3 {self.plugin_root / 'scripts' / 'workspace_scope.py'} --cwd {self.workspace}"

    def latest_tool_result(self) -> str:
        """The text of the last tool result in the model's latest request."""
        return list(self.model.tool_results(self.model.requests[-1]).values())[-1].strip()

    def create_scope(self, title: str) -> str:
        response = self.server.post(
            "/v1/scopes",
            json={"title": title, "summary": "Disposable Claude Code host acceptance.", "idempotency_key": title},
        )
        response.raise_for_status()
        return response.json()["scope_id"]

    def bind_workspace(self, scope_id: str) -> None:
        # The plugin keys a workspace by the SHA-256 of its resolved Git root.
        external_id = hashlib.sha256(os.fsencode(self.workspace.resolve())).hexdigest()
        response = self.server.put(
            "/v1/scope-bindings",
            json={
                "key": {"integration": "claude-code", "kind": "workspace", "external_id": external_id},
                "scope_id": scope_id,
            },
        )
        response.raise_for_status()

    def remember(self, scope_id: str, text: str) -> str:
        response = self.server.post(
            "/v1/memory/remember", json={"scope_id": scope_id, "kind": "decision", "text": text}
        )
        response.raise_for_status()
        return response.json()["records"][0]["artifact"]["artifact_id"]

    def flush(self, scope_id: str) -> None:
        """Process every captured Source, as the Server's schedule would; this stack runs none."""
        for _ in range(8):
            response = self.server.post("/v1/memory/flush", json={"scope_id": scope_id})
            response.raise_for_status()
            result = response.json()
            if result["current_cursor"] >= result["high_watermark"] and not result.get("remaining_work"):
                return
        raise AssertionError("memory_flush_did_not_drain")

    def memories(self, scope_id: str) -> dict[str, dict[str, Any]]:
        response = self.server.post("/v1/memory/entries/list", json={"scope_id": scope_id})
        response.raise_for_status()
        return {entry["text"]: entry for entry in response.json()["entries"]}

    def sources(self, scope_id: str) -> list[dict[str, Any]]:
        response = self.server.get(f"/v1/scopes/{scope_id}/sources")
        response.raise_for_status()
        return response.json()["items"]

    def records(self, path: str) -> list[dict[str, Any]]:
        return [record for record in self.wire.records if record["path"] == path]


@pytest.fixture
def claude_stack(tmp_path: Path) -> Iterator[ClaudeStack]:
    executable = os.environ.get("CLAUDE_CODE_CLI_BIN")
    if not executable:
        pytest.skip("Set CLAUDE_CODE_CLI_BIN to a real Claude Code CLI; CI runs this in the claude-code-host job")
    assert Path(executable).is_file(), "CLAUDE_CODE_CLI_BIN must name an existing Claude Code CLI"
    workspace = tmp_path / "workspace"
    home = tmp_path / "home"
    for directory in (workspace, home / ".claude", home / "config", home / "data", home / "cache", home / "state"):
        directory.mkdir(parents=True)
    # The plugin keys workspace bindings by Git root.
    subprocess.run(["git", "init", "--quiet", str(workspace)], check=True)  # noqa: S607 - Git from PATH.
    model = MessagesModelFixture(MODEL)
    generation = HostModelFixture()
    wire = WireEvidence()
    with ExitStack() as stack:
        model_url = stack.enter_context(serve(model.app))
        generation_url = stack.enter_context(serve(generation.app))
        settings = ServerSettings(
            _env_file=None,  # ty: ignore[unknown-argument] BaseSettings runtime option.
            workspace=workspace,
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'server.db'}"),
            auth=BearerAuthConfig(enabled=True, token=SecretStr(TOKEN)),
            # No memory schedule: captured prompts become Memory only when a case flushes them.
            runtime=RuntimeConfig(artifact_processing_families=("memory",)),
            inference=InferenceConfig(
                generation_model="openai-chat:memory-fixture",
                generation_base_url=AnyHttpUrl(generation_url + "/v1"),
                generation_headers={"Authorization": SecretStr("Bearer synthetic-generation-only")},
            ),
        )
        app = create_server_app(settings=settings)
        base_url = stack.enter_context(serve(ObserveServer(app, wire)))
        server = stack.enter_context(
            httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=15)
        )
        environment = _isolated_environment(home, tmp_path, model_url)
        log = tmp_path / "claude.log"
        plugin_root = _install_plugin(executable, environment, base_url, tmp_path, log)
        claude = ClaudeStack(
            executable,
            workspace,
            environment,
            tmp_path / "permissions.json",
            log,
            plugin_root,
            model,
            generation,
            wire,
            server,
        )
        claude.allow()
        yield claude


def _text(output: str | bytes | None) -> str:
    return output.decode(errors="replace") if isinstance(output, bytes) else output or ""


def _isolated_environment(home: Path, tmp_path: Path, model_url: str) -> dict[str, str]:
    # Inherited Claude Code variables would reattach to the session running these tests, an inherited
    # `POWERCONTEXT_CLAUDE_AUTHORIZATION` would let the hooks skip the credential setup saves and MCP fall back to its
    # static header, and proxies would route the loopback Server and model traffic away from this host.
    prefixes = ("POWERCONTEXT_", "CLAUDE", "ANTHROPIC_", "OTEL_", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.upper().startswith(prefixes) and not name.upper().endswith("_PROXY")
    }
    # Claude Code runs the hooks with the `python3` on PATH; keep this test run's virtual environment off it, so they
    # run with the runner's own Python as they would for a user.
    test_bin = Path(sys.executable).parent
    path = [entry for entry in environment.get("PATH", "").split(os.pathsep) if entry and Path(entry) != test_bin]
    environment.update(
        PATH=os.pathsep.join(path),
        HOME=str(home),
        USERPROFILE=str(home),
        CLAUDE_CONFIG_DIR=str(home / ".claude"),
        XDG_CONFIG_HOME=str(home / "config"),
        XDG_DATA_HOME=str(home / "data"),
        XDG_CACHE_HOME=str(home / "cache"),
        XDG_STATE_HOME=str(home / "state"),
        POWERCONTEXT_CLIENT_CONFIG_FILE=str(tmp_path / "clients.json"),
        ANTHROPIC_BASE_URL=model_url,
        ANTHROPIC_API_KEY="synthetic-only",
        # Requests for the main model follow the script; any other model a side request names gets a plain answer.
        ANTHROPIC_MODEL=MODEL,
        ANTHROPIC_SMALL_FAST_MODEL="fixture-small",
        ANTHROPIC_DEFAULT_HAIKU_MODEL="fixture-small",
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
        DISABLE_AUTOUPDATER="1",
        # A loaded runner must not turn a slow Server reply into a silently skipped preparation or capture. The budget
        # leaves the hook time to start within its 10 s timeout, so a slow Server still ends in the plugin's own
        # fail-open path rather than a killed hook.
        POWERCONTEXT_CLAUDE_REQUEST_TIMEOUT_SECONDS="5",
        POWERCONTEXT_CLAUDE_HTTP_BUDGET_SECONDS="7",
    )
    return environment


def _install_plugin(executable: str, environment: dict[str, str], base_url: str, tmp_path: Path, log: Path) -> Path:
    """Run `powercontext setup claude-code` from this checkout, with the Claude Code under test first on PATH."""
    # Setup runs the first `claude` on PATH; link the executable under test under that name.
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    (bin_directory / "claude").symlink_to(executable)
    setup_environment = dict(environment)
    setup_environment["PATH"] = os.pathsep.join((str(bin_directory), environment.get("PATH", "")))
    setup_environment["PYTHONPATH"] = str(REPOSITORY / "src")
    # Only setup receives the client credential, and it saves the credential for this Server under
    # `$CLAUDE_CONFIG_DIR/powercontext/credentials.json`. `claude` runs without the variable: the hooks read the saved
    # credential, and MCP gets it from the plugin's `headersHelper`.
    setup_environment["POWERCONTEXT_CLAUDE_AUTHORIZATION"] = f"Bearer {TOKEN}"
    script = (
        "import json, sys; from powercontext.cli.installation import setup; "
        "print(json.dumps(setup('claude-code', source=sys.argv[1], server_url=sys.argv[2])))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(REPOSITORY), base_url],
        cwd=tmp_path,
        env=setup_environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    log.write_text(f"$ powercontext setup claude-code\n{completed.stdout}\n{completed.stderr}\n", encoding="utf-8")
    assert completed.returncode == 0, f"powercontext_setup_claude_code_failed; see {log}"
    report = json.loads(completed.stdout.strip().splitlines()[-1])
    assert report["authorization_state"] == "configured", f"powercontext_setup_saved_no_credential; see {log}"
    return Path(report["cache_dir"])


def _mcp_tool(turn: ClaudeTurn, name: str) -> str:
    """The name Claude Code offers a PowerContext MCP tool under."""
    (tool,) = [tool for tool in turn.init["tools"] if tool.startswith("mcp__") and tool.endswith("__" + name)]
    return tool


def test_claude_code_prompt_is_recalled_in_a_later_session(claude_stack: ClaudeStack) -> None:
    scope_id = claude_stack.create_scope("Claude Code recall")
    claude_stack.bind_workspace(scope_id)
    credential = "sk-claudee2ecredential0123456789"
    first = claude_stack.run(FACT)
    claude_stack.run(f"Deploy the synthetic heron project with {credential} today.", resume=first.session_id)
    # The credential prompt reached the model but was not captured.
    assert [item["content"] for item in claude_stack.sources(scope_id)] == [FACT]
    assert claude_stack.records("/v1/sources/content")[0]["request"]["metadata"]["session_id"] == first.session_id
    claude_stack.flush(scope_id)
    assert FACT in claude_stack.memories(scope_id)

    start = len(claude_stack.model.requests)
    claude_stack.run(QUESTION)
    prepared = claude_stack.records("/v1/context/prepare")[-1]["response"]
    assert prepared["status"] == "ready"
    assert FACT_MARKER in prepared["content"]
    # The new session's first model request carries the prepared context unchanged, after the user's prompt.
    sent = claude_stack.model.texts(claude_stack.model.requests[start])
    assert 0 <= sent.find(QUESTION) < sent.find(prepared["content"])
    assert 0 <= sent.find("PowerContext task outcome guidance") < sent.find(prepared["content"])
    assert "actually complete or explicitly interrupted" in sent
    assert "intermediate turns" in sent

    queries = [record["request"]["query"] for record in claude_stack.records("/v1/context/prepare")]
    assert queries == [FACT, "Deploy the synthetic heron project with [REDACTED] today.", QUESTION]
    bootstraps = [record["request"]["lifecycle"] for record in claude_stack.records("/v1/context/bootstrap")]
    assert bootstraps == ["startup", "resume", "startup"]
    server_side = [claude_stack.wire.arrivals, claude_stack.wire.records, claude_stack.generation.generation_requests]
    assert credential not in json.dumps(server_side)


def test_claude_code_bootstrap_context_reaches_the_first_model_request(claude_stack: ClaudeStack) -> None:
    scope_id = claude_stack.create_scope("Claude Code bootstrap")
    claude_stack.bind_workspace(scope_id)
    bootstrap = "The synthetic heron project always runs its release checklist before tagging."
    artifact_id = claude_stack.remember(scope_id, bootstrap)
    path = f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{artifact_id}/tags"
    tags = claude_stack.server.get(path)
    tags.raise_for_status()
    claude_stack.server.put(
        path, json={"tags": ["bootstrap-context"]}, headers={"If-Match": tags.headers["ETag"]}
    ).raise_for_status()

    prompt = "Start the synthetic heron release."
    claude_stack.run(prompt, POWERCONTEXT_CLAUDE_BOOTSTRAP_CONTEXT="true")
    # The startup package reaches the session's first model request unchanged, and the turn's own preparation does
    # not repeat it. Claude Code 2.1 places SessionStart context after the prompt, so its position is not asserted.
    package = claude_stack.records("/v1/context/bootstrap")[-1]["response"]["content"]
    assert bootstrap in package
    sent = claude_stack.model.texts(claude_stack.model.requests[-1])
    assert (sent.count(package), sent.count(bootstrap)) == (1, 1)
    receipts = claude_stack.records("/v1/context/bootstrap/receipts")
    assert [record["request"]["outcome"] for record in receipts] == ["injected"]
    prepared = claude_stack.records("/v1/context/prepare")[-1]
    assert prepared["request"]["bootstrap_receipt_id"] == receipts[0]["request"]["receipt_id"]
    assert prepared["response"]["status"] == "empty"

    # Bootstrap context is opt-in; without it only prompt-time recall supplies the Memory, after the prompt.
    claude_stack.run(prompt)
    sent = claude_stack.model.texts(claude_stack.model.requests[-1])
    assert 0 <= sent.find(prompt) < sent.find(bootstrap)
    assert "BEGIN_POWERCONTEXT_BOOTSTRAP_CONTEXT_V1" not in sent
    assert "bootstrap_receipt_id" not in claude_stack.records("/v1/context/prepare")[-1]["request"]


def test_claude_code_memory_tools_write_to_the_workspace_scope_after_approval(claude_stack: ClaudeStack) -> None:
    scope_id = claude_stack.create_scope("Claude Code tools")
    claude_stack.bind_workspace(scope_id)
    decision = "The synthetic osprey project uses jade-806 as its verification color."
    resolver = claude_stack.resolver_command()
    # Only the plugin's Scope resolver is approved; the PowerContext MCP tools still need the user's approval.
    claude_stack.allow(f"Bash({resolver})")
    # As the bundled Skill directs, the model resolves the workspace Scope first and passes it to the Memory tools.
    resolve = ("Bash", {"command": resolver, "description": "Resolve the PowerContext Scope"})
    remember = (
        "remember_memory",
        lambda: {"scope_id": claude_stack.latest_tool_result(), "kind": "decision", "text": decision},
    )
    claude_stack.model.plan([resolve, remember])
    declined = claude_stack.run("Save the osprey verification color decision.")
    remember_tool = _mcp_tool(declined, "remember_memory")
    search_tool = _mcp_tool(declined, "search_memory")
    mcp_servers = {server["name"]: server["status"] for server in declined.init["mcp_servers"]}
    assert mcp_servers == {"plugin:powercontext:powercontext": "connected"}
    # Without approval `claude -p` refuses the write, so nothing reaches the Server.
    assert [denial["tool_name"] for denial in declined.result["permission_denials"]] == [remember_tool]
    assert claude_stack.wire.calls("remember_memory") == []
    assert claude_stack.memories(scope_id) == {}

    claude_stack.allow(f"Bash({resolver})", remember_tool, search_tool)
    search = ("search_memory", {"scope_id": scope_id, "query": "osprey verification color", "mode": "fts"})
    claude_stack.model.plan([resolve, remember, search])
    approved = claude_stack.run("Yes, save it and check that it is there.", resume=declined.session_id)
    assert approved.result["permission_denials"] == []
    assert not any(result.get("is_error") for result in approved.tool_results().values())
    assert decision in claude_stack.memories(scope_id)
    found = claude_stack.wire.result("search_memory")
    assert decision in json.dumps(found)


def test_claude_code_linked_worktree_falls_back_to_its_checkout_binding(
    claude_stack: ClaudeStack, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = make_git_layout(tmp_path / "git", monkeypatch)
    worktree = replace(claude_stack, workspace=layout.worktree)
    checkout_scope = claude_stack.create_scope("Claude Code checkout")
    replace(claude_stack, workspace=layout.checkout).bind_workspace(checkout_scope)
    worktree_scope = claude_stack.create_scope("Claude Code worktree")

    def sources(scope_id: str) -> list[str]:
        return [item["content"] for item in claude_stack.sources(scope_id)]

    def scope_script(*arguments: str) -> str:
        """Run the plugin's workspace Scope script in the worktree, as the guide's binding command does."""
        script = claude_stack.plugin_root / "scripts" / "workspace_scope.py"
        completed = subprocess.run(
            ["python3", str(script), "--cwd", str(layout.worktree), *arguments],  # noqa: S607 - the hooks' Python.
            env=claude_stack.environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return completed.stdout.strip()

    # Until the worktree has a binding of its own, its prompts are captured in its checkout's Scope.
    unbound = "Synthetic heron prompt from the unbound worktree."
    worktree.run(unbound)
    assert sources(checkout_scope) == [unbound]

    # Binding in the worktree binds the worktree itself, and its own binding wins over the checkout's.
    assert scope_script("--bind-scope", worktree_scope) == worktree_scope
    bound = "Synthetic heron prompt from the bound worktree."
    worktree.run(bound)
    assert sources(worktree_scope) == [bound]

    # Clearing the worktree's binding leaves the checkout's in place, so the worktree falls back to it again.
    assert scope_script("--clear-scope") == checkout_scope
    cleared = "Synthetic heron prompt from the cleared worktree."
    worktree.run(cleared)
    assert sources(checkout_scope) == [unbound, cleared]
