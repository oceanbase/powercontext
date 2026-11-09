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

"""Project-tool recovery works on runners without the GitHub CLI."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]


@pytest.mark.skipif(sys.platform != "linux", reason="The recovery step targets Linux runners")
@pytest.mark.parametrize("mode", ["cold", "cached", "corrupt"])
def test_mise_recovery_without_gh_keeps_verified_inputs(tmp_path, mode):
    binary = b"#!/bin/sh\necho 2026.11.3\n"
    archive = tmp_path / "mise.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        info = tarfile.TarInfo("mise/bin/mise")
        info.size = len(binary)
        info.mode = 0o755
        output.addfile(info, io.BytesIO(binary))
    archive_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    binary_digest = hashlib.sha256(binary).hexdigest()
    action = yaml.safe_load((ROOT / ".github/actions/setup-project-tools/action.yml").read_text())
    command = next(step["run"] for step in action["runs"]["steps"] if step.get("id") == "fallback")
    command = command.replace("837acae6defb8c9ad74823f6e38856633bacb04c65c1ac67d952749847543bd9", archive_digest)
    command = command.replace("c2f7e814cb39f60b7f67a0ff36cb805e94128bf2dff09d175309d0f095b8cc3b", binary_digest)
    if mode == "corrupt":
        archive.write_bytes(b"untrusted response")

    commands = tmp_path / "commands"
    commands.mkdir()
    for name in ("mktemp", "rm", "sha256sum", "tar", "gzip", "mkdir", "install", "timeout"):
        source = shutil.which(name)
        assert source is not None, name
        (commands / name).symlink_to(source)
    curl = commands / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        "assert os.environ['GH_TOKEN'] not in ' '.join(sys.argv)\n"
        "assert os.environ['GH_TOKEN'] in sys.stdin.read()\n"
        "Path(os.environ['DOWNLOAD_MARKER']).write_text('downloaded')\n"
        "Path(sys.argv[sys.argv.index('--output') + 1]).write_bytes(Path(os.environ['TEST_ARCHIVE']).read_bytes())\n"
    )
    curl.chmod(0o755)
    mise = tmp_path / "mise"
    installed = mise / "bin/mise"
    if mode == "cached":
        installed.parent.mkdir(parents=True)
        installed.write_bytes(binary)
        installed.chmod(0o755)
    marker = tmp_path / "downloaded"
    env = dict(
        os.environ,
        PATH=str(commands),
        GH_TOKEN="synthetic-test-token",  # noqa: S106 -- local transport fixture
        MISE_DATA_DIR=str(mise),
    )
    env.update(TEST_ARCHIVE=str(archive), DOWNLOAD_MARKER=str(marker))
    result = subprocess.run(
        ["/bin/bash", "-c", command], env=env, capture_output=True, text=True, timeout=10, check=False
    )
    assert env["GH_TOKEN"] not in result.stdout + result.stderr
    if mode == "corrupt":
        assert result.returncode != 0
        assert not installed.exists()
        assert not (mise / "bin/mise-version").exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        assert installed.read_bytes() == binary
        assert os.access(installed, os.X_OK)
        assert (mise / "bin/mise-version").read_text() == "2026.11.3"
    assert marker.exists() == (mode != "cached")
