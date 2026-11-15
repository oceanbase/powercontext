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

"""Run pinned upstream compatibility suites through actual gateway processes.

Python 3.11+, Node/npm, Go 1.26+, and a built Rust gateway are required.
The upstream tests and their assertions are used without modification.
"""

from __future__ import annotations

import argparse
import http.client
import json
import shutil
import socket
import subprocess
import threading
import time
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
TCK = ROOT / "gateway/tck"
PINS = json.loads((TCK / "upstreams.json").read_text())
FIXTURES = ROOT / "gateway/crates/pc-gateway-app/tests/fixtures/republic/cases"


def checkout(name, cache):
    pin = PINS[name]
    git = shutil.which("git")
    assert git is not None, "git is required"
    directory = cache / f"{name}-{pin['commit']}"
    if not directory.exists():
        subprocess.run([git, "init", "--quiet", str(directory)], check=True)
        subprocess.run(
            [git, "-C", str(directory), "fetch", "--quiet", "--depth=1", pin["repository"], pin["commit"]], check=True
        )
        subprocess.run([git, "-C", str(directory), "checkout", "--quiet", "--detach", "FETCH_HEAD"], check=True)
    actual = subprocess.check_output([git, "-C", str(directory), "rev-parse", "HEAD"], text=True).strip()
    assert actual == pin["commit"], (directory, actual, pin["commit"])
    subprocess.run([git, "-C", str(directory), "diff", "--exit-code", "HEAD", "--"], check=True)
    return directory


def free_port():
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        return reserved.getsockname()[1]


@contextmanager
def process(command, env, health, log):
    child = subprocess.Popen(command, env=env, stdout=log, stderr=log)
    try:
        deadline = time.monotonic() + 15
        parsed = urlsplit(health)
        assert parsed.scheme == "http" and parsed.hostname == "127.0.0.1", health
        while time.monotonic() < deadline:
            assert child.poll() is None, ("startup_exit", command)
            try:
                connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=1)
                try:
                    connection.request("GET", parsed.path, headers={"Authorization": "Bearer gateway-test-key"})
                    response = connection.getresponse()
                    if response.status == 200:
                        break
                finally:
                    connection.close()
            except OSError:
                pass
            time.sleep(0.025)
        else:
            raise TimeoutError("startup_timeout", command)
        yield
    finally:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()


