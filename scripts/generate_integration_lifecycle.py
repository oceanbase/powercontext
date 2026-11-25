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

"""Write the copies of the integration lifecycle helpers that plugins and SDK adapters ship.

Copy-installed Python plugins and the Python SDK adapters get the Python helpers. TypeScript host plugins get the
TypeScript helpers, which their bundlers inline or, for Pi, which the host loads as source. Hooks that their host runs
as plain JavaScript get the TypeScript helpers with the types removed.
"""

from __future__ import annotations

import argparse
import os
import re
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
_KIMI_CODE = "integrations/kimi-code/plugins/powercontext"
_ZCODE = "integrations/zcode/plugins/powercontext"
# The SDK adapters install with the PowerContext package, but they also support released cores that predate
# powercontext_integration_lifecycle, so each keeps a private copy beside its own modules.
_LANGCHAIN = "integrations/langchain/src/powercontext_langchain"
_LANGGRAPH = "integrations/langgraph/src/powercontext_langgraph"
_PYDANTIC_AI = "integrations/pydantic-ai/src/powercontext_pydantic_ai"
# Claude Code and Codex ship the whole package, so a module added to the source reaches both without an entry here.
# The copies use another name than the source, so a process that has already imported the source, such as the test
# suite, still loads the plugin's own copy.
PACKAGE_COPIES = (f"{_CLAUDE_CODE}/powercontext_lifecycle", f"{_CODEX}/powercontext_lifecycle")
# WorkBuddy, Hermes, and the SDK adapters ship single modules where their hooks, installers, and imports already
# expect them.
COPIES = {
    "client_config.py": (
        f"{_HERMES}/powercontext_client_config.py",
        f"{_WORKBUDDY}/hooks/powercontext_client_config.py",
    ),
    "prepared_context.py": (
        f"{_HERMES}/prepared_context.py",
        f"{_WORKBUDDY}/hooks/prepared_context.py",
        f"{_LANGCHAIN}/_prepared_context.py",
        f"{_LANGGRAPH}/_prepared_context.py",
        f"{_PYDANTIC_AI}/_prepared_context.py",
    ),
    "prompt_privacy.py": (f"{_WORKBUDDY}/hooks/prompt_privacy.py",),
}
# Each copy sits at the path its plugin imports.
TYPESCRIPT_COPIES = {
    "flush-progress.ts": (
        f"{_OPENCLAW}/src/flush-progress.ts",
        f"{_PI}/src/flush-progress.ts",
    ),
    "prompt-privacy.ts": (
        f"{_DSH}/src/prompt-privacy.ts",
        f"{_OPENCLAW}/src/prompt-privacy.ts",
        f"{_OPENCODE}/src/prompt-privacy.ts",
        f"{_PI}/src/prompt-privacy.ts",
    ),
    "transport.ts": (
        f"{_DSH}/src/transport.ts",
        f"{_OPENCLAW}/src/transport.ts",
        f"{_OPENCODE}/src/transport.ts",
        f"{_PI}/src/transport.ts",
    ),
}
# Kimi Code and ZCode run their hooks with Node.js straight from the installed plugin, with no build step, so they get
# JavaScript copies.
JAVASCRIPT_COPIES = {
    "prompt-privacy.ts": (
        f"{_KIMI_CODE}/shared/prompt-privacy.mjs",
        f"{_ZCODE}/shared/prompt-privacy.mjs",
    ),
}
# The single-file copies, each table with the directory its modules come from.
TABLES = ((SOURCE, COPIES), (TYPESCRIPT_SOURCE, TYPESCRIPT_COPIES), (TYPESCRIPT_SOURCE, JAVASCRIPT_COPIES))
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
    "scripts/generate_integration_lifecycle.py does not write them; add each copy to COPIES, TYPESCRIPT_COPIES, or "
    "JAVASCRIPT_COPIES there, or rename or delete the file."
)
EXTRA_MESSAGE = (
    "Package copies of the integration lifecycle helpers hold files that the source does not have; "
    "'make integration-lifecycle' deletes the generated ones, and other files belong outside the package."
)
NESTED_MESSAGE = (
    "The plugins get only the top-level modules of src/powercontext_integration_lifecycle/; move these modules to "
    "the top level:"
)
# The JavaScript copies drop the only TypeScript syntax that the helpers they come from may use: the types of plain
# parameters, made of names, arrays, and unions, and the return type, on a one-line function signature, and `as const`
# after a closing bracket at the end of a line. Without a TypeScript parser, the conversion rejects every other
# construct that it recognizes as TypeScript rather than copy it, among them a `this` parameter and any annotation whose
# `:` stands where JavaScript allows none, such as on a method's parameters. It first scans the source for comments,
# quoted strings, and regular expression literals, following JavaScript's rules for where each ends, and it rewrites and
# inspects only the code around them. Generation stops where the scan cannot tell: at a template literal, a string that
# continues past its line, a `/` that may either start a regular expression or divide, and, in code, any character other
# than ASCII names, numbers, spaces, and operators. The Kimi Code and ZCode tests then run each copy against the shared
# privacy fixture.
_SIGNATURE = re.compile(
    r"(?P<head>\s*(?:export\s+)?(?:async\s+)?function\s+[\w$]+)\((?P<parameters>[^()]*)\)(?:\s*:[^{]*?)?(?P<tail>\s*\{)"
)
_PARAMETER = re.compile(r"(?P<name>[\w$]+)\??(?:\s*:\s*[\w.\[\]| ]+)?")
_CONST_ASSERTION = re.compile(r"(?<=[\])}])\s+as\s+const(?=[;,)]?$)")
_TYPESCRIPT_ONLY = re.compile(
    r"^\s*(?:export\s+)?(?:declare|type|interface|enum|namespace|abstract)\b|^\s*(?:import|export)\s+type\b|"
    r"^\s*(?:export\s+)?(?:const|let|var)\s+\w+\s*[!:]|\bfunction\b|\([^()]*:[^()]*\)\s*(?::[^=]*)?=>|"
    r"\bas\s+\S|\bsatisfies\b|\b(?:private|protected|public|readonly)\s+\w|\w!\.|\w<(?![<=])"
)
# Stands in for every character of a comment or literal, so that no pattern matches inside one.
_MASK = "\0"
_CASE = re.compile(r"\s*case\b")
_WORD = re.compile(r"[\w$]+", re.ASCII)
# A number keeps its decimal point, so a keyword after `1.` stays a keyword.
_NUMBER = re.compile(r"\d[\w$]*(?:\.[\w$]*)?", re.ASCII)
# JavaScript's line terminators. Python's str.splitlines also breaks at form feeds and other characters that JavaScript
# treats as spaces, which would end a `//` comment early.
_LINE = re.compile(r"[^\r\n\u2028\u2029]*(?:\r\n|[\r\n\u2028\u2029]|$)")
# A `/` starts a regular expression at the start of the file, after one of these punctuators, and after one of these
# keywords. It divides after a value, `]`, a property name, or any other name that is not a reserved word. After `)`,
# `}`, `++`, `--`, `of`, another reserved word, or a name after a `.` that does not follow a value, it can do either,
# so the scan stops there.
_BEFORE_REGEX = frozenset({"", "...", *"(,=:[!&|?{};+-*%<>~^"})
_KEYWORDS_BEFORE_REGEX = frozenset({
    *("await", "case", "default", "delete", "do", "else", "extends", "in", "instanceof", "new", "return", "throw"),
    *("typeof", "void", "yield"),
})
_STARTS_REGEX = _BEFORE_REGEX | _KEYWORDS_BEFORE_REGEX
_VALUE_KEYWORDS = frozenset({"false", "null", "super", "this", "true"})
_RESERVED = frozenset({
    *("break", "catch", "class", "const", "continue", "debugger", "enum", "export", "finally", "for", "function"),
    *("if", "implements", "import", "interface", "let", "of", "package", "private", "protected", "public", "static"),
    *("switch", "try", "var", "while", "with"),
})


