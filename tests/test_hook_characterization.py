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

"""Claude Code and Codex hooks keep the behavior that their host and the Server observe.

Each case runs an installed copy of a plugin's hook scripts with what its host gives them: a native
payload on stdin, the plugin's environment, the packages the host provides, and a Server that answers
from a script. Claude Code starts its hooks with ``python3``, so they run on an interpreter without site
packages; Codex starts them with ``uv run --frozen``, so they run in an environment built from the
plugin's lock file. The fixtures record what crosses the process boundary: the requests the Server
receives, the hook's stdout, stderr, and exit status, and the files it leaves in the case directory for
later hooks. A refactoring leaves the fixtures unchanged. After a reviewed behavior change, rewrite the
expectations with ``uv run python tests/test_hook_characterization.py`` and review the fixture diff.

A fixture names the Server responses its cases use and the request header sets it expects. Tokens
such as ``$CWD``, ``$SERVER_URL``, and ``$WORKSPACE_KEY`` stand for the paths, address, and keys of
each run, and ``$NOW`` for a time written during the case. JSON objects are recorded with sorted keys,
because no reader of what the hooks write depends on their order.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock, Thread
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="the recorded state includes POSIX file modes")

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/hook_characterization"
PLUGINS = {
    "claude-code": ROOT / "integrations/claude-code/plugins/powercontext",
    "codex": ROOT / "integrations/codex/plugins/powercontext",
}
MANIFESTS = {"claude-code": ".claude-plugin/plugin.json", "codex": ".codex-plugin/plugin.json"}
FIXTURE_DATA = {host: json.loads((FIXTURES / f"{host}.json").read_text(encoding="utf-8")) for host in PLUGINS}
CASES = [(host, case) for host, fixture in FIXTURE_DATA.items() for case in fixture["cases"]]
# urllib adds these to every request; no hook chooses them.
_TRANSPORT_HEADERS = frozenset({"accept-encoding", "connection", "content-length", "host"})
# The installed plugin and the repository metadata belong to the harness, not to the hooks.
_UNRECORDED = ("plugin", "workspace/.git")
# A longer string is recorded by its length, digest, and ends, which keeps a long prompt reviewable.
_LONG_STRING = 512
_ISO_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})")


@pytest.fixture(scope="session")
def hook_commands(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[str]]:
    return _hook_commands(tmp_path_factory.mktemp("hook-environments"))


@pytest.mark.parametrize(("host", "case"), CASES, ids=[f"{host}:{case['id']}" for host, case in CASES])
def test_hook_keeps_its_characterized_behavior(
    tmp_path: Path,
    hook_commands: dict[str, list[str]],
    host: str,
    case: dict[str, Any],
) -> None:
    observed, _ = _run_case(host, FIXTURE_DATA[host], case, tmp_path, hook_commands[host])

    for number, (step, actual) in enumerate(zip(case["steps"], observed, strict=True), start=1):
        assert actual.keys() == step["expected"].keys()
        for field, expected in step["expected"].items():
            assert actual[field] == expected, f"step {number} ({step['hook']}) changed its {field}"


class _ScriptedServer:
    """Answer each ``METHOD path`` route from a script and record requests in arrival order.

    A route returns its scripted responses in turn and then repeats the last one. A route without a
    script answers HTTP 500, so the recorded requests show it. A response is a status with a JSON value
    or a text body, extra headers, and a delay before it starts, or else a dropped connection. A
    response value ``"$REQUEST.name"`` echoes that field of the JSON request, as the Server echoes
    identities it was sent.
    """

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.unscripted: list[str] = []
        self._routes: dict[str, list[dict[str, Any]]] = {}
        self._lock = Lock()
        answer = self._answer

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                answer(self)

            def do_PUT(self) -> None:
                answer(self)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - BaseHTTPRequestHandler override.
                pass

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"

    @contextmanager
    def running(self) -> Iterator[_ScriptedServer]:
        thread = Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        thread.start()
        try:
            yield self
        finally:
            self._server.shutdown()
            thread.join()
            self._server.server_close()

    def script(self, routes: Mapping[str, list[dict[str, Any]]]) -> None:
        with self._lock:
            self._routes = {route: list(responses) for route, responses in routes.items()}
            self.requests = []
            self.unscripted = []

    def _answer(self, handler: BaseHTTPRequestHandler) -> None:
        raw = handler.rfile.read(int(handler.headers.get("Content-Length") or 0))
        route = f"{handler.command} {handler.path}"
        request = _decoded(raw)
        with self._lock:
            self.requests.append({
                "method": handler.command,
                "path": handler.path,
                "headers": dict(
                    sorted(
                        (name.lower(), value)
                        for name, value in handler.headers.items()
                        if name.lower() not in _TRANSPORT_HEADERS
                    )
                ),
                "body": request,
            })
            responses = self._routes.get(route)
            if not responses:
                self.unscripted.append(route)
                response: dict[str, Any] = {"status": 500, "json": {"error": {"code": "unscripted_route"}}}
            else:
                response = responses.pop(0) if len(responses) > 1 else responses[0]
        if response.get("disconnect"):
            handler.close_connection = True
            return
        time.sleep(response.get("delay", 0))
        body = (
            json.dumps(_echoed(response["json"], request)).encode()
            if "json" in response
            else response.get("text", "").encode()
        )
        try:
            handler.send_response(response.get("status", 200))
            handler.send_header("Content-Type", "application/json")
            for name, value in response.get("headers", {}).items():
                handler.send_header(name, value)
            handler.send_header("Content-Length", str(len(body)))
            handler.end_headers()
            handler.wfile.write(body)
        except OSError:
            # A hook that stopped waiting has already closed the connection.
            handler.close_connection = True


def _hook_commands(directory: Path) -> dict[str, list[str]]:
    """Return the command that starts each host's hooks with the packages that host provides."""

    project = directory / "codex"
    project.mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(PLUGINS["codex"] / name, project / name)
    environment = {name: value for name, value in os.environ.items() if name != "VIRTUAL_ENV"}
    environment["UV_PROJECT_ENVIRONMENT"] = str(project / ".venv")
    subprocess.run(
        ["uv", "sync", "--frozen", "--quiet", "--project", str(project), "--python", sys.executable],  # noqa: S607
        check=True,
        capture_output=True,
        env=environment,
        timeout=300,
    )
    return {"claude-code": [sys.executable, "-S"], "codex": [str(project / ".venv/bin/python")]}


