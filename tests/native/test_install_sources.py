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

"""Linux installer source acceptance with real uv and a loopback package index."""

from __future__ import annotations

import hashlib
import http.server
import io
import os
import shutil
import subprocess
import sys
import urllib.parse
import zipfile
from pathlib import Path
from threading import Thread
from typing import ClassVar

import pytest

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash")
UV = os.environ.get("POWERCONTEXT_INSTALL_UV") or shutil.which("uv")
pytestmark = pytest.mark.skipif(sys.platform != "linux" or not UV, reason="requires Linux and real uv")


def build_wheel(version: str) -> bytes:
    data = io.BytesIO()
    metadata = f"powercontext-{version}.dist-info"
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr(
            "fixture_cli.py",
            "from importlib.metadata import version\ndef main():\n    print(version('powercontext'))\n",
        )
        archive.writestr(
            f"{metadata}/METADATA",
            f"Metadata-Version: 2.1\nName: powercontext\nVersion: {version}\nProvides-Extra: cli\nProvides-Extra: server\n",
        )
        archive.writestr(f"{metadata}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr(f"{metadata}/entry_points.txt", "[console_scripts]\npowercontext = fixture_cli:main\n")
        archive.writestr(f"{metadata}/RECORD", "")
    return data.getvalue()


@pytest.fixture
def installation(tmp_path):  # noqa: C901 - one fixture owns the server and subprocess environment
    assert BASH
    bash_command: str = BASH
    wheels = {version: build_wheel(version) for version in ("1.0.0", "1.1.0", "1.2.0rc1")}

    class Index(http.server.BaseHTTPRequestHandler):
        mirror_status = 200
        mirror_versions: ClassVar[list[str]] = ["1.0.0", "1.1.0", "1.2.0rc1"]
        requests: ClassVar[list[str]] = []

        # Keep this controlled fixture compatible with standalone Python 3.11.
        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            pass

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            self.requests.append(self.path)
            path = urllib.parse.unquote(self.path)
            if path.startswith("/cn/") and self.mirror_status != 200:
                self.send_error(self.mirror_status)
                return
            if path.endswith("/powercontext/"):
                versions = self.mirror_versions if path.startswith("/cn/") else list(wheels)
                source = path.split("/")[1]
                links = []
                for version in versions:
                    name = f"powercontext-{version}-py3-none-any.whl"
                    # Valid URL encoding and opaque anchor text reproduce the filename-probe defect.
                    href = f"/{source}/files/{name}".replace(".", "%2E")
                    digest = hashlib.sha256(wheels[version]).hexdigest()
                    links.append(f'<a href="{href}#sha256={digest}">download</a>')
                payload = ("<!doctype html>" + "\n".join(links)).encode()
                content_type = "text/html"
            elif path.endswith(".whl"):
                payload = wheels[path.rsplit("/", 1)[-1].split("-")[1]]
                content_type = "application/octet-stream"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Index)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}"
    utilities = tmp_path / "utilities"
    utilities.mkdir()
    # Translate only distribution endpoints to fixtures; run the real downloader and uv.
    mappings = {
        "https://pypi.tuna.tsinghua.edu.cn/simple": base + "/cn/simple",
        "https://pypi.org/simple": base + "/global/simple",
        "https://explicit.invalid/simple": base + "/cn/simple",
    }
    for name, executable in (("uv", UV), ("curl", shutil.which("curl"))):
        assert executable
        wrapper = utilities / name
        wrapper.write_text(
            f"#!{sys.executable}\nimport os, sys\nmapping={mappings!r}\n"
            "args=[mapping.get(value, value) for value in sys.argv[1:]]\n"
            "args=[next((target + value[len(source):] for source,target in mapping.items() "
            "if value.startswith(source)), value) for value in args]\n"
            "for key in ('UV_DEFAULT_INDEX','UV_INDEX','UV_INDEX_URL'):\n"
            "    if key in os.environ: os.environ[key]=mapping.get(os.environ[key],os.environ[key])\n"
            f"os.execv({executable!r}, [{executable!r}, *args])\n",
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
    env = {
        "PATH": str(utilities) + os.pathsep + os.environ["PATH"],
        "HOME": str(tmp_path / "home"),
        "TMPDIR": str(tmp_path),
        "LANG": "C.UTF-8",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CONFIG_DIRS": str(tmp_path / "system-config"),
        "UV_TOOL_DIR": str(tmp_path / "tools"),
        "UV_TOOL_BIN_DIR": str(tmp_path / "bin"),
        "UV_CACHE_DIR": str(tmp_path / "cache"),
        "UV_PYTHON_INSTALL_DIR": str(tmp_path / "python"),
        "UV_HTTP_RETRIES": "0",
        "NO_PROXY": "127.0.0.1",
    }
    Path(env["HOME"]).mkdir()

    def install(*arguments, success=True, extra=None):
        completed = subprocess.run(
            [bash_command, str(ROOT / "website/public/install.sh"), "--no-hosts", *arguments],
            cwd=tmp_path,
            env=env | (extra or {}),
            text=True,
            capture_output=True,
            timeout=30,
        )
        assert (completed.returncode == 0) == success, completed.stdout + completed.stderr
        return completed.stdout + completed.stderr

    def version():
        return subprocess.check_output([str(tmp_path / "bin/powercontext"), "--version"], env=env, text=True).strip()

    try:
        yield install, version, Index, env
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.parametrize("region", ["global", "cn"])
def test_encoded_release_installs_through_public_script(installation, region):
    install, version, _index, _env = installation
    output = install("--region", region, "--version", "1.0.0")
    assert version() == "1.0.0"
    assert "Runtime installed: 1.0.0" in output


def test_latest_stable_and_explicit_prerelease(installation):
    install, version, _index, _env = installation
    install("--region", "cn")
    assert version() == "1.1.0"
    install("--region", "cn", "--version", "1.2.0rc1")
    assert version() == "1.2.0rc1"
    install("--region", "cn")
    assert version() == "1.1.0"


def test_unavailable_automatic_mirror_falls_back_before_tool_install(installation):
    install, version, index, _env = installation
    index.mirror_status = 503
    output = install("--region", "cn", "--version", "1.0.0")
    assert version() == "1.0.0"
    assert "Automatic package mirror unavailable; using PyPI." in output
    assert any(path.startswith("/global/") for path in index.requests)


def test_reachable_stale_mirror_preserves_request_and_installed_runtime(installation):
    install, version, index, _env = installation
    install("--region", "global", "--version", "1.1.0")
    index.requests.clear()
    index.mirror_versions = ["1.0.0"]
    output = install("--region", "cn", "--version", "1.2.0rc1", success=False)
    assert "Installation failed" in output
    assert version() == "1.1.0"
    assert not any(path.startswith("/global/") for path in index.requests)


def test_explicit_failed_index_has_no_automatic_fallback(installation):
    install, _version, index, _env = installation
    index.mirror_status = 403
    install("--region", "cn", "--index-url", "https://explicit.invalid/simple", "--version", "1.0.0", success=False)
    assert not any(path.startswith("/global/") for path in index.requests)


def test_unrelated_config_preserved_and_region_does_not_override_index(installation):
    install, version, index, env = installation
    config = Path(env["XDG_CONFIG_HOME"]) / "uv/uv.toml"
    config.parent.mkdir(parents=True)
    config.write_text("compile-bytecode = false\n", encoding="utf-8")
    original = config.read_bytes()
    # Existing uv settings provide the local source while conservative suppression preserves config.
    output = install("--region", "cn", "--version", "1.0.0", extra={"UV_DEFAULT_INDEX": "https://pypi.org/simple"})
    assert version() == "1.0.0"
    assert "Using existing uv configuration" in output
    assert config.read_bytes() == original
    assert not any(path.startswith("/cn/") for path in index.requests)
