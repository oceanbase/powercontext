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

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from types import SimpleNamespace
from typing import Any

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "integrations" / "antigravity" / "plugins" / "powercontext"
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "recorded_antigravity_payloads.json"
_HOOKS_DIR = _PLUGIN_ROOT / "hooks"
for _path in (_PLUGIN_ROOT, _HOOKS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import antigravity_host  # noqa: E402
import bind_tools  # noqa: E402
import hook  # noqa: E402
from powercontext_lifecycle.scope_binding_errors import ScopeBindingStatusError  # noqa: E402
from settings import (  # noqa: E402
    PRE_TOOL_USE_HOST_TIMEOUT_SECONDS,
    AntigravityPluginSettings,
    host_bounded_http_budget,
)


def _settings(
    *, bootstrap_context: bool = False, prompt_recall: bool = True, capture_prompts: bool = True
) -> AntigravityPluginSettings:
    return AntigravityPluginSettings(
        server_url="http://127.0.0.1:8000",
        bootstrap_context=bootstrap_context,
        prompt_recall=prompt_recall,
        capture_prompts=capture_prompts,
    )


def _start_scripted_server(
    recorded_requests: list[tuple[str, dict[str, object]]],
) -> tuple[ThreadingHTTPServer, Thread]:
    class Handler(BaseHTTPRequestHandler):
        def _handle(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            recorded_requests.append((self.path, body))
            status = 200
            if self.path in {"/v1/scope-bindings/resolve", "/v1/scope-bindings"}:
                result: dict[str, object] = {"scope_id": "scp_char_123"}
            elif self.path == "/v1/context/prepare":
                text = "Retained server trust policy and historical context."
                result = {
                    "schema": "powercontext.prepared-context.v1",
                    "status": "ready",
                    "content": text,
                    "content_bytes": len(text.encode("utf-8")),
                }
            elif self.path == "/v1/sources/content":
                status = 202
                result = {
                    "status": "accepted",
                    "source": {"name": "content", "source_id": body["source_id"]},
                    "position": 1,
                }
            else:
                self.send_error(404)
                return
            raw = json.dumps(result).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:
            self._handle()

        def do_PUT(self) -> None:
            self._handle()

        def log_message(self, format: str, *_args: object) -> None:  # noqa: A002
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def test_resolve_cwd_prioritizes_workspace_paths_and_never_falls_back_to_getcwd(tmp_path: Path) -> None:
    uri = (tmp_path / "my repo").as_uri()
    assert antigravity_host.resolve_cwd({"workspacePaths": [uri, "/other"], "cwd": "/fallback"}) == str(
        tmp_path / "my repo"
    )
    assert antigravity_host.resolve_cwd({"cwd": str(tmp_path)}) == str(tmp_path)
    assert antigravity_host.resolve_cwd({}) is None


def test_settings_defaults_bootstrap_false_and_native_prompt_recall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    native_cfg = tmp_path / "powercontext.json"
    native_cfg.write_text(
        json.dumps({"server_url": "http://127.0.0.1:8000", "allow_insecure_http": False, "prompt_recall": False}),
        encoding="utf-8",
    )
    monkeypatch.setattr("settings._PLUGIN_SETTINGS_PATH", native_cfg)
    for var in (
        "POWERCONTEXT_ANTIGRAVITY_BOOTSTRAP_CONTEXT",
        "POWERCONTEXT_ANTIGRAVITY_PROMPT_RECALL",
        "POWERCONTEXT_ANTIGRAVITY_SERVER_URL",
    ):
        monkeypatch.delenv(var, raising=False)

    loaded = AntigravityPluginSettings.from_environment()
    assert loaded.bootstrap_context is False
    assert loaded.prompt_recall is False

    monkeypatch.setenv("POWERCONTEXT_ANTIGRAVITY_PROMPT_RECALL", "true")
    overridden = AntigravityPluginSettings.from_environment()
    assert overridden.prompt_recall is True


def test_session_start_emits_inject_steps_single_line_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def _fake_run(_host: object, _event: object, *, connect: object, emit: object) -> None:
        print('{"diagnostic":"should_not_leak_to_stdout"}', file=sys.stderr)
        emit("Bootstrap context body")

    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path))
    monkeypatch.setattr(hook, "run_session_start", _fake_run)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO(json.dumps({"conversationId": "abc", "workspacePaths": [str(tmp_path)]}))
    )
    monkeypatch.setattr(sys, "argv", ["hook.py", "session-start"])
    monkeypatch.setattr(
        hook.AntigravityPluginSettings, "from_environment", classmethod(lambda cls: _settings(bootstrap_context=True))
    )

    assert hook.main() == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    msg = parsed["injectSteps"][0]["systemMessage"]["systemMessage"]
    assert '<powercontext-context source="session-start">' in msg
    assert "Bootstrap context body" in msg