def _run_case(
    host: str,
    fixture: Mapping[str, Any],
    case: Mapping[str, Any],
    directory: Path,
    command: list[str],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Run the steps of one case in one isolated host home; return the observations and unscripted routes."""

    root = directory.resolve()
    home, state, config, workspace = (root / name for name in ("home", "state", "config", "workspace"))
    layout = case.get("workspace", {})
    cwd = workspace / layout.get("cwd", "")
    for path in (home, state, config, cwd):
        path.mkdir(parents=True, exist_ok=True)
    environment = {
        "PATH": os.environ.get("PATH", os.defpath),
        "HOME": str(home),
        # Git must not find a repository above the case directory.
        "GIT_CEILING_DIRECTORIES": str(root),
        # Without proxy variables, urllib on macOS would read the system proxy settings.
        "no_proxy": "*",
    }
    if layout.get("git"):
        if shutil.which("git") is None:
            pytest.skip("a repository workspace needs git")
        subprocess.run(["git", "init", "--quiet", str(workspace)], check=True, capture_output=True, env=environment)  # noqa: S607
    server = _ScriptedServer()
    observed: list[dict[str, Any]] = []
    unscripted: list[str] = []
    with server.running():
        plugin = _installed_plugin(host, root / "plugin", server.url)
        tokens = {
            "$SERVER_URL": server.url,
            "$CWD": str(cwd),
            "$WORKSPACE": str(workspace),
            "$PLUGIN": str(plugin),
            "$STATE": str(state),
            "$CONFIG": str(config),
            "$HOME": str(home),
            "$ROOT": str(root),
            "$WORKSPACE_KEY": _path_key(workspace if layout.get("git") else cwd),
            "$CWD_KEY": _path_key(cwd),
        }
        environment.update(_substituted(fixture["environment"], tokens))
        _write_files(case.get("files", {}), tokens)
        # A directory that exists before the first step was made by the harness, whose umask is not the hooks'.
        harness_directories = frozenset(Path(path) for path, _, _ in os.walk(root))
        version = json.loads((PLUGINS[host] / MANIFESTS[host]).read_text(encoding="utf-8"))["version"]
        shared = fixture.get("responses", {})
        header_sets = fixture.get("headers", {})
        # A time written by any step of the case reads as $NOW, so a slow host cannot move it out of the window.
        case_started = time.time()
        for step in case["steps"]:
            server.script({
                route: [
                    _substituted(_expanded(shared[entry] if isinstance(entry, str) else entry), tokens)
                    for entry in entries
                ]
                for route, entries in step.get("server", {}).items()
            })
            step_environment = {**environment, **_substituted(step.get("environment", {}), tokens)}
            # The process runs outside the workspace, so the payload's cwd is the hook's only workspace signal.
            completed = subprocess.run(
                [*command, str(plugin / step["hook"])],
                input=_stdin(step, tokens),
                capture_output=True,
                cwd=home,
                env={name: value for name, value in step_environment.items() if value is not None},
                timeout=60,
                umask=0o022,
                check=False,
            )
            window = (case_started - 1, time.time() + 1)
            for request in server.requests:
                agent = request["headers"].get("user-agent")
                if agent is not None:
                    request["headers"]["user-agent"] = agent.replace(f"/{version}", "/$PLUGIN_VERSION")
            observation = {
                "exit_code": completed.returncode,
                "requests": server.requests,
                "stdout": _output_lines(completed.stdout),
                "stderr": _output_lines(completed.stderr),
                "state": _case_files(root, harness_directories),
            }
            normalized = _normalized(observation, _replacements(tokens), window)
            for request in normalized["requests"]:
                # A header set that the fixture names is recorded by its name.
                request["headers"] = next(
                    (name for name, headers in header_sets.items() if headers == request["headers"]),
                    request["headers"],
                )
            observed.append(normalized)
            unscripted.extend(server.unscripted)
    return observed, unscripted


def _installed_plugin(host: str, target: Path, server_url: str) -> Path:
    shutil.copytree(PLUGINS[host], target, ignore=shutil.ignore_patterns("__pycache__", ".venv"))
    if host == "codex":
        # Codex hooks read their endpoint from the MCP configuration installed beside them.
        configuration = target / ".mcp.json"
        value = json.loads(configuration.read_text(encoding="utf-8"))
        value["mcpServers"]["powercontext"]["url"] = f"{server_url}/mcp/"
        configuration.write_text(json.dumps(value), encoding="utf-8")
    return target


def _path_key(path: Path) -> str:
    return hashlib.sha256(os.fsencode(path.resolve())).hexdigest()


def _write_files(files: Mapping[str, Mapping[str, Any]], tokens: Mapping[str, str]) -> None:
    for name, file in files.items():
        path = Path(_substituted(name, tokens))
        path.parent.mkdir(parents=True, exist_ok=True)
        content = (
            json.dumps(_substituted(file["json"], tokens)) if "json" in file else _substituted(file["text"], tokens)
        )
        path.write_text(content, encoding="utf-8")
        path.chmod(int(file.get("mode", "600"), 8))


def _stdin(step: Mapping[str, Any], tokens: Mapping[str, str]) -> bytes:
    if "stdin" in step:
        return step["stdin"].encode()
    return json.dumps(_substituted(_expanded(step["payload"]), tokens)).encode()


def _expanded(value: Any) -> Any:
    """Expand ``{"$concat": [{"text": ..., "repeat": n}, ...]}`` into the string it describes."""

    if isinstance(value, dict):
        if set(value) == {"$concat"}:
            return "".join(segment["text"] * segment.get("repeat", 1) for segment in value["$concat"])
        return {key: _expanded(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expanded(item) for item in value]
    return value


def _substituted(value: Any, tokens: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        # Longer tokens first, so that $CWD_KEY is not read as $CWD followed by text.
        for token in sorted(tokens, key=len, reverse=True):
            value = value.replace(token, tokens[token])
        return value
    if isinstance(value, dict):
        return {_substituted(key, tokens): _substituted(item, tokens) for key, item in value.items()}
    if isinstance(value, list):
        return [_substituted(item, tokens) for item in value]
    return value


def _replacements(tokens: Mapping[str, str]) -> list[tuple[str, str]]:
    """Map observed values back to tokens; the first token listed for a value wins, longer values go first."""

    first: dict[str, str] = {}
    for token, value in tokens.items():
        first.setdefault(value, token)
    return sorted(first.items(), key=lambda item: len(item[0]), reverse=True)


def _echoed(value: Any, request: Any) -> Any:
    if isinstance(value, str) and value.startswith("$REQUEST."):
        return request.get(value.removeprefix("$REQUEST.")) if isinstance(request, dict) else None
    if isinstance(value, dict):
        return {key: _echoed(item, request) for key, item in value.items()}
    if isinstance(value, list):
        return [_echoed(item, request) for item in value]
    return value


def _json(text: str | bytes) -> Any:
    return json.loads(text, object_pairs_hook=lambda pairs: dict(sorted(pairs, key=lambda pair: pair[0])))


def _decoded(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return _json(raw)
    except ValueError:
        return {"$raw": raw.decode("utf-8", "replace")}


def _output_lines(raw: bytes) -> list[Any]:
    lines = raw.decode("utf-8", "replace").splitlines()
    return [_decoded(line.encode()) for line in lines if line.strip()]


def _case_files(root: Path, harness_directories: frozenset[Path]) -> dict[str, dict[str, Any]]:
    """Record every file in the case directory except locks, and each directory a hook made, by its mode."""

    excluded = {root / name for name in _UNRECORDED}
    recorded: dict[str, dict[str, Any]] = {}
    for directory, subdirectories, files in os.walk(root):
        current = Path(directory)
        subdirectories[:] = [name for name in subdirectories if current / name not in excluded]
        if current not in harness_directories:
            recorded[f"{current}/"] = {"mode": _mode(current)}
        for name in files:
            if not name.endswith(".lock"):
                recorded[str(current / name)] = {"mode": _mode(current / name), **_file_content(current / name)}
    return dict(sorted(recorded.items()))


def _mode(path: Path) -> str:
    return format(stat.S_IMODE(path.stat().st_mode), "o")


def _file_content(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        return {"json": _json(text)}
    except ValueError:
        pass
    try:
        return {"json_lines": [_json(line) for line in text.splitlines()]}
    except ValueError:
        return {"text": text}


def _normalized(value: Any, replacements: list[tuple[str, str]], window: tuple[float, float]) -> Any:
    """Replace case paths, keys, and times written during the case with tokens; summarize long strings."""

    if isinstance(value, dict):
        return {
            _normalized(key, replacements, window): _normalized(item, replacements, window)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalized(item, replacements, window) for item in value]
    if isinstance(value, (int, float)) and not isinstance(value, bool) and window[0] <= value <= window[1]:
        return "$NOW"
    if not isinstance(value, str):
        return value
    if _ISO_TIMESTAMP.fullmatch(value):
        moment = datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        if window[0] <= moment <= window[1]:
            return "$NOW"
    for actual, token in replacements:
        value = value.replace(actual, token)
    if len(value) > _LONG_STRING:
        return {
            "$long": {
                "length": len(value),
                "sha256": hashlib.sha256(value.encode()).hexdigest(),
                "start": value[:40],
                "end": value[-40:],
            }
        }
    return value


def _record() -> None:
    """Rewrite every expectation from the hooks' current behavior."""

    with tempfile.TemporaryDirectory() as environments:
        commands = _hook_commands(Path(environments))
        for host, fixture in FIXTURE_DATA.items():
            identifiers = [case["id"] for case in fixture["cases"]]
            if len(set(identifiers)) != len(identifiers):
                raise SystemExit(f"{host}: case ids must be unique")  # noqa: TRY003
            for case in fixture["cases"]:
                with tempfile.TemporaryDirectory() as directory:
                    observed, unscripted = _run_case(host, fixture, case, Path(directory), commands[host])
                if unscripted:
                    raise SystemExit(f"{host}:{case['id']} reached unscripted routes: {', '.join(unscripted)}")  # noqa: TRY003
                for step, expected in zip(case["steps"], observed, strict=True):
                    step["expected"] = expected
            text = json.dumps(fixture, indent=2, ensure_ascii=False) + "\n"
            (FIXTURES / f"{host}.json").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    _record()
