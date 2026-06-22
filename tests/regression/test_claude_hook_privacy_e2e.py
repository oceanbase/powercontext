import json
import os
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from json import dumps as json_dumps
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_ROOT = REPO_ROOT / "apps" / "claude-code-plugin"
FAKE_HOME = "/workspace/alice"
FAKE_PRIVATE_DIR = FAKE_HOME + "/project"
FAKE_PRIVATE_PATH = FAKE_PRIVATE_DIR + "/secret-plan.md"
SECRET_VALUE = "sk-" + ("a" * 24)


class RecordingPowerMemServer(ThreadingHTTPServer):
    def __init__(self):
        self._public_host = "localhost"
        super().__init__((self._public_host, 0), RecordingPowerMemHandler)
        self.requests = []
        self._condition = threading.Condition()

    @property
    def url(self):
        _, port = self.server_address
        return f"http://{self._public_host}:{port}"

    def record(self, request):
        with self._condition:
            self.requests.append(request)
            self._condition.notify_all()

    def snapshot(self):
        with self._condition:
            return list(self.requests)

    def wait_for(self, predicate, timeout=10.0):
        deadline = time.time() + timeout
        with self._condition:
            while True:
                for request in self.requests:
                    if predicate(request):
                        return request
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise AssertionError(f"timed out waiting for request; saw {self.requests!r}")
                self._condition.wait(remaining)


class RecordingPowerMemHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            body = None
        self.server.record(
            {
                "method": "POST",
                "path": self.path,
                "body": raw.decode("utf-8", errors="replace"),
                "json": body,
            }
        )
        if self.path == "/api/v1/memories/search":
            self._json_response(
                {
                    "success": True,
                    "data": {
                        "results": [
                            {
                                "content": (
                                    "Use route GET /api/v1/memories/search, but do not leak "
                                    + FAKE_PRIVATE_PATH
                                    + " or Authorization: Bearer "
                                    + SECRET_VALUE
                                ),
                                "score": 0.87,
                            }
                        ]
                    },
                }
            )
            return
        if self.path == "/api/v1/memories":
            self._json_response({"success": True, "data": {"id": "mem-1053"}})
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, format, *args):
        return

    def _json_response(self, data):
        raw = json_dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


@pytest.fixture
def powermem_server():
    server = RecordingPowerMemServer()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture(scope="session")
def claude_hook_binary(tmp_path_factory):
    override = os.environ.get("POWERMEM_HOOK_BIN")
    if override:
        return Path(override)
    suffix = ".exe" if os.name == "nt" else ""
    binary = tmp_path_factory.mktemp("claude-hook-bin") / ("powermem-hook" + suffix)
    subprocess.run(
        ["go", "build", "-trimpath", "-o", str(binary), "./cmd/powermem-hook"],
        cwd=PLUGIN_ROOT,
        check=True,
    )
    return binary


def base_hook_env(tmp_path, powermem_server, **overrides):
    env = os.environ.copy()
    env.update(
        {
            "HOME": FAKE_HOME,
            "POWERMEM_BASE_URL": powermem_server.url,
            "POWERMEM_DATA_DIR": str(tmp_path / ".powermem"),
            "POWERMEM_USER_ID": "privacy-user",
            "POWERMEM_AGENT_ID": "privacy-agent",
            "POWERMEM_HOOK_SCRUB": "1",
            "POWERMEM_HOOK_PATH_PRIVACY": "basename",
            "POWERMEM_HOOK_SECRET_ACTION": "redact",
            "POWERMEM_HOOK_SEARCH_SECRET_POLICY": "redact",
            "POWERMEM_HOOK_MAX_CHARS": "5000",
            "POWERMEM_PROMPT_SEARCH": "1",
        }
    )
    env.pop("POWERMEM_API_KEY", None)
    env.update(overrides)
    return env


def run_hook(binary, payload, env):
    result = subprocess.run(
        [str(binary)],
        input=json_dumps(payload),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result


def wait_for_memory(powermem_server, kind):
    return powermem_server.wait_for(
        lambda request: request["path"] == "/api/v1/memories"
        and request["json"]
        and request["json"].get("metadata", {}).get("kind") == kind
    )


def assert_no_sensitive_values(*texts):
    for text in texts:
        assert SECRET_VALUE not in text
        assert FAKE_PRIVATE_PATH not in text
        assert FAKE_PRIVATE_DIR not in text


def test_user_prompt_submit_scrubs_search_request_and_stdout(claude_hook_binary, powermem_server, tmp_path):
    env = base_hook_env(tmp_path, powermem_server)
    result = run_hook(
        claude_hook_binary,
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": (
                "Use route GET /api/v1/memories/search, then open "
                + FAKE_PRIVATE_PATH
                + " with Authorization: Bearer "
                + SECRET_VALUE
            ),
        },
        env,
    )

    search_request = powermem_server.wait_for(lambda request: request["path"] == "/api/v1/memories/search")
    query = search_request["json"]["query"]
    assert "/api/v1/memories/search" in query
    assert "secret-plan.md" in query
    assert_no_sensitive_values(json_dumps(search_request["json"], sort_keys=True), result.stdout, result.stderr)

    output = json.loads(result.stdout)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert "PowerMem" in context
    assert "/api/v1/memories/search" in context
    assert "secret-plan.md" in context
    assert_no_sensitive_values(context)