def test_pre_invocation_dedup_wakeup_and_inject_steps(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "\n".join([
            json.dumps({
                "step_index": 0,
                "type": "USER_INPUT",
                "source": "USER_EXPLICIT",
                "content": "<USER_REQUEST>hello</USER_REQUEST>",
            }),
            json.dumps({"step_index": 1, "type": "PLANNER_RESPONSE", "source": "MODEL", "response": "hi"}),
        ])
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path))
    monkeypatch.setattr(hook.AntigravityPluginSettings, "from_environment", classmethod(lambda cls: _settings()))

    # 1. invocationNum > 0 -> no-op
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "conversationId": "c1",
                "invocationNum": 1,
                "lastUserInput": "hello",
                "workspacePaths": [str(tmp_path)],
                "transcriptPath": str(transcript),
            })
        ),
    )
    monkeypatch.setattr(sys, "argv", ["hook.py", "pre-invocation"])
    assert hook.main() == 0
    assert json.loads(capsys.readouterr().out) == {}

    # 2. wakeup after MODEL step with same lastUserInput -> no-op
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "conversationId": "c1",
                "invocationNum": 0,
                "initialNumSteps": 2,
                "lastUserInput": "hello",
                "workspacePaths": [str(tmp_path)],
                "transcriptPath": str(transcript),
            })
        ),
    )
    assert hook.main() == 0
    assert json.loads(capsys.readouterr().out) == {}

    # 3. new lastUserInput differing from recorded step -> injects prepared context
    monkeypatch.setattr(
        hook,
        "run_user_prompt_submit",
        lambda *_a, **_kw: SimpleNamespace(scope_id="scp_1", context="Recalled decision", diagnostics=[]),
    )
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "conversationId": "c1",
                "invocationNum": 0,
                "initialNumSteps": 2,
                "lastUserInput": "<USER_REQUEST>new question</USER_REQUEST>",
                "workspacePaths": [str(tmp_path)],
                "transcriptPath": str(transcript),
            })
        ),
    )
    assert hook.main() == 0
    parsed = json.loads(capsys.readouterr().out)
    assert (
        '<powercontext-context source="pre-invocation">' in parsed["injectSteps"][0]["systemMessage"]["systemMessage"]
    )


def test_bind_tools_rewrites_lazy_and_eager_and_ignores_other_servers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    monkeypatch.setattr(bind_tools, "resolve_scope_id", lambda *_a, **_kw: "scp_bound_123")

    # 1. Lazy call_mcp_tool for powercontext
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "conversationId": "c2",
                "workspacePaths": [str(tmp_path)],
                "toolCall": {
                    "name": "call_mcp_tool",
                    "args": {
                        "ServerName": "powercontext_powercontext",
                        "ToolName": "search_memory",
                        "Arguments": {"query": "x", "scope_id": "placeholder"},
                    },
                },
            })
        ),
    )
    assert bind_tools.main(settings=_settings()) == 0
    assert json.loads(capsys.readouterr().out) == {
        "decision": "ask",
        "overwrite": {"Arguments": {"query": "x", "scope_id": "scp_bound_123"}},
    }

    # 2. Eager mcp_powercontext_search_memory
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "conversationId": "c2",
                "workspacePaths": [str(tmp_path)],
                "toolCall": {"name": "mcp_powercontext_search_memory", "args": {"query": "x"}},
            })
        ),
    )
    assert bind_tools.main(settings=_settings()) == 0
    assert json.loads(capsys.readouterr().out) == {
        "decision": "ask",
        "overwrite": {"query": "x", "scope_id": "scp_bound_123"},
    }

    # 3. Non-powercontext server passthrough
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "conversationId": "c2",
                "workspacePaths": [str(tmp_path)],
                "toolCall": {
                    "name": "call_mcp_tool",
                    "args": {"ServerName": "openviking", "ToolName": "find", "Arguments": {"query": "x"}},
                },
            })
        ),
    )
    assert bind_tools.main(settings=_settings()) == 0
    assert json.loads(capsys.readouterr().out) == {"decision": "ask"}

    # 4. 401/unavailable denies
    def _fail_401(*_a: object, **_kw: object) -> str:
        raise ScopeBindingStatusError(401, "unauthorized")

    monkeypatch.setattr(bind_tools, "resolve_scope_id", _fail_401)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "conversationId": "c2",
                "workspacePaths": [str(tmp_path)],
                "toolCall": {
                    "name": "call_mcp_tool",
                    "args": {"ServerName": "powercontext", "ToolName": "search_memory", "Arguments": {"query": "x"}},
                },
            })
        ),
    )
    assert bind_tools.main(settings=_settings()) == 0
    denied = json.loads(capsys.readouterr().out)
    assert denied["decision"] == "deny"
    assert "401" in denied["reason"]


