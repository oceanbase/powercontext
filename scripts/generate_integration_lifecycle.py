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

"""Write the copies of the integration lifecycle helpers that plugins ship.

Copy-installed Python plugins get the Python helpers. TypeScript host plugins get the TypeScript helpers, which their
bundlers inline or, for Pi, which the host loads as source.
"""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "powercontext_integration_lifecycle"
TYPESCRIPT_SOURCE = ROOT / "integrations" / "lifecycle"
PLUGINS = "integrations/*/plugins/*"
_CLAUDE_CODE = "integrations/claude-code/plugins/powercontext"
_CODEX = "integrations/codex/plugins/powercontext"
_HERMES = "integrations/hermes/plugins/powercontext"
_WORKBUDDY = "integrations/workbuddy/plugins/powercontext"
_DSH = "integrations/dsh/plugins/powercontext"
_OPENCLAW = "integrations/openclaw/plugins/memory-powercontext"
_OPENCODE = "integrations/opencode/plugins/powercontext"
_PI = "integrations/pi/plugins/powercontext"
# Claude Code and Codex ship the whole package, so a module added to the source reaches both without an entry here.
# The copies use another name than the source, so a process that has already imported the source, such as the test
# suite, still loads the plugin's own copy.
PACKAGE_COPIES = (f"{_CLAUDE_CODE}/powercontext_lifecycle", f"{_CODEX}/powercontext_lifecycle")
# WorkBuddy and Hermes ship single modules where their hooks and installers already expect them.
COPIES = {
    "client_config.py": (
        f"{_HERMES}/powercontext_client_config.py",
        f"{_WORKBUDDY}/hooks/powercontext_client_config.py",
    ),
    "prepared_context.py": (f"{_WORKBUDDY}/hooks/prepared_context.py",),
    "prompt_privacy.py": (f"{_WORKBUDDY}/hooks/prompt_privacy.py",),
}
# A copy keeps the path its plugin imports, so bundled output still names the same module.
TYPESCRIPT_COPIES = {
    "transport.ts": (
        f"{_DSH}/src/transport.ts",
        f"{_OPENCLAW}/src/transport.ts",
        f"{_OPENCODE}/src/transport.ts",
        f"{_PI}/src/transport.ts",
    ),
}
# The single-file copies, each table with the directory its modules come from.
TABLES = ((SOURCE, COPIES), (TYPESCRIPT_SOURCE, TYPESCRIPT_COPIES))
# Every copy's generated-file comment starts with one of these.
GENERATED_FROM = (
    f"# Generated from {SOURCE.relative_to(ROOT).as_posix()}/",
    f"// Generated from {TYPESCRIPT_SOURCE.relative_to(ROOT).as_posix()}/",
)
DRIFT_MESSAGE = (
    "Generated integration lifecycle helpers drifted; edit src/powercontext_integration_lifecycle/ or "
    "integrations/lifecycle/, run 'make integration-lifecycle', and review the result."
)
UNLISTED_MESSAGE = (
    "Plugin files look like generated integration lifecycle helpers, by name, generated-file comment, or content, but "
    "scripts/generate_integration_lifecycle.py does not write them; add each copy to COPIES or TYPESCRIPT_COPIES "
    "there, or rename or delete the file."
)
EXTRA_MESSAGE = (
    "Package copies of the integration lifecycle helpers hold files that the source does not have; "
    "'make integration-lifecycle' deletes the generated ones, and other files belong outside the package."
)
NESTED_MESSAGE = (
    "The plugins get only the top-level modules of src/powercontext_integration_lifecycle/; move these modules to "
    "the top level:"
)


def render(source: Path) -> str:
    """Return a plugin's copy of one helper, marked as generated below its license header."""

    lines = source.read_text(encoding="utf-8").splitlines(keepends=True)
    if source.suffix == ".py":
        comment = "#"
        header = next((index for index, line in enumerate(lines) if not line.startswith("#")), len(lines))
    elif source.suffix in {".ts", ".mjs"}:
        # The JavaScript and TypeScript license header is one leading block comment.
        comment = "//"
        header = 0
        if lines and lines[0].startswith("/*"):
            end = next((index for index, line in enumerate(lines) if line.rstrip().endswith("*/")), None)
            if end is None:
                raise SystemExit(f"{source.relative_to(ROOT)}: no license header line ends with '*/'")  # noqa: TRY003
            header = end + 1
    else:
        raise SystemExit(f"{source.relative_to(ROOT)}: no generated-file marker for this file type")  # noqa: TRY003
    marker = (
        f"{comment} Generated from {source.relative_to(ROOT).as_posix()}; do not edit.\n"
        f"{comment} Edit the source and run `make integration-lifecycle` instead.\n"
    )
    return "".join([*lines[:header], "\n" if header else "", marker, *lines[header:]])


