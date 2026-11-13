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


def internal_url(host: str, suffix: str = "/requirements/doc") -> str:
    # Build the URL at runtime so this test file does not contain an intranet
    # link that the checker itself (or `prek run -a`) would flag.
    scheme = "https"
    return f"{scheme}://{host}{suffix}"


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


def test_rejects_intranet_url_next_to_cjk_punctuation_or_inline_code(tmp_path: Path) -> None:
    # A bare URL (no path) is where the host capture used to absorb the
    # following delimiter. Chinese docs and Markdown inline code are common
    # in this repository, so all of these must be rejected.
    url = internal_url("yuque.antfin.com", suffix="")
    # Full-width delimiters are written as escapes: Ruff RUF001 flags those
    # characters as ambiguous inside string literals, but the checker must
    # still see the real Unicode characters at runtime.
    lines = [
        f"- 见 {url}\u3002",
        f"- 见 {url}\uff0c然后是其它内容",
        f"- 见 {url}\uff1b",
        f"- \uff08见 {url}\uff09",
        f"- 见\u3010{url}\u3011",
        f"- 见\u201c{url}\u201d",
        f"- see `{url}` for details",
        f"| {url} | notes |",
        f"- see {url}.",
        f"- see {url}, next",
    ]
    doc = tmp_path / "punctuation.md"
    doc.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = run_checker(doc)

    assert result.returncode == 1
    assert result.stderr.count("internal document URL is not allowed") == len(lines)
    for number in range(1, len(lines) + 1):
        assert f"punctuation.md:{number}:" in result.stderr


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
            "- Chinese punctuation next to a public link: 见 https://github.com/oceanbase/powercontext。",
            "- Inline code around a public link: 见 `https://github.com/oceanbase/powercontext` 的说明。",
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