def test_stdlib_recorded_payloads_characterization_and_prompt_recall_switch(tmp_path: Path) -> None:
    """Run copied plugin hooks with python -S (stdlib only) against recorded Antigravity payloads."""

    plugin_copy = tmp_path / "plugin"
    shutil.copytree(_PLUGIN_ROOT, plugin_copy, ignore=shutil.ignore_patterns(".venv", "__pycache__", "tests"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        json.dumps({
            "step_index": 0,
            "type": "USER_INPUT",
            "source": "USER_EXPLICIT",
            "status": "DONE",
            "content": "<USER_REQUEST>\nWhich service owns the retry budget?\n</USER_REQUEST>",
        })
        + "\n",
        encoding="utf-8",
    )

    recorded = json.loads(_FIXTURES.read_text(encoding="utf-8"))
    replacements = {"$WORKSPACE_URI": workspace.as_uri(), "$TRANSCRIPT_PATH": str(transcript)}

    def _sub(node: object) -> object:
        if isinstance(node, str):
            return replacements.get(node, node)
        if isinstance(node, list):
            return [_sub(item) for item in node]
        if isinstance(node, dict):
            return {k: _sub(v) for k, v in node.items()}
        return node

    requests: list[tuple[str, dict[str, object]]] = []
    server, thread = _start_scripted_server(requests)
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POWERCONTEXT_", "PYTHONPATH", "VIRTUAL_ENV"))
    }
    env.update({
        "HOME": str(tmp_path),
        "PLUGIN_DATA": str(tmp_path / "plugin-data"),
        "POWERCONTEXT_ANTIGRAVITY_SERVER_URL": f"http://127.0.0.1:{server.server_port}",
        "POWERCONTEXT_ANTIGRAVITY_CAPTURE_PROMPTS": "true",
    })
    try:
        # 1. SessionStart with stdlib-only python -S resolves scope and injects nothing when bootstrap=false
        ss = subprocess.run(
            [sys.executable, "-S", str(plugin_copy / "hooks/hook.py"), "session-start"],
            input=json.dumps(_sub(recorded["SessionStart"])),
            capture_output=True,
            text=True,
            env=env,
            cwd=plugin_copy,
            check=True,
            timeout=15,
        )
        assert json.loads(ss.stdout) == {}
        assert any(path == "/v1/scope-bindings/resolve" for path, _ in requests)

        # 2. PreInvocation initial turn calls prepare + capture and emits injectSteps
        requests.clear()
        pi = subprocess.run(
            [sys.executable, "-S", str(plugin_copy / "hooks/hook.py"), "pre-invocation"],
            input=json.dumps(_sub(recorded["PreInvocationInitial"])),
            capture_output=True,
            text=True,
            env=env,
            cwd=plugin_copy,
            check=True,
            timeout=15,
        )
        pi_out = json.loads(pi.stdout)
        msg = pi_out["injectSteps"][0]["systemMessage"]["systemMessage"]
        assert '<powercontext-context source="pre-invocation">' in msg
        assert "Retained server trust policy and historical context." in msg
        assert [p for p, _ in requests] == ["/v1/scope-bindings/resolve", "/v1/context/prepare", "/v1/sources/content"]

        # 3. PreInvocation with invocationNum=1 is a local no-op (0 HTTP calls)
        requests.clear()
        loop_run = subprocess.run(
            [sys.executable, "-S", str(plugin_copy / "hooks/hook.py"), "pre-invocation"],
            input=json.dumps(_sub(recorded["PreInvocationToolLoop"])),
            capture_output=True,
            text=True,
            env=env,
            cwd=plugin_copy,
            check=True,
            timeout=15,
        )
        assert json.loads(loop_run.stdout) == {}
        assert requests == []

        # 4. PreToolUse lazy MCP call rewrites scope_id to resolved scope with python -S
        ptu = subprocess.run(
            [sys.executable, "-S", str(plugin_copy / "hooks/bind_tools.py")],
            input=json.dumps(_sub(recorded["PreToolUseLazySearch"])),
            capture_output=True,
            text=True,
            env=env,
            cwd=plugin_copy,
            check=True,
            timeout=15,
        )
        assert json.loads(ptu.stdout) == {
            "decision": "ask",
            "overwrite": {"Arguments": {"mode": "hybrid", "query": "retry budget owner", "scope_id": "scp_char_123"}},
        }

        # 5. Gateway toggle: POWERCONTEXT_ANTIGRAVITY_PROMPT_RECALL=false disables /v1/context/prepare while preserving capture
        requests.clear()
        off_payload = _sub(recorded["PreInvocationInitial"])
        assert isinstance(off_payload, dict)
        off_payload["conversationId"] = "66666666-7777-8888-9999-aaaaaaaaaaaa"
        off_run = subprocess.run(
            [sys.executable, "-S", str(plugin_copy / "hooks/hook.py"), "pre-invocation"],
            input=json.dumps(off_payload),
            capture_output=True,
            text=True,
            env={**env, "POWERCONTEXT_ANTIGRAVITY_PROMPT_RECALL": "false"},
            cwd=plugin_copy,
            check=True,
            timeout=15,
        )
        assert json.loads(off_run.stdout) == {}
        assert [p for p, _ in requests] == ["/v1/scope-bindings/resolve", "/v1/sources/content"]
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_pre_invocation_scrubs_credentials_before_capture(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    captured: list[tuple[str, dict[str, object]]] = []
    server, thread = _start_scripted_server(captured)
    try:
        settings = AntigravityPluginSettings(
            server_url=f"http://127.0.0.1:{server.server_port}",
            capture_prompts=True,
        )
        monkeypatch.setenv("PLUGIN_DATA", str(tmp_path))
        monkeypatch.setattr(hook.AntigravityPluginSettings, "from_environment", classmethod(lambda cls: settings))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps({
                    "conversationId": "c5",
                    "invocationNum": 0,
                    "lastUserInput": "Use key sk-1234567890abcdef123456 to deploy.",
                    "workspacePaths": [str(tmp_path)],
                })
            ),
        )
        monkeypatch.setattr(sys, "argv", ["hook.py", "pre-invocation"])
        assert hook.main() == 0
    finally:
        server.shutdown()
        thread.join()
        server.server_close()

    out = json.loads(capsys.readouterr().out)
    assert (
        "Retained server trust policy and historical context."
        in out["injectSteps"][0]["systemMessage"]["systemMessage"]
    )
    assert not any(path == "/v1/sources/content" for path, _body in captured)
    assert "sk-1234567890abcdef123456" not in json.dumps(captured)


