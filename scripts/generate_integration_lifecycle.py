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

"""Write the copies of the integration lifecycle helpers that copy-installed Python plugins ship."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "powercontext_integration_lifecycle"
PLUGINS = "integrations/*/plugins/*"
_CLAUDE_CODE = "integrations/claude-code/plugins/powercontext"
_CODEX = "integrations/codex/plugins/powercontext"
_HERMES = "integrations/hermes/plugins/powercontext"
_WORKBUDDY = "integrations/workbuddy/plugins/powercontext"
# Each copy stays where its host's hooks and installer already expect it.
COPIES = {
    "client_config.py": (
        f"{_CLAUDE_CODE}/powercontext_client_config.py",
        f"{_CODEX}/powercontext_client_config.py",
        f"{_HERMES}/powercontext_client_config.py",
        f"{_WORKBUDDY}/hooks/powercontext_client_config.py",
    ),
    "prepared_context.py": (
        f"{_CLAUDE_CODE}/hooks/prepared_context.py",
        f"{_CODEX}/hooks/prepared_context.py",
        f"{_WORKBUDDY}/hooks/prepared_context.py",
    ),
    "prompt_privacy.py": (
        f"{_CLAUDE_CODE}/hooks/prompt_privacy.py",
        f"{_CODEX}/hooks/prompt_privacy.py",
        f"{_WORKBUDDY}/hooks/prompt_privacy.py",
    ),
    "bootstrap_context.py": (
        f"{_CLAUDE_CODE}/hooks/bootstrap_context.py",
        f"{_CODEX}/hooks/bootstrap_context.py",
    ),
}
DRIFT_MESSAGE = (
    "Generated integration lifecycle helpers drifted; edit src/powercontext_integration_lifecycle/, "
    "run 'make integration-lifecycle', and review the result."
)
UNLISTED_MESSAGE = (
    "Plugin files share a name with a generated integration lifecycle helper; add each copy to COPIES in "
    "scripts/generate_integration_lifecycle.py, or rename or delete the file."
)


def render(module: str) -> str:
    """Return a plugin's copy of one helper, marked as generated below its license header."""

    source = SOURCE / module
    lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    header = next((index for index, line in enumerate(lines) if not line.startswith("#")), len(lines))
    marker = (
        f"# Generated from {source.relative_to(ROOT).as_posix()}; do not edit.\n"
        "# Edit the source and run `make integration-lifecycle` instead.\n"
    )
    return "".join([*lines[:header], "\n" if header else "", marker, *lines[header:]])


def unlisted_copies() -> list[Path]:
    """Return committable plugin files named like a generated copy that no entry in COPIES writes."""

    listed = {ROOT / target for targets in COPIES.values() for target in targets}
    pathspecs = sorted({f":(glob){PLUGINS}/**/{path.name}" for path in listed})
    # Git leaves out ignored build output and installed dependencies, which are not plugin sources.
    # Git writes its own errors, such as a missing checkout, to the terminal.
    result = subprocess.run(  # noqa: S603 -- fixed arguments, no shell
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *pathspecs],  # noqa: S607
        check=True,
        stdout=subprocess.PIPE,
        cwd=ROOT,
    )
    found = {ROOT / os.fsdecode(path) for path in result.stdout.split(b"\0") if path}
    # A tracked copy already deleted from the working tree needs no entry.
    return sorted(path for path in found - listed if path.is_file())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail when a copy is missing, stale, or kept by hand.")
    args = parser.parse_args()
    expected = {ROOT / target: render(module) for module, targets in COPIES.items() for target in targets}
    if args.check:
        stale = []
        for path, text in expected.items():
            if not path.is_file():
                stale.append(f"  {path.relative_to(ROOT)} is missing")
            elif path.read_text(encoding="utf-8") != text:
                stale.append(f"  {path.relative_to(ROOT)} has drifted")
        unlisted = [f"  {path.relative_to(ROOT)}" for path in unlisted_copies()]
        problems = []
        if stale:
            problems += [DRIFT_MESSAGE, *stale]
        if unlisted:
            problems += [UNLISTED_MESSAGE, *unlisted]
        if problems:
            raise SystemExit("\n".join(problems))
        return
    for path, text in expected.items():
        path.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