def expected_copies() -> dict[Path, str]:
    """Return every copy the generator writes, keyed by its path."""

    # The package copies hold the top-level modules only, so a subpackage would be left out.
    if nested := sorted(SOURCE.glob("*/**/*.py")):
        raise SystemExit("\n".join([NESTED_MESSAGE, *(f"  {path.relative_to(ROOT)}" for path in nested)]))
    copies = {
        ROOT / target: render(source / module)
        for source, table in TABLES
        for module, targets in table.items()
        for target in targets
    }
    modules = sorted(SOURCE.glob("*.py"))
    copies.update({ROOT / package / module.name: render(module) for package in PACKAGE_COPIES for module in modules})
    return copies


def has_generated_comment(content: bytes) -> bool:
    """Return whether a file has a line that starts like a generated-file comment."""

    prefixes = tuple(prefix.encode() for prefix in GENERATED_FROM)
    return any(line.startswith(prefixes) for line in content.splitlines())


def committable_files(*pathspecs: str) -> set[Path]:
    """Return the files that match the pathspecs and that Git tracks or would commit."""

    # Git leaves out ignored build output and installed dependencies, which are not plugin sources.
    # Git writes its own errors, such as a missing checkout, to the terminal.
    result = subprocess.run(  # noqa: S603 -- fixed arguments, no shell
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *pathspecs],  # noqa: S607
        check=True,
        stdout=subprocess.PIPE,
        cwd=ROOT,
    )
    found = {ROOT / os.fsdecode(path) for path in result.stdout.split(b"\0") if path}
    # A tracked file already deleted from the working tree is not there to report.
    return {path for path in found if path.is_file()}


def package_extras(listed: set[Path]) -> list[Path]:
    """Return the files in the package copies that the generator does not write."""

    return sorted(committable_files(*(f":(glob){package}/**" for package in PACKAGE_COPIES)) - listed)


def unlisted_copies(listed: set[Path]) -> list[Path]:
    """Return the plugin modules outside the package copies that look like a copy but are not one."""

    # A host's own module may share a package module's name, so only the names of the single-file copies count.
    # Other copies show themselves by their generated-file comment or, when taken straight from the source, by their
    # content. An empty module matches no source. Only the file types of the sources are searched, so bundled
    # JavaScript output is not.
    names = {Path(target).name for _, table in TABLES for targets in table.values() for target in targets}
    sources = [*SOURCE.glob("*.py"), *(TYPESCRIPT_SOURCE / module for module in TYPESCRIPT_COPIES)]
    contents = {path.read_bytes() for path in sources} - {b""}
    pathspecs = sorted({f":(glob){PLUGINS}/**/*{path.suffix}" for path in sources})
    packages = [ROOT / package for package in PACKAGE_COPIES]
    unlisted = []
    for path in sorted(committable_files(*pathspecs) - listed):
        if any(path.is_relative_to(package) for package in packages):
            continue
        content = path.read_bytes()
        if path.name in names or has_generated_comment(content) or content in contents:
            unlisted.append(path)
    return unlisted


def check(expected: dict[Path, str]) -> list[str]:
    """Return the messages for copies that are missing, stale, or kept by hand."""

    stale = []
    for path, text in expected.items():
        if not path.is_file():
            stale.append(f"  {path.relative_to(ROOT)} is missing")
        elif path.read_text(encoding="utf-8") != text:
            stale.append(f"  {path.relative_to(ROOT)} has drifted")
    unlisted = [f"  {path.relative_to(ROOT)}" for path in unlisted_copies(set(expected))]
    extras = [f"  {path.relative_to(ROOT)}" for path in package_extras(set(expected))]
    problems = []
    if stale:
        problems += [DRIFT_MESSAGE, *stale]
    if unlisted:
        problems += [UNLISTED_MESSAGE, *unlisted]
    if extras:
        problems += [EXTRA_MESSAGE, *extras]
    return problems


def write(expected: dict[Path, str]) -> None:
    """Write every copy, and delete the generated modules in the package copies that the source does not have."""

    for path, text in expected.items():
        path.parent.mkdir(exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    for path in package_extras(set(expected)):
        # Only a module that this generator wrote goes; `make check` reports any other file.
        if path.suffix == ".py" and has_generated_comment(path.read_bytes()):
            path.unlink()
            print(f"Deleted {path.relative_to(ROOT)}; the source has no such module.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail when a copy is missing, stale, or kept by hand.")
    args = parser.parse_args()
    expected = expected_copies()
    if not args.check:
        write(expected)
    elif problems := check(expected):
        raise SystemExit("\n".join(problems))


if __name__ == "__main__":
    main()