@contextmanager
def core_and_recordings():
    """Script only Core verdicts and model I/O; production code owns proxy behavior."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 - Match the standard library override signature.
            pass

        def send_body(self, body, content_type="application/json"):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/v1/capabilities":
                # The gateway pauses model routes unless startup discovery returns the complete v1
                # capability document, including the input capacity limits; this scripted Core
                # advertises the same ceiling as Core's complete-body parser.
                body = {
                    "context_protection": {
                        "enforcing": True,
                        "check_versions": ["powercontext.context-check.v1"],
                        "payload_versions": ["powercontext.model-json-payload.v1"],
                        "reuse_versions": [],
                        "reuse_families": [],
                        "limits": {
                            "payload_bytes": 1_048_576,
                            "binding_bytes": 8192,
                            "prepared_contexts": 16,
                            "dependencies": 256,
                            "send_seconds": 60,
                            "correlate_seconds": 300,
                        },
                    }
                }
            else:
                body = {"object": "list", "data": [{"id": "gpt-4o-mini", "object": "model"}]}
            self.send_body(json.dumps(body).encode())

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/v1/context/check":
                phase = request["phase"]
                body = {
                    "schema": "powercontext.context-check.v1",
                    "phase": phase,
                    "decision": "allow",
                    "reason_codes": [],
                    "coverage": {"profile": "text-and-tools.v1", "complete": True},
                    "policy_version": "test",
                    "valid_until": "2999-01-01T00:00:00Z",
                    "binding": f"test-{phase}",
                }
                self.send_body(json.dumps(body).encode())
                return
            case = "tool" if request.get("tools") else "simple"
            path = FIXTURES / f"fantasy_responses_{case}"
            if request.get("stream"):
                self.send_body((path.with_name(path.name + "_streaming") / "0.sse").read_bytes(), "text/event-stream")
            else:
                self.send_body((path / "0.json").read_bytes())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@contextmanager
def target(name, args, upstream, core, directory):
    """Exercise the same unmodified suite directly and through the Rust gateway."""
    if name == "direct":
        yield upstream, "gpt-4o-mini"
        return
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {
        "PC_GATEWAY_LISTEN": f"127.0.0.1:{port}",
        "PC_GATEWAY_CORE_BASE": core,
        "PC_GATEWAY_UPSTREAM_URL_CHAT": f"{upstream}/chat/completions",
        "PC_GATEWAY_UPSTREAM_URL_RESPONSES": f"{upstream}/responses",
        "PC_GATEWAY_UPSTREAM_API_KEY": "provider-test-key",
        "PC_GATEWAY_API_KEY": "gateway-test-key",
        "PC_GATEWAY_INJECTION": "off",
        "PC_GATEWAY_SEDIMENT": "off",
    }
    with (
        (directory / "gateway.log").open("w") as log,
        process([str(args.gateway.resolve())], env, f"{base}/healthz", log),
    ):
        yield f"{base}/gateway/v1", "gpt-4o-mini"


def run_suite(kind, name, command, directory):
    path = directory / f"{kind}-{name}.log"
    with path.open("w") as output:
        result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, timeout=180, check=False)
    print(f"{'PASS' if result.returncode == 0 else 'FAIL'} {kind}: {name} ({path})", flush=True)
    if result.returncode:
        print(path.read_text(), flush=True)
    return {"suite": kind, "target": name, "exit_code": result.returncode, "log": str(path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gateway", type=Path, default=ROOT / "gateway/target/debug/pc-gateway")
    parser.add_argument("--output", type=Path, default=ROOT / ".artifacts/gateway-tck")
    parser.add_argument("--cache", type=Path, default=ROOT / ".cache/gateway-tck")
    parser.add_argument("--go", default="go")
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    args.cache.mkdir(parents=True, exist_ok=True)
    sdk = checkout("openai_sdk", args.cache)
    responses = checkout("openresponses", args.cache)
    modules = responses / "node_modules"
    if not modules.exists():
        modules.symlink_to(TCK / "node_modules", target_is_directory=True)
    bun = TCK / "node_modules/.bin/bun"
    assert bun.is_file(), "Run npm ci --prefix gateway/tck first"
    binaries = args.cache / f"bin-{PINS['openai_sdk']['commit']}"
    binaries.mkdir(exist_ok=True)
    subprocess.run(
        [
            args.go,
            "build",
            "-mod=readonly",
            "-o",
            str(binaries.resolve()) + "/",
            "./cmd/openai-compatibility-tester",
            "./cmd/mockserver",
        ],
        cwd=sdk,
        check=True,
    )
    targets = ("direct", "powercontext")
    results = []
    with core_and_recordings() as core, ExitStack() as stack:
        native_port = free_port()
        native = f"http://127.0.0.1:{native_port}/v1"
        log = stack.enter_context((args.output / "mockserver.log").open("w"))
        stack.enter_context(
            process(
                [str(binaries.resolve() / "mockserver"), "--addr", f"127.0.0.1:{native_port}"],
                {},
                f"{native}/models",
                log,
            )
        )
        for kind, upstream in (("openai_sdk", native), ("openresponses", f"{core}/v1")):
            for name in targets:
                directory = args.output / kind / name
                directory.mkdir(parents=True, exist_ok=True)
                with target(name, args, upstream, core, directory) as (base, model):
                    if kind == "openai_sdk":
                        command = [
                            str(binaries.resolve() / "openai-compatibility-tester"),
                            "--base-url",
                            base,
                            "--api-key",
                            "gateway-test-key",
                            "--model",
                            model,
                            "--reasoning-model",
                            model,
                            "--suites",
                            ",".join(PINS[kind]["tests"]),
                            "--timeout",
                            "20s",
                        ]
                    else:
                        command = [
                            str(bun),
                            str(responses / "bin/compliance-test.ts"),
                            "--base-url",
                            base,
                            "--api-key",
                            "gateway-test-key",
                            "--model",
                            model,
                            "--filter",
                            ",".join(PINS[kind]["tests"]),
                            "--json",
                        ]
                    result = run_suite(kind, name, command, directory)
                    results.append(result)
                    if name == "direct" and result["exit_code"]:
                        break
    report = {
        "upstreams": PINS,
        "scope": "Real gateway subprocesses with scripted Core verdicts and controlled model I/O",
        "results": results,
    }
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    raise SystemExit(int(any(result["exit_code"] for result in results)))


if __name__ == "__main__":
    main()
