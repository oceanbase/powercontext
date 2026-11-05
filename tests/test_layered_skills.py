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

import json
import posixpath
import re
import shutil
import subprocess
import tarfile
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("host", ["opencode", "openclaw", "pi", "dsh"])
def test_published_skill_can_read_its_references(host: str, tmp_path: Path) -> None:
    npm, node = shutil.which("npm"), shutil.which("node")
    if not npm or not node:
        pytest.skip("Node/npm are required for package archive validation")
    cli = Path(npm).resolve()
    if cli.suffix != ".js":
        cli = cli.parent / "node_modules/npm/bin/npm-cli.js"
    if not cli.is_file():
        pytest.skip("npm CLI entrypoint is not available")
    package = (
        ROOT / "integrations" / host / "plugins" / ("memory-powercontext" if host == "openclaw" else "powercontext")
    )
    completed = subprocess.run(
        [node, str(cli), "pack", "--json", "--ignore-scripts", "--pack-destination", str(tmp_path)],
        cwd=package,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=60,
    )
    archive_path = tmp_path / json.loads(completed.stdout)[0]["filename"]
    with tarfile.open(archive_path) as archive:
        pending = ["package/skills/powercontext-project-context/SKILL.md"]
        read = set()
        while pending:
            resource = pending.pop()
            if resource in read:
                continue
            stream = archive.extractfile(resource)
            assert stream is not None, f"{host}: missing Skill resource {resource}"
            content = stream.read().decode("utf-8")
            assert content.strip(), f"{host}: empty Skill resource {resource}"
            read.add(resource)
            for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", content):
                target = urlsplit(link)
                if not target.scheme and not target.netloc and target.path:
                    pending.append(
                        posixpath.normpath(posixpath.join(posixpath.dirname(resource), unquote(target.path)))
                    )
