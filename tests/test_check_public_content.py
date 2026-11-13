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

import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]
CHECKER = REPOSITORY_ROOT / "scripts" / "check_public_content.py"


def run_checker(*paths: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), *(str(path) for path in paths)],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def internal_url(host: str) -> str:
    # Build the URL at runtime so this test file does not contain an intranet
    # link that the checker itself (or `prek run -a`) would flag.
    scheme = "https"
    return f"{scheme}://{host}/requirements/doc"


def test_rejects_intranet_document_url(tmp_path: Path) -> None:
    doc = tmp_path / "rfc.md"
    doc.write_text(f"- Business requirements: [notes]({internal_url('yuque.antfin.com')})\n", encoding="utf-8")

    result = run_checker(doc)

    assert result.returncode == 1
    assert "rfc.md:1" in result.stderr
    assert "yuque.antfin.com" in result.stderr


def test_rejects_every_configured_intranet_domain(tmp_path: Path) -> None:
    doc = tmp_path / "links.md"
    doc.write_text(
        "\n".join(
            f"- see {internal_url(host)}"
            for host in ("docs.antfin.com", "wiki.alibaba-inc.com", "kb.alipay-inc.com", "note.aliyun-inc.com")
        )
        + "\n",
        encoding="utf-8",
    )

    result = run_checker(doc)

    assert result.returncode == 1
    assert result.stderr.count("internal document URL is not allowed") == 4


def test_accepts_public_links_bare_domains_and_local_examples(tmp_path: Path) -> None:
    doc = tmp_path / "rules.md"
    doc.write_text(
        "\n".join([
            "- See [the RFC](https://github.com/oceanbase/powercontext/pull/1614).",
            "- License: http://www.apache.org/licenses/LICENSE-2.0",
            "- Forbidden example (bare domain, not a link): yuque.antfin.com/...",
            "- Self-check regex: https?://[^ ]*(antfin|yuque)[^ ]*",
            "- Local example: http://powercontext.internal.example:8765",
            "- Docker example: http://host.docker.internal:8000",
            "- Lookalike public host: https://notantfin.com/docs",
        ])
        + "\n",
        encoding="utf-8",
    )

    result = run_checker(doc)

    assert result.returncode == 0, result.stderr
    assert "no internal document URLs" in result.stdout


def test_skips_binary_and_missing_files(tmp_path: Path) -> None:
    binary = tmp_path / "payload.bin"
    binary.write_bytes(b"\x00\x01" + internal_url("yuque.antfin.com").encode("utf-8"))

    result = run_checker(binary, tmp_path / "absent.md")

    assert result.returncode == 0, result.stderr
