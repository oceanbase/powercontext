"""Dual-backend regression tests for the Claude Code hook binary.

Validates that when POWERMEM_FALLBACK_BASE_URL is set, the hook routes
search/write requests to the primary backend when healthy, falls back to
the local backend on connection refusal / timeout / 5xx, persists
circuit-breaker state to fallback-state.json across hook invocations, and
respects the POWERMEM_FALLBACK_DISABLED kill switch.

Uses only the Python standard library so it runs in an isolated Docker
container without project / test dependencies. Modelled on
test_claude_hook_no_llm.py.
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


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "claude_hook"
PLUGIN_ROOT = ROOT / "apps" / "claude-code-plugin"
HOOK_BIN_ENV = "POWERMEM_HOOK_BIN"
_BUILT_HOOK_BIN: Path | None = None


def hook_binary() -> Path:
    configured = os.environ.get(HOOK_BIN_ENV)
    if configured:
        return Path(configured)
    global _BUILT_HOOK_BIN
    if _BUILT_HOOK_BIN is not None:
        return _BUILT_HOOK_BIN
    build_dir = Path(tempfile.mkdtemp(prefix="powermem-hook-dual-"))
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
    server: "ControllablePowerMemServer"

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
            if self.server.fail_health:
                self._send_json(503, {"status": "unhealthy"})
                return
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

        if self.server.fail_search and self.path == "/api/v1/memories/search":
            self._send_json(500, {"error": "primary degraded"})
            return

        if self.path == "/api/v1/memories/search":
            self._send_json(
                200,
                {
                    "data": {
                        "results": [
                            {
                                "content": f"memory from {self.server.label}",
                                "score": 0.91,
                            }
                        ]
                    }
                },
            )
            return

        if self.path == "/api/v1/memories":
            self._send_json(200, {"data": {"id": f"fake-{self.server.label}"}})
            return

        self._send_json(404, {"error": "not found"})


class ControllablePowerMemServer(RecordingHTTPServer):
    """A fake PowerMem HTTP server that can be started, stopped, and toggled.

    Subclasses RecordingHTTPServer so the handler's `self.server` is this
    instance — that's how `label`/`fail_search`/`fail_health` reach the
    handler.

    - `stop()` shuts the httpd down so the port refuses connections.
    - `restart()` brings it back on the same port.
    - `fail_search=True` makes POST /api/v1/memories/search return 500.
    - `fail_health=True` makes GET /api/v1/system/health return 503.
    """

    def __init__(self, *, label: str, host: str = "localhost", port: int = 0) -> None:
        self.label = label
        self.fail_search = False
        self.fail_health = False
        super().__init__((host, port))
        bound_host, bound_port = self.server_address[:2]
        self.url = f"http://{bound_host}:{bound_port}"
        self._thread: threading.Thread | None = None
        self._start_serving()

    def _start_serving(self) -> None:
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.shutdown()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.server_close()
        self._thread = None

    def restart(self) -> None:
        # Rebind a fresh socket on the same port. We need a new
        # ThreadingHTTPServer instance because shutdown() is final on the
        # previous one — but we want to preserve records and toggles. So we
        # re-init the underlying socket while keeping our state.
        host = self.server_address[0]
        port = self._last_bound_port()
        # RecordingHTTPServer.__init__ re-runs the bind. We must first
        # close the old socket — server_close() already did that.
        RecordingHTTPServer.__init__(self, (host, port))
        self._start_serving()

    def _last_bound_port(self) -> int:
        url = getattr(self, "url", "")
        if ":" in url:
            return int(url.rsplit(":", 1)[1])
        return 0

    def __enter__(self) -> "ControllablePowerMemServer":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.stop()


def _load_user_prompt_submit_fixture() -> dict[str, Any]:
    with (FIXTURES / "prompts" / "user_prompt_submit.json").open() as f:
        return json.load(f)


class DualBackendHookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.hook_bin = hook_binary()
        if not self.hook_bin.exists():
            self.fail(f"hook binary does not exist: {self.hook_bin}")
        if not os.access(self.hook_bin, os.X_OK):
            self.fail(f"hook binary is not executable: {self.hook_bin}")
        self.primary = ControllablePowerMemServer(label="primary")
        self.fallback = ControllablePowerMemServer(label="fallback")

    def tearDown(self) -> None:
        self.primary.stop()
        self.fallback.stop()

    def _env(self, tmp_path: Path, **overrides: str) -> dict[str, str]:
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": str(tmp_path),
            "TMPDIR": str(tmp_path),
            "POWERMEM_BASE_URL": self.primary.url,
            "POWERMEM_FALLBACK_BASE_URL": self.fallback.url,
            "POWERMEM_INFER_TRANSCRIPT": "0",
            "POWERMEM_INFER_COMPACT": "0",
            "POWERMEM_INFER_FILE": "0",
            "POWERMEM_PROMPT_SEARCH": "1",
            "POWERMEM_DATA_DIR": str(tmp_path / "powermem-data"),
            "POWERMEM_USER_ID": "dual-user",
            "POWERMEM_AGENT_ID": "dual-agent",
            # Short TTLs so cached-state tests don't sleep for 30s.
            "POWERMEM_FALLBACK_DOWN_TTL_SECONDS": "2",
            "POWERMEM_FALLBACK_UP_TTL_SECONDS": "2",
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

    def _run_hook(
        self,
        payload: dict[str, Any],
        tmp_path: Path,
        **env_overrides: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(self.hook_bin)],
            cwd=ROOT,
            env=self._env(tmp_path, **env_overrides),
            input=json.JSONEncoder().encode(payload),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=20,
        )

    def _state_file(self, tmp_path: Path) -> Path:
        return tmp_path / "powermem-data" / "fallback-state.json"

    def _read_state(self, tmp_path: Path) -> dict[str, Any]:
        path = self._state_file(tmp_path)
        if not path.exists():
            return {}
        with path.open() as f:
            return json.load(f)

    def _wait_for_records(
        self,
        server: ControllablePowerMemServer,
        path: str,
        *,
        timeout: float = 3.0,
    ) -> RecordedRequest:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for request in server.records():
                if request.path == path:
                    return request
            time.sleep(0.05)
        self.fail(f"no request to {path} recorded on {server.label}")

    def _wait_for_no_records(
        self,
        server: ControllablePowerMemServer,
        path: str,
        *,
        timeout: float = 0.5,
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            matches = [r for r in server.records() if r.path == path]
            if matches:
                self.fail(f"unexpected {path} request on {server.label}: {matches[-1]}")
            time.sleep(0.05)

    # ------------------------------------------------------------------
    # Test cases
    # ------------------------------------------------------------------

    def test_both_up_routes_to_primary(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            payload = _load_user_prompt_submit_fixture()
            result = self._run_hook(payload, tmp_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            request = self._wait_for_records(self.primary, "/api/v1/memories/search")
            self._wait_for_no_records(self.fallback, "/api/v1/memories/search")

            state = self._read_state(tmp_path)
            self.assertFalse(state.get("primary_down"))

            output = json.loads(result.stdout)
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertIn("memory from primary", context)

    def test_primary_down_routes_to_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            self.primary.stop()

            payload = _load_user_prompt_submit_fixture()
            result = self._run_hook(payload, tmp_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            request = self._wait_for_records(self.fallback, "/api/v1/memories/search")
            self._wait_for_no_records(self.primary, "/api/v1/memories/search")

            state = self._read_state(tmp_path)
            self.assertTrue(state.get("primary_down"))

            output = json.loads(result.stdout)
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertIn("memory from fallback", context)

    def test_cached_down_skips_primary_probe(self) -> None:
        # Seed the state file with primary_down=true and a fresh last_probe_at,
        # then verify the hook skips the probe and goes straight to fallback
        # even though primary is actually up.
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            data_dir = tmp_path / "powermem-data"
            data_dir.mkdir(parents=True)
            state_path = data_dir / "fallback-state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "primary_down": True,
                        "last_probe_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    }
                )
            )

            self.primary.clear()
            self.fallback.clear()
            payload = _load_user_prompt_submit_fixture()
            result = self._run_hook(
                payload,
                tmp_path,
                POWERMEM_FALLBACK_DOWN_TTL_SECONDS="300",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self._wait_for_records(self.fallback, "/api/v1/memories/search")
            self._wait_for_no_records(self.primary, "/api/v1/memories/search")

    def test_recovery_marks_primary_up(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)

            # First invocation: primary down, fallback hit, state marked down.
            self.primary.stop()
            payload = _load_user_prompt_submit_fixture()
            r1 = self._run_hook(payload, tmp_path)
            self.assertEqual(r1.returncode, 0, r1.stderr)
            self._wait_for_records(self.fallback, "/api/v1/memories/search")
            self.assertTrue(self._read_state(tmp_path).get("primary_down"))

            # Bring primary back. Rewrite state with an old last_probe_at so
            # the down TTL is definitely expired (regardless of the env clamp
            # minimum of 5s) — no sleep needed.
            self.primary.restart()
            data_dir = tmp_path / "powermem-data"
            state_path = data_dir / "fallback-state.json"
            state_path.write_text(
                json.dumps(
                    {
                        "primary_down": True,
                        "last_probe_at": time.strftime(
                            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 3600)
                        ),
                    }
                )
            )
            self.primary.clear()
            self.fallback.clear()
            r2 = self._run_hook(payload, tmp_path)
            self.assertEqual(r2.returncode, 0, r2.stderr)
            self._wait_for_records(self.primary, "/api/v1/memories/search")
            self._wait_for_no_records(self.fallback, "/api/v1/memories/search")

            state = self._read_state(tmp_path)
            self.assertFalse(state.get("primary_down"))

    def test_primary_5xx_triggers_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            self.primary.fail_search = True

            payload = _load_user_prompt_submit_fixture()
            result = self._run_hook(payload, tmp_path)

            self.assertEqual(result.returncode, 0, result.stderr)
            self._wait_for_records(self.primary, "/api/v1/memories/search")
            self._wait_for_records(self.fallback, "/api/v1/memories/search")

            state = self._read_state(tmp_path)
            self.assertTrue(state.get("primary_down"))

            output = json.loads(result.stdout)
            context = output["hookSpecificOutput"]["additionalContext"]
            self.assertIn("memory from fallback", context)

    def test_primary_5xx_no_trigger_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            self.primary.fail_search = True

            payload = _load_user_prompt_submit_fixture()
            result = self._run_hook(
                payload,
                tmp_path,
                POWERMEM_FALLBACK_TRIGGER_5XX="0",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self._wait_for_records(self.primary, "/api/v1/memories/search")
            self._wait_for_no_records(self.fallback, "/api/v1/memories/search")
            # No state file should be written — 5xx with TRIGGER_5XX=0 does not
            # count as a fallback trigger.
            self.assertFalse(self._state_file(tmp_path).exists())

    def test_fallback_disabled_kill_switch(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            self.primary.stop()

            payload = _load_user_prompt_submit_fixture()
            result = self._run_hook(
                payload,
                tmp_path,
                POWERMEM_FALLBACK_DISABLED="1",
            )

            # Hook returns 0 (no error surfaces to Claude Code), but no
            # additionalContext is emitted and no fallback request is made.
            self.assertEqual(result.returncode, 0, result.stderr)
            self._wait_for_no_records(self.fallback, "/api/v1/memories/search")
            self.assertEqual(result.stdout, "")
            # Kill switch must not write state.
            self.assertFalse(self._state_file(tmp_path).exists())

    def test_single_backend_fast_path_no_state_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp_path = Path(raw_tmp)
            self.primary.stop()

            payload = _load_user_prompt_submit_fixture()
            result = self._run_hook(
                payload,
                tmp_path,
                POWERMEM_FALLBACK_BASE_URL="",  # unset → single-backend fast path
            )
            # Remove the env entirely; an empty string is the same as unset for
            # the hook's fallbackBaseURL() helper.
            self.assertEqual(result.returncode, 0, result.stderr)
            self._wait_for_no_records(self.fallback, "/api/v1/memories/search")
            self.assertEqual(result.stdout, "")
            self.assertFalse(self._state_file(tmp_path).exists())


if __name__ == "__main__":
    unittest.main()