def _end_of_literal(line: str, index: int, closing: str) -> int:
    """Return the index after the literal whose body starts at `index`, or -1 when it continues past the line."""

    in_class = False
    while index < len(line):
        character = line[index]
        if character == "\\":
            index += 2
            continue
        if closing == "/" and character in "[]":
            in_class = character == "["
        elif character == closing and not in_class:
            flags = _WORD.match(line, index + 1) if closing == "/" else None
            return flags.end() if flags else index + 1
        index += 1
    return -1


def _next_token(line: str, index: int, previous: str) -> tuple[int, bool, str]:
    """Return where the token at `index` ends, whether it is code, and the token a following `/` is judged by.

    Raises ValueError, naming what the scan cannot follow.
    """

    character = line[index]
    if line.startswith(("//", "/*"), index):
        closing = line.find("*/", index + 2) if line[index + 1] == "*" else -1
        return (len(line) if closing < 0 else closing + 2), False, previous
    if character == "`":
        raise ValueError("a template literal")  # noqa: TRY003
    if character in "'\"" or (character == "/" and previous in _STARTS_REGEX):
        end = _end_of_literal(line, index + 1, character)
        if end < 0:
            raise ValueError("a literal that continues past its line")  # noqa: TRY003
        return end, False, "value"
    end, previous = _code_token(line, index, previous)
    return end, True, previous