def test_antigravity_cli_installer_configure_and_verify(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from powercontext.cli.antigravity import AntigravityInstaller
    from powercontext.cli.installation import PluginSource
    from powercontext.cli.system import DiagnosticStatus
    from powercontext.cli.transport import SetupTransport

    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("ANTIGRAVITY_CONFIG_DIR", str(tmp_path / "agy-cfg"))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "pc-home"))

    installer = AntigravityInstaller()
    install_res = installer.install(PluginSource(str(repo_root)))
    dest = Path(install_res["plugin_path"])
    assert (dest / ".powercontext-owned").is_file()
    assert (dest / "hooks.json").is_file()
    assert not (dest / ".venv").exists()

    conn = SetupTransport(host="antigravity", server_url="http://127.0.0.1:8000", allow_insecure_http=True)
    cfg_res = installer.configure(conn)
    assert cfg_res["server_url"] == "http://127.0.0.1:8000"
    assert (
        json.loads((dest / "mcp_config.json").read_text())["mcpServers"]["powercontext"]["url"]
        == "http://127.0.0.1:8000/mcp/"
    )
    assert json.loads((dest / "powercontext.json").read_text())["prompt_recall"] is True

    checks = installer.verify()
    assert checks["plugin"].status is DiagnosticStatus.OK
    assert checks["plugin_runtime"].status is DiagnosticStatus.OK
    assert "stdlib" in checks["plugin_runtime"].detail
    assert checks["plugin_memory_protocol"].status is DiagnosticStatus.OK
    assert checks["hooks"].status is DiagnosticStatus.OK
    assert checks["mcp_configuration"].status is DiagnosticStatus.OK
    assert checks["hook_diagnostics"].status is DiagnosticStatus.OK