def test_user_prompt_submit_skips_search_when_identifier_contains_secret(
    claude_hook_binary, powermem_server, tmp_path
):
    env = base_hook_env(
        tmp_path,
        powermem_server,
        POWERMEM_USER_ID=SECRET_VALUE,
        POWERMEM_HOOK_SEARCH_SECRET_POLICY="skip",
    )
    result = run_hook(
        claude_hook_binary,
        {
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Find reusable memory for the current task.",
        },
        env,
    )

    time.sleep(0.2)
    assert result.stdout == ""
    assert result.stderr == ""
    assert not [request for request in powermem_server.snapshot() if request["path"] == "/api/v1/memories/search"]


def test_session_end_scrubs_transcript_request_body_and_uses_neutral_handoff(
    claude_hook_binary, powermem_server, tmp_path
):
    transcript = tmp_path / "private" / "session.jsonl"
    transcript.parent.mkdir()
    transcript.write_text(
        "\n".join(
            [
                json_dumps({"type": "summary", "summary": "Work in " + FAKE_PRIVATE_PATH}),
                json_dumps(
                    {
                        "type": "user",
                        "message": {
                            "content": "Please inspect " + FAKE_PRIVATE_PATH + " with token " + SECRET_VALUE
                        },
                    }
                ),
                json_dumps({"type": "assistant", "message": {"content": "Acknowledged " + FAKE_PRIVATE_DIR}}),
            ]
        ),
        encoding="utf-8",
    )
    env = base_hook_env(tmp_path, powermem_server)
    result = run_hook(
        claude_hook_binary,
        {
            "hook_event_name": "SessionEnd",
            "session_id": "session-1053",
            "cwd": FAKE_PRIVATE_DIR,
            "reason": "user-finished",
            "transcript_path": str(transcript),
        },
        env,
    )

    memory_request = wait_for_memory(powermem_server, "session-end-transcript")
    body = json_dumps(memory_request["json"], sort_keys=True)
    assert "session.jsonl" in body
    assert "project" in body
    assert_no_sensitive_values(body, result.stdout, result.stderr)
    assert str(transcript) not in body
    assert str(transcript) not in result.stdout + result.stderr


def test_post_compact_scrubs_summary_request_body(claude_hook_binary, powermem_server, tmp_path):
    env = base_hook_env(tmp_path, powermem_server)
    result = run_hook(
        claude_hook_binary,
        {
            "hook_event_name": "PostCompact",
            "session_id": "session-1053",
            "cwd": FAKE_PRIVATE_DIR,
            "trigger": "manual",
            "compact_summary": "Summary mentions " + FAKE_PRIVATE_PATH + " and " + SECRET_VALUE,
        },
        env,
    )

    memory_request = wait_for_memory(powermem_server, "post-compact-summary")
    body = json_dumps(memory_request["json"], sort_keys=True)
    assert "secret-plan.md" in body
    assert "project" in body
    assert_no_sensitive_values(body, result.stdout, result.stderr)


def test_poller_scrubs_file_request_body_and_process_output(claude_hook_binary, powermem_server, tmp_path):
    watch_root = tmp_path / "private-watch-root"
    watch_root.mkdir()
    note = watch_root / "note.md"
    note.write_text("Workspace file includes " + FAKE_PRIVATE_PATH + " and " + SECRET_VALUE, encoding="utf-8")
    env = base_hook_env(
        tmp_path,
        powermem_server,
        POWERMEM_WATCH_ROOT=str(watch_root),
        POWERMEM_WATCH_SUFFIXES=".md",
        POWERMEM_POLL_INTERVAL="5",
    )
    proc = subprocess.Popen(
        [str(claude_hook_binary), "poll"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        memory_request = wait_for_memory(powermem_server, "workspace-file")
    finally:
        proc.terminate()
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate(timeout=5)

    body = json_dumps(memory_request["json"], sort_keys=True)
    assert "note.md" in body
    assert_no_sensitive_values(body, stdout, stderr)
    assert str(note) not in body
    assert str(watch_root) not in stdout + stderr


def test_windows_launcher_imports_runtime_env_files_with_quoted_values():
    text = (PLUGIN_ROOT / "hooks" / "run-hook.ps1").read_text(encoding="utf-8")

    assert "function Import-PowerMemEnvFile" in text
    assert "'^\\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)\\s*$'" in text
    assert "SetEnvironmentVariable($name, $value, \"Process\")" in text
    assert "StartsWith('\"')" in text
    assert "StartsWith(\"'\")" in text
    data_runtime = text.index('Join-Path $dataDir "runtime.env"')
    plugin_runtime = text.index('Join-Path $PluginRoot "config\\runtime.env"')
    assert data_runtime < plugin_runtime
