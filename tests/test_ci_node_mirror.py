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

"""Installer routing must preserve failures, lock integrity, and non-CI behavior."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/ci_node_mirror.mjs"
NODE = shutil.which("node")
NPM = shutil.which("npm")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is required for the CI installer")


@pytest.fixture
def installer(tmp_path: Path):
    binary = tmp_path / "bin"
    binary.mkdir()
    executable = binary / "npm"
    executable.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "if 'config' in sys.argv and sys.argv[-2:] == ['get', 'registry']:\n"
        " print(os.environ.get('TEST_REGISTRY', 'https://registry.npmjs.org/')); sys.exit(0)\n"
        "with open(os.environ['TEST_CALLS'], 'a') as f:\n"
        " f.write(json.dumps({'args':sys.argv[1:], 'no_proxy':os.environ.get('no_proxy', '')})+'\\n')\n"
        "mirror = any('registry.npmmirror.com' in x for x in sys.argv)\n"
        "if mirror and os.environ.get('TEST_ERROR'):\n"
        " print(os.environ['TEST_ERROR'], file=sys.stderr); sys.exit(1)\n"
        "if not mirror and os.environ.get('TEST_OFFICIAL_ERROR'):\n"
        " print(os.environ['TEST_OFFICIAL_ERROR'], file=sys.stderr); sys.exit(7)\n"
    )
    executable.chmod(0o755)
    (binary / "pnpm").symlink_to(executable)
    calls = tmp_path / "calls.jsonl"
    environment = {**os.environ, "PATH": f"{binary}{os.pathsep}{os.environ['PATH']}", "TEST_CALLS": str(calls)}
    environment.update(RUNNER_OS="Linux", RUNNER_ENVIRONMENT="self-hosted")
    for key in list(environment):
        if key.lower().startswith("npm_config_") or key.startswith("PC_CI_NPM_"):
            environment.pop(key)

    def run(*arguments: str, env: dict[str, str] | None = None):
        assert NODE is not None
        result = subprocess.run(
            [NODE, str(SCRIPT), *arguments], env=environment | (env or {}), capture_output=True, text=True
        )
        records = [json.loads(line) for line in calls.read_text().splitlines()] if calls.exists() else []
        return result, records

    return run


def test_official_installer_uses_direct_mirror_and_keeps_version_and_flags(installer):
    result, calls = installer(
        "npm", "install", "example@1.2.3", "--ignore-scripts", "--registry=https://registry.npmjs.org/"
    )
    assert result.returncode == 0, result.stderr
    assert calls[0]["args"] == [
        "install",
        "example@1.2.3",
        "--ignore-scripts",
        "--registry=https://registry.npmmirror.com/",
    ]
    assert "registry.npmmirror.com" in calls[0]["no_proxy"].split(",")


@pytest.mark.parametrize(
    "manager,error",
    [
        ("npm", "npm error code ECONNRESET"),
        ("pnpm", "ERR_PNPM_FETCH_503 GET mirror failed"),
        ("pnpm", "[ERROR] fetch failed\nTypeError: fetch failed\n    at Object.processResponse (pnpm.mjs:82959:22)"),
    ],
)
def test_registry_failure_falls_back_to_official_source(installer, manager, error):
    result, calls = installer(manager, "install", "example@1.2.3", env={"TEST_ERROR": error})
    assert result.returncode == 0, result.stderr
    assert len(calls) == 2
    assert "--registry=https://registry.npmmirror.com/" in calls[0]["args"]
    assert "--registry=https://registry.npmjs.org/" in calls[1]["args"]


def test_official_fallback_failure_is_returned_without_another_retry(installer):
    result, calls = installer(
        "npm", "install", env={"TEST_ERROR": "npm error code ECONNRESET", "TEST_OFFICIAL_ERROR": "npm error code E404"}
    )
    assert result.returncode == 7
    assert len(calls) == 2
    assert "npm error code E404" in result.stderr


@pytest.mark.parametrize(
    "manager,error",
    [
        ("npm", "npm error code 1\nnpm error command failed\nECONNRESET in test"),
        ("pnpm", "ELIFECYCLE Command failed with exit code 1.\nECONNRESET in test"),
        ("pnpm", "ERR_PNPM_FETCH_503 retrying\nERR_PNPM_PEER_DEP_ISSUES unmet peers"),
        ("pnpm", "[ERROR] fetch failed\nTypeError: fetch failed\nELIFECYCLE Command failed with exit code 1."),
        ("pnpm", "[ERROR] fetch failed\nTypeError: fetch failed\nERR_PNPM_RECURSIVE_RUN_FIRST_FAIL failed postinstall"),
    ],
)
def test_lifecycle_failure_is_not_retried(installer, manager, error):
    result, calls = installer(manager, "install", env={"TEST_ERROR": error})
    assert result.returncode == 1
    assert len(calls) == 1


@pytest.mark.parametrize(
    "arguments,environment",
    [
        (("npm", "install", "example@1.2.3"), {"RUNNER_ENVIRONMENT": "github-hosted"}),
        (("npm", "install", "example@1.2.3"), {"RUNNER_OS": "Windows"}),
        (("npm", "publish"), {}),
        (("pnpm", "test"), {}),
        (("npm", "ci", "--offline"), {}),
        (("npm", "install", "--registry=https://private.example/"), {}),
        (("pnpm", "install"), {"TEST_REGISTRY": "https://private.example/"}),
    ],
)
def test_hosted_private_offline_and_noninstaller_commands_are_unchanged(installer, arguments, environment):
    result, calls = installer(*arguments, env=environment)
    assert result.returncode == 0, result.stderr
    assert calls[0]["args"] == list(arguments[1:])


def test_configure_leaves_hosted_runner_environment_unchanged(installer, tmp_path):
    output = tmp_path / "environment"
    result, calls = installer("configure", env={"GITHUB_ENV": str(output), "RUNNER_ENVIRONMENT": "github-hosted"})
    assert result.returncode == 0, result.stderr
    assert not output.exists()
    assert calls == []


def test_configure_exports_absolute_installer_for_internal_runner(installer, tmp_path):
    output = tmp_path / "environment"
    result, calls = installer("configure", env={"GITHUB_ENV": str(output)})
    assert result.returncode == 0, result.stderr
    assert output.read_text() == f"PC_CI_NODE_INSTALLER={SCRIPT.resolve()}\n"
    assert calls == []


def test_pnpm_directory_options_keep_project_selection(installer):
    result, calls = installer(
        "pnpm", "--dir", "path with spaces", "install", "--frozen-lockfile", "--config.auto-install-peers=false"
    )
    assert result.returncode == 0, result.stderr
    assert calls[0]["args"] == [
        "--dir",
        "path with spaces",
        "install",
        "--frozen-lockfile",
        "--config.auto-install-peers=false",
        "--registry=https://registry.npmmirror.com/",
    ]


@pytest.mark.skipif(NPM is None, reason="npm is required for the real locked installation regression")
def test_real_npm_ci_downloads_official_locked_tarball_from_mirror_without_lock_changes(tmp_path):
    assert NODE is not None
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        for name, content in {
            "package/package.json": b'{"name":"ci-mirror-fixture","version":"1.2.3"}',
            "package/index.js": b'module.exports = "locked";\n',
        }.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    artifact = payload.getvalue()
    requests = []

    class Registry(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", str(len(artifact)))
            self.end_headers()
            self.wfile.write(artifact)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002 -- stdlib HTTP handler interface
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Registry)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    package = {"name": "consumer", "version": "1.0.0", "dependencies": {"ci-mirror-fixture": "1.2.3"}}
    (tmp_path / "package.json").write_text(json.dumps(package))
    lock = {
        "name": "consumer",
        "version": "1.0.0",
        "lockfileVersion": 3,
        "packages": {
            "": package,
            "node_modules/ci-mirror-fixture": {
                "version": "1.2.3",
                "resolved": "https://registry.npmjs.org/ci-mirror-fixture/-/ci-mirror-fixture-1.2.3.tgz",
                "integrity": "sha512-" + base64.b64encode(hashlib.sha512(artifact).digest()).decode(),
            },
        },
    }
    original = json.dumps(lock).encode()
    (tmp_path / "package-lock.json").write_bytes(original)
    environment = {key: value for key, value in os.environ.items() if not key.lower().startswith("npm_config_")}
    environment["PC_CI_NPM_MIRROR"] = f"http://127.0.0.1:{server.server_port}"
    try:
        result = subprocess.run(
            [
                NODE,
                str(SCRIPT),
                "npm",
                "ci",
                "--ignore-scripts",
                "--no-audit",
                "--no-fund",
                "--cache",
                str(tmp_path / "cache"),
                "--registry=https://registry.npmjs.org/",
            ],
            env=environment,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "/ci-mirror-fixture/-/ci-mirror-fixture-1.2.3.tgz" in requests
    assert (tmp_path / "node_modules/ci-mirror-fixture/index.js").read_text() == 'module.exports = "locked";\n'
    assert (tmp_path / "package-lock.json").read_bytes() == original