def _code_token(line: str, index: int, previous: str) -> tuple[int, str]:
    """Return where the code token at `index` ends and the token a following `/` is judged by."""

    character = line[index]
    if not character.isascii() or character in "\\#":
        # Escapes, private names, and other Unicode in names would hide where a name or keyword ends.
        raise ValueError("a character other than an ASCII name, number, space, or operator")  # noqa: TRY003
    name = bool(_WORD.fullmatch(previous)) and previous not in _RESERVED | _STARTS_REGEX
    value = previous in {"value", "]"} | _VALUE_KEYWORDS or name
    if character == "/" and not value:
        raise ValueError("a `/` that may start a regular expression or divide")  # noqa: TRY003
    if number := _NUMBER.match(line, index):
        return number.end(), "value"
    if word := _WORD.match(line, index):
        # A property name is a value even when it is spelled like a keyword. After a `.` that does not follow a value,
        # the scan cannot tell, so a `/` after the name stops it.
        kinds = {".": "value", "loose .": "unknown name"}
        return word.end(), kinds.get(previous, word[0])
    if character.isspace():
        return index + 1, previous
    if line.startswith("...", index):
        return index + 3, "..."
    if line.startswith("?.", index) and not line[index + 2 : index + 3].isdigit():
        return index + 2, "."
    if character == ".":
        return index + 1, "." if value or previous == ")" else "loose ."
    if character in "+-" and line.startswith(character * 2, index):
        return index + 2, character * 2
    return index + 1, character


def _code_masks(source: Path, lines: list[str]) -> list[str]:
    """Return each line with its comments and literals masked, or stop where the scan cannot follow the source."""

    masks, in_comment, previous = [], False, ""
    for number, line in enumerate(lines, start=1):
        masked, index = [], 0
        if number == 1 and line.startswith("#!"):
            # A hashbang line is a comment.
            masks.append(_MASK * len(line))
            continue
        if in_comment:
            closing = line.find("*/")
            in_comment, index = closing < 0, len(line) if closing < 0 else closing + 2
            masked.append(_MASK * index)
        while index < len(line):
            try:
                end, code, previous = _next_token(line, index, previous)
            except ValueError as error:
                raise SystemExit(  # noqa: TRY003
                    f"{source.relative_to(ROOT)}:{number}: the JavaScript copies cannot convert {error}"
                ) from None
            in_comment = line.startswith("/*", index) and line.find("*/", index + 2) < 0
            masked.append(line[index:end] if code else _MASK * (end - index))
            index = end
        masks.append("".join(masked))
    if in_comment:
        raise SystemExit(f"{source.relative_to(ROOT)}: the JavaScript copies cannot convert an unclosed comment")  # noqa: TRY003
    return masks