def test_unwrap_user_request_strips_trailing_metadata_and_dedups_unindexed_prompts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    raw = "<USER_REQUEST>\nTrack port 28461\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\ntime=now\n</ADDITIONAL_METADATA>"
    assert hook._unwrap_user_request(raw) == "Track port 28461"

    monkeypatch.setenv("PLUGIN_DATA", str(tmp_path))
    monkeypatch.delenv("POWERCONTEXT_ANTIGRAVITY_DEBUG", raising=False)
    monkeypatch.setattr(hook.AntigravityPluginSettings, "from_environment", classmethod(lambda cls: _settings()))
    seen_prompts: list[str] = []

    def _fake_submit(_host: object, event: object, *_a: object, **_kw: object) -> object:
        seen_prompts.append(event.prompt)  # type: ignore[attr-defined]
        return SimpleNamespace(scope_id="scp_1", context="ctx", diagnostics=[])

    monkeypatch.setattr(hook, "run_user_prompt_submit", _fake_submit)
    payload = {
        "conversationId": "unindexed",
        "invocationNum": 0,
        "lastUserInput": raw,
        "workspacePaths": [str(tmp_path)],
    }

    monkeypatch.setattr(sys, "argv", ["hook.py", "pre-invocation"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hook.main() == 0
    assert "ctx" in capsys.readouterr().out

    # Second wakeup with identical unindexed prompt must dedup when transcriptPath is absent.
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))
    assert hook.main() == 0
    assert json.loads(capsys.readouterr().out) == {}
    assert seen_prompts == ["Track port 28461"]
    assert not (tmp_path / "antigravity-hooks.log").exists()


def test_transcript_truncated_content_falls_back_to_full_transcript(tmp_path: Path) -> None:
    compact = tmp_path / "transcript.jsonl"
    full = tmp_path / "transcript_full.jsonl"
    compact.write_text(
        json.dumps({"step_index": 0, "type": "USER_INPUT", "source": "USER_EXPLICIT", "truncated_fields": ["content"]})
        + "\n",
        encoding="utf-8",
    )
    full.write_text(
        json.dumps({
            "step_index": 0,
            "type": "USER_INPUT",
            "source": "USER_EXPLICIT",
            "content": "<USER_REQUEST>full prompt</USER_REQUEST>",
        })
        + "\n",
        encoding="utf-8",
    )
    resolved = hook._transcript_path(str(compact))
    assert resolved == full
    assert hook._resolve_turn_step(resolved.read_text(encoding="utf-8"), 1, "full prompt") == 1


def test_settings_rejects_ipv4_mapped_ipv6_loopback_and_bounds_pre_tool_use() -> None:
    with pytest.raises(ValueError, match="loopback"):
        AntigravityPluginSettings(server_url="http://[::ffff:127.0.0.1]:8000")
    assert (
        AntigravityPluginSettings(
            server_url="http://[::ffff:127.0.0.1]:8000", allow_insecure_http=True
        ).allow_insecure_http
        is True
    )
    assert host_bounded_http_budget(9.0, host_timeout=PRE_TOOL_USE_HOST_TIMEOUT_SECONDS) == 6.0


