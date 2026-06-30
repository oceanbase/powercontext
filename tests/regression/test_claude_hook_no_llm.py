"""No-LLM regression tests for the Claude Code hook binary.

The suite intentionally uses only the Python standard library so it can run in
an isolated Docker container without installing project or test dependencies.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "claude_hook"
PLUGIN_ROOT = ROOT / "apps" / "claude-code-plugin"
HOOK_BIN_ENV = "POWERMEM_HOOK_BIN"
_BUILT_HOOK_BIN: Path | None = None
SENTINEL = "Bearer sentinelsecret1049"
FAKE_TOKEN = "Bearer redactme1049"
FAKE_BEARER = "Bearer abcdefghijk"
FORBIDDEN_LEAKS = (SENTINEL, FAKE_TOKEN, FAKE_BEARER)


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: dict[str, Any]


class RecordingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, server_address: tuple[str, int]) -> None:
        super().__init__(server_address, FakePowerMemHandler)
        self._records: list[RecordedRequest] = []
        self._lock = threading.Lock()

    def record(self, request: RecordedRequest) -> None:
        with self._lock:
            self._records.append(request)

    def records(self) -> list[RecordedRequest]:
        with self._lock:
            return list(self._records)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


class FakePowerMemHandler(BaseHTTPRequestHandler):
    server: RecordingHTTPServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.JSONEncoder().encode(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path == "/api/v1/system/health":
            self._send_json(200, {"status": "ok"})
            return
        self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        raw = self.rfile.read(int(self.headers.get("Content-Length", "0") or "0"))
        try:
            body: dict[str, Any] = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            body = {"_raw": raw.decode("utf-8", errors="replace")}

        headers = {key.lower(): value for key, value in self.headers.items()}
        self.server.record(RecordedRequest("POST", self.path, headers, body))

        if self.path == "/api/v1/memories/search":
            self._send_json(
                200,
                {
                    "data": {
                        "results": [
                            {
                                "content": (
                                    "The isolated hook regression suite should "
                                    "run without network."
                                ),
                                "score": 0.91,
                            }
                        ]
                    }
                },
            )
            return

        if self.path == "/api/v1/memories":
            self._send_json(200, {"data": {"id": "fake-memory-id"}})
            return

        self._send_json(404, {"error": "not found"})


class FakePowerMemServer:
    def __init__(self) -> None:
        host, port = self._bind_target()
        self.httpd = RecordingHTTPServer((host, port))
        bound_host, bound_port = self.httpd.server_address[:2]
        self.url = f"http://{bound_host}:{bound_port}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @staticmethod
    def _bind_target() -> tuple[str, int]:
        raw = os.environ.get("POWERMEM_TEST_BASE_URL", "").strip()
        if not raw:
            return "localhost", 0
        parsed = urlparse(raw)
        host = parsed.hostname or "localhost"
        if host != "localhost":
            return "localhost", 0
        port = parsed.port or 8848
        return host, port

    def __enter__(self) -> "FakePowerMemServer":
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.httpd.shutdown()
        self._thread.join(timeout=5)
        self.httpd.server_close()

    def records(self) -> list[RecordedRequest]:
        return self.httpd.records()

    def clear(self) -> None:
        self.httpd.clear()


def load_fixture_json(relative: str) -> dict[str, Any]:
    return json.loads((FIXTURES / relative).read_text(encoding="utf-8"))


def hook_binary() -> Path:
    configured = os.environ.get(HOOK_BIN_ENV)
    if configured:
        return Path(configured)

    global _BUILT_HOOK_BIN
    if _BUILT_HOOK_BIN is not None:
        return _BUILT_HOOK_BIN

    build_dir = Path(tempfile.mkdtemp(prefix="powermem-hook-regression-"))
    out = build_dir / "powermem-hook"
    subprocess.run(
        ["go", "build", "-trimpath", "-o", str(out), "./cmd/powermem-hook"],
        cwd=PLUGIN_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
        timeout=60,
    )
    _BUILT_HOOK_BIN = out
    return out


class ClaudeHookNoLLMRegressionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.hook_bin = hook_binary()
        if not self.hook_bin.exists():
            self.fail(f"hook binary does not exist: {self.hook_bin}")
        if not os.access(self.hook_bin, os.X_OK):
            self.fail(f"hook binary is not executable: {self.hook_bin}")
        self.server_cm = FakePowerMemServer()
        self.server = self.server_cm.__enter__()

    def tearDown(self) -> None:
        self.server_cm.__exit__(None, None, None)

    def hook_env(self, tmp_path: Path, **overrides: str) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_path),
            "TMPDIR": str(tmp_path),
            "POWERMEM_BASE_URL": self.server.url,
            "POWERMEM_INFER_TRANSCRIPT": "0",
            "POWERMEM_INFER_COMPACT": "0",
            "POWERMEM_INFER_FILE": "0",
            "POWERMEM_PROMPT_SEARCH": "1",
            "POWERMEM_DATA_DIR": str(tmp_path / "powermem-data"),
            "POWERMEM_USER_ID": "hook-user",
            "POWERMEM_AGENT_ID": "hook-agent",
            "POWERMEM_API_KEY": "hook-api-key",
        }
        for key in (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "OPENAI_API_KEY",
            "LLM_API_KEY",
            "LLM_AUTH_TOKEN",
            "EMBEDDING_API_KEY",
            "QWEN_API_KEY",
            "DASHSCOPE_API_KEY",
        ):
            env.pop(key, None)
        env.update(overrides)
        return env

    def run_hook(
        self,
        payload: dict[str, Any],
        tmp_path: Path,
        **env_overrides: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.hook_bin)],
            cwd=ROOT,
            env=self.hook_env(tmp_path, **env_overrides),
            input=json.JSONEncoder().encode(payload),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
        )

    def wait_for_request(
        self,
        path: str,
        *,
        kind: str | None = None,
        timeout: float = 5.0,
    ) -> RecordedRequest:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for request in self.server.records():
                if request.path != path:
                    continue
                if kind is not None and request.body.get("metadata", {}).get("kind") != kind:
                    continue
                return request
            time.sleep(0.05)
        self.fail(f"timed out waiting for {path} request")

    def assert_no_request(self, path: str, *, duration: float = 0.35) -> None:
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            matches = [request for request in self.server.records() if request.path == path]
            if matches:
                self.fail(f"unexpected {path} request: {matches[-1]}")
            time.sleep(0.05)

    def wait_for_no_hook_workers(self, *, timeout: float = 5.0) -> None:
        proc = Path("/proc")
        if not proc.is_dir():
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            workers: list[str] = []
            for entry in proc.iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    cmdline = (entry / "cmdline").read_bytes().replace(b"\x00", b" ")
                except OSError:
                    continue
                if b"powermem-hook" in cmdline and b"worker-" in cmdline:
                    workers.append(cmdline.decode("utf-8", errors="replace"))
            if not workers:
                return
            time.sleep(0.05)
        self.fail("hook worker processes are still running")

    def assert_no_sentinel(self, *values: Any) -> None:
        for value in values:
            text = str(value)
            for forbidden in FORBIDDEN_LEAKS:
                self.assertNotIn(forbidden, text)

    def test_user_prompt_submit_searches_with_headers_and_scrubs_output(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            payload = load_fixture_json("prompts/user_prompt_submit.json")
            result = self.run_hook(payload, Path(raw_tmp))

        self.assertEqual(result.returncode, 0, result.stderr)
        request = self.wait_for_request("/api/v1/memories/search")

        self.assertEqual(request.headers.get("x-api-key"), "hook-api-key")
        self.assertEqual(request.body["user_id"], "hook-user")
        self.assertEqual(request.body["agent_id"], "hook-agent")
        self.assertEqual(request.body["limit"], 8)
        self.assert_no_sentinel(request.body, result.stdout, result.stderr)

        output = json.loads(result.stdout)
        context = output["hookSpecificOutput"]["additionalContext"]
        self.assertIn("PowerMem", context)
        self.assertIn("isolated hook regression suite", context)
        self.assert_no_sentinel(context)

    def test_user_prompt_submit_can_disable_search(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            payload = load_fixture_json("prompts/user_prompt_submit.json")
            result = self.run_hook(payload, Path(raw_tmp), POWERMEM_PROMPT_SEARCH="0")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assert_no_request("/api/v1/memories/search")
        self.assert_no_sentinel(result.stdout, result.stderr)

    def test_session_start_searches_and_injects_context(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            payload = load_fixture_json("payloads/session_start.json")
            result = self.run_hook(payload, Path(raw_tmp))

        self.assertEqual(result.returncode, 0, result.stderr)
        request = self.wait_for_request("/api/v1/memories/search")

        self.assertEqual(request.headers.get("x-api-key"), "hook-api-key")
        self.assertEqual(request.body["user_id"], "hook-user")
        self.assertEqual(request.body["agent_id"], "hook-agent")
        self.assertEqual(request.body["limit"], 6)
        self.assertIn("session_title: Hook session bootstrap", request.body["query"])
        self.assertIn("cwd: powermem", request.body["query"])
        self.assertNotIn("/workspace/powermem", request.body["query"])

        output = json.loads(result.stdout)
        hook_output = output["hookSpecificOutput"]
        self.assertEqual(hook_output["hookEventName"], "SessionStart")
        self.assertIn("isolated hook regression suite", hook_output["additionalContext"])
        self.assert_no_sentinel(request.body, result.stdout, result.stderr)

    def test_session_start_can_disable_search(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            payload = load_fixture_json("payloads/session_start.json")
            result = self.run_hook(
                payload,
                Path(raw_tmp),
                POWERMEM_SESSION_START_SEARCH="0",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assert_no_request("/api/v1/memories/search")
        self.assert_no_sentinel(result.stdout, result.stderr)

    def test_session_end_posts_transcript_and_ignores_bad_transcripts(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            payload = load_fixture_json("payloads/session_end.json")
            payload["transcript_path"] = str(FIXTURES / "transcripts" / "valid.jsonl")

            result = self.run_hook(payload, tmp_path)
            self.assertEqual(result.returncode, 0, result.stderr)
            request = self.wait_for_request(
                "/api/v1/memories",
                kind="session-end-transcript",
            )
            self.wait_for_no_hook_workers()

            self.assertEqual(request.headers.get("x-api-key"), "hook-api-key")
            self.assertFalse(request.body["infer"])
            self.assertEqual(request.body["user_id"], "hook-user")
            self.assertEqual(request.body["agent_id"], "hook-agent")
            self.assertEqual(request.body["run_id"], "session-end-1049")
            self.assertIn("Claude Code session transcript", request.body["content"])
            self.assertIn("[session title] No LLM regression session", request.body["content"])
            self.assertEqual(request.body["metadata"]["session_id"], "session-end-1049")
            self.assertEqual(request.body["metadata"]["session_end_reason"], "logout")
            self.assert_no_sentinel(request.body, result.stdout, result.stderr)

            for transcript_name in ("missing.jsonl", "empty.jsonl", "malformed.jsonl"):
                self.server.clear()
                bad_payload = load_fixture_json("payloads/session_end.json")
                bad_payload["transcript_path"] = str(
                    FIXTURES / "transcripts" / transcript_name
                )
                bad_result = self.run_hook(bad_payload, tmp_path)
                self.assertEqual(bad_result.returncode, 0, bad_result.stderr)
                self.wait_for_no_hook_workers()
                self.assert_no_request("/api/v1/memories")
                self.assert_no_sentinel(bad_result.stdout, bad_result.stderr)

    def test_post_compact_posts_summary_and_ignores_empty_summary(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            payload = load_fixture_json("payloads/post_compact.json")
            result = self.run_hook(payload, tmp_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            request = self.wait_for_request(
                "/api/v1/memories",
                kind="post-compact-summary",
            )
            self.wait_for_no_hook_workers()

            self.assertEqual(request.headers.get("x-api-key"), "hook-api-key")
            self.assertFalse(request.body["infer"])
            self.assertEqual(request.body["run_id"], "session-compact-1049")
            self.assertIn("Claude Code context compact summary", request.body["content"])
            self.assertEqual(request.body["metadata"]["session_id"], "session-compact-1049")
            self.assertEqual(request.body["metadata"]["compact_trigger"], "manual")
            self.assert_no_sentinel(request.body, result.stdout, result.stderr)

            self.server.clear()
            empty_payload = load_fixture_json("payloads/post_compact.json")
            empty_payload["compact_summary"] = "   "
            empty_result = self.run_hook(empty_payload, tmp_path)
            self.assertEqual(empty_result.returncode, 0, empty_result.stderr)
            self.wait_for_no_hook_workers()
            self.assert_no_request("/api/v1/memories")
            self.assert_no_sentinel(empty_result.stdout, empty_result.stderr)

    def test_top_level_identity_fields_are_scrubbed(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            payload = load_fixture_json("payloads/post_compact.json")
            payload["session_id"] = f"session-{SENTINEL}"
            result = self.run_hook(
                payload,
                tmp_path,
                POWERMEM_USER_ID=f"user-{FAKE_TOKEN}",
                POWERMEM_AGENT_ID=f"agent-{SENTINEL}",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            request = self.wait_for_request(
                "/api/v1/memories",
                kind="post-compact-summary",
            )
            self.wait_for_no_hook_workers()

            self.assert_no_sentinel(request.body, result.stdout, result.stderr)
            self.assertNotEqual(request.body["run_id"], payload["session_id"])
            self.assertNotEqual(request.body["user_id"], f"user-{FAKE_TOKEN}")
            self.assertNotEqual(request.body["agent_id"], f"agent-{SENTINEL}")

    def test_post_tool_use_bash_posts_structured_scrubbed_event(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            payload = load_fixture_json("payloads/post_tool_use_bash.json")
            result = self.run_hook(payload, tmp_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            request = self.wait_for_request("/api/v1/memories", kind="post-tool-use")
            self.wait_for_no_hook_workers()

            metadata = request.body["metadata"]
            self.assertFalse(request.body["infer"])
            self.assertEqual(request.body["run_id"], "session-tool-1041")
            self.assertEqual(metadata["event_name"], "PostToolUse")
            self.assertEqual(metadata["event_id"], "claude-code:session-tool-1041:tool-bash-1")
            self.assertEqual(metadata["tool_name"], "Bash")
            self.assertEqual(metadata["tool_use_id"], "tool-bash-1")
            self.assertEqual(metadata["command_class"], "test")
            self.assertEqual(metadata["exit_code"], 0)
            self.assertIn("Input summary", request.body["content"])
            self.assertIn("Response summary", request.body["content"])
            self.assert_no_sentinel(request.body, result.stdout, result.stderr)

    def test_post_tool_use_agent_preserves_response_agent_link(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            payload = load_fixture_json("payloads/post_tool_use_agent.json")
            result = self.run_hook(payload, tmp_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            request = self.wait_for_request("/api/v1/memories", kind="post-tool-use")
            self.wait_for_no_hook_workers()

            metadata = request.body["metadata"]
            self.assertFalse(request.body["infer"])
            self.assertEqual(metadata["tool_name"], "Agent")
            self.assertEqual(metadata["tool_use_id"], "tool-agent-1")
            self.assertEqual(metadata["agent_id"], "agent-response-1045")
            self.assertEqual(metadata["agent_type"], "reviewer")
            self.assertEqual(metadata["agent_status"], "completed")
            self.assertIn("Review completed", metadata["response_summary"])
            self.assertIn("Review completed", request.body["content"])
            self.assert_no_sentinel(request.body, result.stdout, result.stderr)

    def test_post_tool_use_write_summarizes_paths_without_content_leak(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            payload = load_fixture_json("payloads/post_tool_use_write.json")
            result = self.run_hook(payload, tmp_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            request = self.wait_for_request("/api/v1/memories", kind="post-tool-use")
            self.wait_for_no_hook_workers()

            metadata = request.body["metadata"]
            self.assertEqual(metadata["tool_name"], "Write")
            self.assertEqual(metadata["affected_paths"], ["src/new_feature.py"])
            self.assertIn("content_chars=", metadata["input_summary"])
            self.assertNotIn("def leaked_token", request.body["content"])
            self.assert_no_sentinel(request.body, result.stdout, result.stderr)

    def test_post_tool_use_wildcard_unknown_tool_records_shape_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            payload = {
                "hook_event_name": "PostToolUse",
                "session_id": "session-tool-unknown",
                "cwd": "/workspace/project",
                "tool_name": "UnlistedTool",
                "tool_use_id": "tool-unknown-1",
                "tool_input": ["unexpected", {"token": SENTINEL}],
                "tool_response": ["ok", FAKE_TOKEN],
            }
            result = self.run_hook(
                payload,
                tmp_path,
                POWERMEM_TOOL_SUCCESS_INCLUDE="*",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            request = self.wait_for_request("/api/v1/memories", kind="post-tool-use")
            self.wait_for_no_hook_workers()

            metadata = request.body["metadata"]
            self.assertEqual(metadata["tool_name"], "UnlistedTool")
            self.assertEqual(metadata["input_summary"], "shape=array[2]")
            self.assertEqual(metadata["response_summary"], "shape=array[2]")
            self.assert_no_sentinel(request.body, result.stdout, result.stderr)

    def test_post_tool_use_exclude_wins_and_capture_can_disable(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            payload = load_fixture_json("payloads/post_tool_use_bash.json")

            result = self.run_hook(
                payload,
                tmp_path,
                POWERMEM_TOOL_SUCCESS_INCLUDE="Bash,Write",
                POWERMEM_TOOL_SUCCESS_EXCLUDE="Bash",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.wait_for_no_hook_workers()
            self.assert_no_request("/api/v1/memories")

            result = self.run_hook(
                payload,
                tmp_path,
                POWERMEM_CAPTURE_TOOL_SUCCESS="0",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.wait_for_no_hook_workers()
            self.assert_no_request("/api/v1/memories")
            self.assert_no_sentinel(result.stdout, result.stderr)

    def test_post_tool_use_failure_posts_structured_failure(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            payload = load_fixture_json("payloads/post_tool_use_failure_bash.json")
            result = self.run_hook(payload, tmp_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            request = self.wait_for_request(
                "/api/v1/memories",
                kind="post-tool-use-failure",
            )
            self.wait_for_no_hook_workers()

            metadata = request.body["metadata"]
            self.assertFalse(request.body["infer"])
            self.assertEqual(request.body["run_id"], "session-failure-1042")
            self.assertEqual(metadata["event_name"], "PostToolUseFailure")
            self.assertEqual(metadata["tool_name"], "Bash")
            self.assertEqual(metadata["tool_use_id"], "tool-failure-1042")
            self.assertEqual(metadata["success"], False)
            self.assertEqual(metadata["is_interrupt"], False)
            self.assertEqual(metadata["error_type"], "non_zero_exit")
            self.assertEqual(metadata["command_class"], "test")
            self.assertEqual(metadata["exit_code"], 2)
            self.assertIn("Error summary", request.body["content"])
            self.assert_no_sentinel(request.body, result.stdout, result.stderr)

            self.server.clear()
            interrupt_payload = load_fixture_json("payloads/post_tool_use_failure_bash.json")
            interrupt_payload["is_interrupt"] = True
            interrupt_result = self.run_hook(interrupt_payload, tmp_path)
            self.assertEqual(interrupt_result.returncode, 0, interrupt_result.stderr)
            self.wait_for_no_hook_workers()
            self.assert_no_request("/api/v1/memories")

    def test_stop_rollup_posts_when_enabled_and_skips_recursion(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            payload = load_fixture_json("payloads/stop_rollup.json")

            disabled_result = self.run_hook(payload, tmp_path)
            self.assertEqual(disabled_result.returncode, 0, disabled_result.stderr)
            self.wait_for_no_hook_workers()
            self.assert_no_request("/api/v1/memories")

            enabled_result = self.run_hook(
                payload,
                tmp_path,
                POWERMEM_CAPTURE_STOP_ROLLUP="1",
            )
            self.assertEqual(enabled_result.returncode, 0, enabled_result.stderr)
            request = self.wait_for_request("/api/v1/memories", kind="stop-rollup")
            self.wait_for_no_hook_workers()

            metadata = request.body["metadata"]
            self.assertFalse(request.body["infer"])
            self.assertEqual(request.body["run_id"], "session-stop-1043")
            self.assertEqual(metadata["event_name"], "Stop")
            self.assertEqual(metadata["session_id"], "session-stop-1043")
            self.assertIn("Final assistant message preview", request.body["content"])
            self.assert_no_sentinel(request.body, enabled_result.stdout, enabled_result.stderr)

            self.server.clear()
            recursive_payload = load_fixture_json("payloads/stop_rollup.json")
            recursive_payload["stop_hook_active"] = True
            recursive_result = self.run_hook(
                recursive_payload,
                tmp_path,
                POWERMEM_CAPTURE_STOP_ROLLUP="1",
            )
            self.assertEqual(recursive_result.returncode, 0, recursive_result.stderr)
            self.wait_for_no_hook_workers()
            self.assert_no_request("/api/v1/memories")

    def test_pre_compact_posts_tail_snapshot_with_offsets(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            transcript = tmp_path / "transcript.jsonl"
            transcript.write_text(
                "\n".join(
                    [
                        json.JSONEncoder().encode(
                            {"type": "user", "message": {"content": "old context"}}
                        ),
                        json.JSONEncoder().encode(
                            {
                                "type": "assistant",
                                "message": {
                                    "content": f"recent implementation plan {SENTINEL}"
                                },
                            }
                        ),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            payload = load_fixture_json("payloads/pre_compact.json")
            payload["transcript_path"] = str(transcript)

            result = self.run_hook(
                payload,
                tmp_path,
                POWERMEM_PRECOMPACT_MAX_CHARS="500",
                POWERMEM_PRECOMPACT_TAIL_LINES="1",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            request = self.wait_for_request(
                "/api/v1/memories",
                kind="pre-compact-snapshot",
            )
            self.wait_for_no_hook_workers()

            metadata = request.body["metadata"]
            self.assertFalse(request.body["infer"])
            self.assertEqual(request.body["run_id"], "session-precompact-1044")
            self.assertEqual(metadata["event_name"], "PreCompact")
            self.assertEqual(metadata["compact_trigger"], "auto")
            self.assertIn("transcript_path_fingerprint", metadata)
            self.assertGreater(metadata["end_byte_offset"], metadata["start_byte_offset"])
            self.assertEqual(metadata["max_chars"], 500)
            self.assertEqual(metadata["max_lines"], 1)
            self.assertIn("recent implementation plan", request.body["content"])
            self.assertNotIn("old context", request.body["content"])
            self.assert_no_sentinel(request.body, result.stdout, result.stderr)

    def test_lifecycle_event_uses_event_name_as_kind_source(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            payload = load_fixture_json("payloads/subagent_stop.json")
            result = self.run_hook(payload, tmp_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            request = self.wait_for_request("/api/v1/memories", kind="subagent-stop")
            self.wait_for_no_hook_workers()

            metadata = request.body["metadata"]
            self.assertFalse(request.body["infer"])
            self.assertEqual(request.body["run_id"], "session-lifecycle-1045")
            self.assertEqual(metadata["kind"], "subagent-stop")
            self.assertEqual(metadata["event_name"], "SubagentStop")
            self.assertEqual(metadata["agent_id"], "agent-1045")
            self.assertEqual(metadata["agent_type"], "reviewer")
            self.assertNotIn("tool_use_id", metadata)
            self.assertNotIn("raw_payload", metadata)
            self.assertNotIn("payload_fields", metadata)
            self.assertNotIn("payload_summary", metadata)
            self.assertEqual(
                metadata["agent_transcript_path"],
                Path(payload["agent_transcript_path"]).name,
            )
            self.assertNotIn("/workspace/project", metadata["agent_transcript_path"])
            self.assertIn("SubagentStop", request.body["content"])
            self.assert_no_sentinel(request.body, result.stdout, result.stderr)

    def test_task_lifecycle_records_task_schema_fields(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            payload = load_fixture_json("payloads/task_completed.json")
            result = self.run_hook(payload, tmp_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            request = self.wait_for_request("/api/v1/memories", kind="task-completed")
            self.wait_for_no_hook_workers()

            metadata = request.body["metadata"]
            self.assertFalse(request.body["infer"])
            self.assertEqual(request.body["run_id"], "session-task-1045")
            self.assertEqual(metadata["kind"], "task-completed")
            self.assertEqual(metadata["event_name"], "TaskCompleted")
            self.assertEqual(metadata["task_id"], "task-1045")
            self.assertEqual(
                metadata["task_subject"],
                "Review Claude hook lifecycle capture",
            )
            self.assertIn("Verify task lifecycle fields", metadata["task_description"])
            self.assertEqual(metadata["teammate_name"], "implementer")
            self.assertIn("task_subject", request.body["content"])
            self.assertIn("Review Claude hook lifecycle capture", request.body["content"])
            self.assertIn("task_description", request.body["content"])
            self.assertIn("Verify task lifecycle fields", request.body["content"])
            self.assertNotIn("raw_payload", metadata)
            self.assertNotIn("payload_fields", metadata)
            self.assertNotIn("payload_summary", metadata)
            self.assertNotIn("/workspace/project", str(metadata))
            self.assert_no_sentinel(request.body, result.stdout, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