class _Nesting:
    """Follows brackets and `?` operators across lines to find code that only a TypeScript annotation can use.

    In JavaScript, a `:` ends an object key, a label, or a `case` test, or it closes a `?`. One right after `?`, `)`, or
    `}`, or inside parentheses or brackets, that closes no open `?` there starts an annotation: of a parameter,
    including `this`, of a `catch` binding, of a destructured variable, or of a return type. A `:` after `)` that a
    `=>` follows on its line is an arrow's return type even inside a `?`, and a `?` right before `,` or `)` marks an
    optional parameter.
    """

    def __init__(self) -> None:
        # The open brackets, each with its count of open `?` operators, the last code character seen, and the depth of
        # the `case` test whose `:` is still to come.
        self.openers, self.questions, self.previous, self.case = [""], [0], "", 0

    def annotates(self, code: str) -> bool:
        """Return whether a line of code, with its comments and literals masked, has such a `:` or `?`."""

        self.case = len(self.openers) if _CASE.match(code) else 0
        index = 0
        while index < len(code):
            if code.startswith("??", index) or (
                code.startswith("?.", index) and not code[index + 2 : index + 3].isdigit()
            ):
                index, self.previous = index + 2, "."
                continue
            if (code[index] in ",)" and self.previous == "?") or (
                code[index] == ":" and self._annotation(code[index + 1 :])
            ):
                return True
            self._read(code[index])
            index += 1
        return False

    def _annotation(self, rest: str) -> bool:
        if self.previous == "?":
            return True
        if self.case == len(self.openers):
            # The first `:` at the depth where a `case` test starts ends it.
            self.case = 0
            if self.previous == ")":
                return False
        if self.previous == ")" and "=>" in rest:
            return True
        if self.questions[-1]:
            self.questions[-1] -= 1
            return False
        return self.previous in {")", "}"} or self.openers[-1] in {"(", "["}

    def _read(self, character: str) -> None:
        if character in "([{":
            self.openers.append(character)
            self.questions.append(0)
        elif character in ")]}" and len(self.openers) > 1:
            self.openers.pop()
            self.questions.pop()
        elif character == "?":
            self.questions[-1] += 1
        if not character.isspace():
            self.previous = character


def javascript(source: Path, text: str) -> str:
    """Return a TypeScript helper without the types, or stop at the first line the conversion cannot handle."""

    lines = [line for line in _LINE.findall(text) if line]
    bodies = [line.rstrip("\r\n\u2028\u2029") for line in lines]
    converted, nesting = [], _Nesting()
    for number, (line, body, masked) in enumerate(zip(lines, bodies, _code_masks(source, bodies), strict=True), 1):
        ending = line[len(body) :]
        # The patterns see the code up to its last character, so a trailing comment does not hide a signature.
        code = masked.rstrip(_MASK + " \t")
        unsupported = False
        if signature := _SIGNATURE.fullmatch(code):
            items = [item.strip() for item in signature["parameters"].split(",") if item.strip()]
            parameters = [_PARAMETER.fullmatch(item) for item in items]
            # A `this` parameter declares the type of `this`; JavaScript has no such parameter.
            unsupported = not all(parameters) or any(
                parameter["name"] == "this" for parameter in parameters if parameter
            )
            if not unsupported:
                names = ", ".join(parameter["name"] for parameter in parameters if parameter)
                start, end = signature.start("parameters") - 1, signature.start("tail")
                body = f"{body[:start]}({names}){body[end:]}"
                code = f"{code[:start]}({names}){code[end:]}"
        else:
            if assertion := _CONST_ASSERTION.search(code):
                body = body[: assertion.start()] + body[assertion.end() :]
                code = code[: assertion.start()] + code[assertion.end() :]
            unsupported = bool(_TYPESCRIPT_ONLY.search(code))
        if unsupported or nesting.annotates(code):
            raise SystemExit(  # noqa: TRY003
                f"{source.relative_to(ROOT)}:{number}: the JavaScript copies cannot drop this TypeScript syntax"
            )
        converted.append(body + ending)
    return "".join(converted)


def render(source: Path, target: str) -> str:
    """Return a plugin's copy of one helper, marked as generated below its license header."""

    text = source.read_text(encoding="utf-8")
    if source.suffix == ".ts" and Path(target).suffix == ".mjs":
        text = javascript(source, text)
    lines = text.splitlines(keepends=True)
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
        ROOT / target: render(source / module, target)
        for source, table in TABLES
        for module, targets in table.items()
        for target in targets
    }
    modules = sorted(SOURCE.glob("*.py"))
    copies.update({
        ROOT / package / module.name: render(module, module.name) for package in PACKAGE_COPIES for module in modules
    })
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
    # content. An empty module matches no source. Only the file types of the copies are searched, so bundled
    # JavaScript output is not.
    names = {Path(target).name for _, table in TABLES for targets in table.values() for target in targets}
    sources = [*SOURCE.glob("*.py"), *(TYPESCRIPT_SOURCE / module for module in TYPESCRIPT_COPIES)]
    contents = {path.read_bytes() for path in sources} - {b""}
    pathspecs = sorted({f":(glob){PLUGINS}/**/*{Path(name).suffix}" for name in names})
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