def test_bind_tools_denies_403_and_native_transport_detects_url_drift(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    def _fail_403(*_a: object, **_kw: object) -> str:
        raise ScopeBindingStatusError(403, "forbidden")

    monkeypatch.setattr(bind_tools, "resolve_scope_id", _fail_403)
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps({
                "conversationId": "c3",
                "workspacePaths": [str(tmp_path)],
                "toolCall": {
                    "name": "call_mcp_tool",
                    "args": {"ServerName": "powercontext", "ToolName": "search_memory", "Arguments": {"query": "x"}},
                },
            })
        ),
    )
    assert bind_tools.main(settings=_settings()) == 0
    denied = json.loads(capsys.readouterr().out)
    assert denied["decision"] == "deny"
    assert "403" in denied["reason"]

    from powercontext.cli.antigravity import AntigravityInstaller
    from powercontext.cli.installation import PluginSource
    from powercontext.cli.native_transport import resolve_host_transport
    from powercontext.cli.transport import SetupTransport

    monkeypatch.setenv("ANTIGRAVITY_CONFIG_DIR", str(tmp_path / "agy-cfg"))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "pc-home"))
    installer = AntigravityInstaller(prompt_recall=False, capture_prompts=False)
    res = installer.install(PluginSource(str(Path(__file__).resolve().parents[2])))
    installer.configure(
        SetupTransport(host="antigravity", server_url="http://127.0.0.1:8000", allow_insecure_http=True)
    )
    dest = Path(res["plugin_path"])
    cfg = json.loads((dest / "powercontext.json").read_text(encoding="utf-8"))
    assert cfg["prompt_recall"] is False
    assert cfg["capture_prompts"] is False

    mcp_path = dest / "mcp_config.json"
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    mcp["mcpServers"]["powercontext"]["url"] = "http://127.0.0.1:9999/mcp/"
    mcp_path.write_text(json.dumps(mcp), encoding="utf-8")
    with pytest.raises(ValueError, match="Antigravity Hook and MCP URLs differ"):
        resolve_host_transport("antigravity")


def test_verify_flags_global_mcp_duplicate_bad_cred_perms_header_drift_and_broken_hook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from powercontext.cli.antigravity import AntigravityInstaller
    from powercontext.cli.authorization import credential_path
    from powercontext.cli.installation import PluginSource
    from powercontext.cli.system import DiagnosticStatus
    from powercontext.cli.transport import SetupTransport

    monkeypatch.setenv("ANTIGRAVITY_CONFIG_DIR", str(tmp_path / "agy-cfg"))
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "pc-home"))
    installer = AntigravityInstaller()
    res = installer.install(PluginSource(str(Path(__file__).resolve().parents[2])))
    dest = Path(res["plugin_path"])
    installer.configure(
        SetupTransport(host="antigravity", server_url="http://127.0.0.1:8000", allow_insecure_http=True)
    )

    # 1. Duplicate global mcp_config.json -> DEGRADED
    global_mcp = tmp_path / "agy-cfg" / "mcp_config.json"
    global_mcp.write_text(
        json.dumps({"mcpServers": {"powercontext": {"url": "http://127.0.0.1:8000/mcp/"}}}), encoding="utf-8"
    )
    assert installer.verify()["mcp_configuration"].status is DiagnosticStatus.DEGRADED
    global_mcp.unlink()

    # 2. Credential header mismatch + unsafe 0o644 permissions -> FAILED
    cred = credential_path("antigravity")
    cred.parent.mkdir(parents=True, exist_ok=True)
    cred.write_text(
        json.dumps({"version": 1, "server_url": "http://127.0.0.1:8000", "authorization": "Bearer secret-tok"}),
        encoding="utf-8",
    )
    os.chmod(cred, 0o600)
    assert installer.verify()["mcp_configuration"].status is DiagnosticStatus.FAILED
    os.chmod(cred, 0o644)
    assert installer.verify()["mcp_configuration"].status is DiagnosticStatus.FAILED
    cred.unlink()

    # 3. Broken hook script fails -S smoke probe -> FAILED
    (dest / "hooks" / "bind_tools.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    assert installer.verify()["hooks"].status is DiagnosticStatus.FAILED


def test_extraction_schedule_diagnostic_surfaces_last_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from powercontext.cli import antigravity as agy_cli
    from powercontext.cli.system import DiagnosticStatus

    payload: dict[str, Any] = {
        "extraction": {
            "background": {"automatic_processing_enabled": True},
            "observation": {
                "last_success_at": None,
                "last_failure": {
                    "code": "model_configuration_error",
                    "stage": "inference",
                    "occurred_at": "2026-11-27T06:35:46+00:00",
                },
            },
        }
    }
    monkeypatch.setattr(agy_cli, "_request_json", lambda *_a, **_kw: (200, payload))
    diag = agy_cli.extraction_schedule_diagnostic("http://127.0.0.1:8000")
    assert diag.status is DiagnosticStatus.OK
    assert diag.checks == {"automatic_processing": "enabled", "last_failure": "model_configuration_error@inference"}
    assert "model_configuration_error at inference" in diag.detail

    payload["extraction"]["observation"]["last_success_at"] = "2026-11-27T06:40:00+00:00"
    recovered = agy_cli.extraction_schedule_diagnostic("http://127.0.0.1:8000")
    assert recovered.checks == {"automatic_processing": "enabled"}
