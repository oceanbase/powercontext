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

"""Opt-in real Codex acceptance using explicit context loading in disposable workspaces.

This exercises the host's CLI and tool execution, not native Skill discovery or publication.
No package programs are run. Existing Codex configuration, authentication and approval rules
remain in force; the runner requests workspace-write and never bypasses approvals or sandboxing.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from powercontext.builtin.artifacts.skill import capture_skill_archive
from powercontext.client import PowerContextClient
from powercontext.http import ArtifactReference, CaptureContentSourceRequest, RecordSkillUsageRequest

from .applicability import SelectionRequest, SelectionResult
from .applicability_catalog import ServerCandidateCatalog
from .applicability_fixture import SKILLS, skill_archive

# These exact fixture packages describe the workflow witnessed by the commands below.
# Reading another Skill does not establish that its different procedure was invoked.
_CONTRACT_WORKFLOW_DIGESTS = frozenset(
    "sha256:" + capture_skill_archive(skill_archive(name, *SKILLS[name])).reference.tree_digest
    for name in ("http-general-maintenance", "http-contract-generation")
)

GENERATOR = """import json
from pathlib import Path

schema = json.loads(Path("openapi/powercontext.yaml").read_text(encoding="utf-8"))
response = schema["components"]["schemas"]["StatusResponse"]
lines = ["from typing import NotRequired, TypedDict", "", "", "class StatusResponse(TypedDict):"]
for name, definition in response["properties"].items():
    assert definition["type"] == "string"
    annotation = "str" if name in response["required"] else "NotRequired[str]"
    lines.append(f"    {name}: {annotation}")
Path("client.py").write_text("\\n".join(lines) + "\\n", encoding="utf-8")
print("CLIENT_GENERATION_PASSED")
"""
CONTRACT_TEST = """import ast
import json
from pathlib import Path

schema = json.loads(Path("openapi/powercontext.yaml").read_text(encoding="utf-8"))
response = schema["components"]["schemas"]["StatusResponse"]
assert response["properties"]["request_id"] == {"type": "string"}
assert "request_id" not in response["required"]
tree = ast.parse(Path("client.py").read_text(encoding="utf-8"))
model = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "StatusResponse")
annotations = {item.target.id: ast.unparse(item.annotation) for item in model.body if isinstance(item, ast.AnnAssign)}
assert annotations == {"status": "str", "request_id": "NotRequired[str]"}
print("CONTRACT_TEST_PASSED")
"""
READ_CONTEXT = """import hashlib
import json
from pathlib import Path

path = Path("selected-context.json")
raw = path.read_bytes()
context = json.loads(raw)
for candidate in context["candidates"]:
    print(json.dumps(candidate, ensure_ascii=False))
print("POWERCONTEXT_CONTEXT_READ sha256:" + hashlib.sha256(raw).hexdigest())
"""


def prepare_workspace(directory: Path, request: SelectionRequest, result: SelectionResult) -> str:
    """Materialize only the approved text in the exact, revalidated selection snapshot."""

    directory.mkdir(parents=True)
    (directory / "openapi").mkdir()
    schema = {
        "openapi": "3.1.0",
        "info": {"title": "Synthetic HTTP fixture", "version": "1"},
        "paths": {},
        "components": {
            "schemas": {
                "StatusResponse": {
                    "type": "object",
                    "properties": {"status": {"type": "string"}},
                    "required": ["status"],
                }
            }
        },
    }
    (directory / "openapi/powercontext.yaml").write_text(json.dumps(schema, indent=2) + "\n", encoding="utf-8")
    (directory / "generate.py").write_text(GENERATOR, encoding="utf-8")
    (directory / "contract_test.py").write_text(CONTRACT_TEST, encoding="utf-8")
    (directory / "read_context.py").write_text(READ_CONTEXT, encoding="utf-8")
    (directory / "client.py").write_text(
        "from typing import NotRequired, TypedDict\n\n\nclass StatusResponse(TypedDict):\n    status: str\n",
        encoding="utf-8",
    )
    (directory / "AGENTS.md").write_text(
        "This is a disposable HTTP contract task fixture. openapi/powercontext.yaml is authoritative; "
        "its JSON syntax is valid YAML. client.py is generated. Change the contract and regenerate; "
        "do not edit client.py by hand or modify the generator, tests, or context reader. "
        "The portable equivalent of make api-generate is python generate.py. "
        "The portable equivalent of make contract-test is python contract_test.py. "
        "Use the Python executable supplied in the task. No deployment or package installation is needed.\n",
        encoding="utf-8",
    )
    selected = [item.model_dump(mode="json") for item in request.candidates if item.address in result.recommendations]
    context = json.dumps({"pool_digest": result.pool_digest, "candidates": selected}, ensure_ascii=False, indent=2)
    raw = (context + "\n").encode("utf-8")
    (directory / "selected-context.json").write_bytes(raw)
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def verify_workspace(directory: Path, context_digest: str) -> bool:
    """Check the task independently of the host's claims, without executing generated code."""

    try:
        for name, expected in (
            ("generate.py", GENERATOR),
            ("contract_test.py", CONTRACT_TEST),
            ("read_context.py", READ_CONTEXT),
        ):
            if (directory / name).read_text(encoding="utf-8") != expected:
                return False
        raw = (directory / "selected-context.json").read_bytes()
        if "sha256:" + hashlib.sha256(raw).hexdigest() != context_digest:
            return False
        schema = json.loads((directory / "openapi/powercontext.yaml").read_text(encoding="utf-8"))
        response = schema["components"]["schemas"]["StatusResponse"]
        if response["properties"] != {"status": {"type": "string"}, "request_id": {"type": "string"}}:
            return False
        if response["required"] != ["status"]:
            return False
        tree = ast.parse((directory / "client.py").read_text(encoding="utf-8"))
        model = next(item for item in tree.body if isinstance(item, ast.ClassDef) and item.name == "StatusResponse")
        if len(model.bases) != 1 or not isinstance(model.bases[0], ast.Name) or model.bases[0].id != "TypedDict":
            return False
        annotations = {
            item.target.id: ast.unparse(item.annotation)
            for item in model.body
            if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name)
        }
    except (OSError, ValueError, KeyError, TypeError, SyntaxError, StopIteration):
        return False
    else:
        return annotations == {"status": "str", "request_id": "NotRequired[str]"}


def _invoke_codex(directory: Path, task: str, timeout: int) -> tuple[int | None, list[dict[str, Any]]]:
    executable = shutil.which("codex")
    if executable is None:
        raise ValueError("--codex-host requires an installed and authenticated Codex CLI")  # noqa: TRY003
    prompt = (
        f"{task}\nRead AGENTS.md. First explicitly load the recommended PowerContext context by running "
        f"read_context.py with this Python executable: {sys.executable}. "
        "Use the printed Experience and Skill text as advisory context for this task. "
        "Then make the contract change, regenerate and run contract tests. "
        "Work only in this fixture directory. Respect existing approval requirements."
    )
    command = [
        executable,
        "exec",
        "--ephemeral",
        "--json",
        "--color",
        "never",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "--cd",
        str(directory),
        "-",
    ]
    # A standalone CLI must not route its tools through the parent desktop task's
    # connection or reuse that task's session identity. Keep auth, proxy and config.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "CODEX_APP_TOOLS_PIPE_PATH",
            "CODEX_THREAD_ID",
            "CODEX_SESSION_ID",
            "CODEX_TASK_WORKSPACE_VERIFYING_IDENTITY",
        }
    }
    try:
        process = subprocess.run(  # noqa: S603 - fixed CLI arguments; the task is passed through stdin.
            command,
            input=prompt,
            text=True,
            encoding="utf-8",
            env=environment,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        exit_code, stdout, stderr = process.returncode, process.stdout, process.stderr
    except subprocess.TimeoutExpired as error:
        exit_code = None
        stdout = (
            error.stdout.decode("utf-8", errors="replace") if isinstance(error.stdout, bytes) else error.stdout or ""
        )
        stderr = (
            error.stderr.decode("utf-8", errors="replace") if isinstance(error.stderr, bytes) else error.stderr or ""
        )
    (directory / "codex.jsonl").write_text(stdout, encoding="utf-8")
    (directory / "codex.stderr.txt").write_text(stderr, encoding="utf-8")
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return exit_code, events


def _command_arguments(command: str, *, depth: int = 0) -> Iterator[list[str]]:
    """Inspect simple command boundaries and CLI shell wrappers without evaluating a trace."""

    if depth > 2:
        return
    lexer = shlex.shlex(command, posix=False, punctuation_chars=";&|\n")
    lexer.whitespace = " \t\r"
    lexer.whitespace_split = True
    lexer.commenters = ""
    segments: list[list[str]] = [[]]
    try:
        for token in lexer:
            if not token.strip(";&|\n"):
                segments.append([])
            else:
                if token[:1] in {"'", '"'} and token[-1:] == token[:1]:
                    token = token[1:-1]
                segments[-1].append(token)
    except ValueError:
        return
    for arguments in segments:
        if not arguments:
            continue
        executable = arguments[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        if executable in {"bash", "sh", "zsh", "pwsh", "pwsh.exe", "powershell", "powershell.exe"}:
            option = next(
                (index for index, value in enumerate(arguments) if value.lower() in {"-c", "-lc", "-command"}),
                None,
            )
            if option is not None and option + 2 == len(arguments):
                yield from _command_arguments(arguments[option + 1], depth=depth + 1)
        else:
            yield arguments


def _runs_fixture_python(command: str, module: str, directory: Path) -> bool:
    """Match a Python main target, rather than script names quoted by unrelated commands."""

    for arguments in _command_arguments(command):
        executable = arguments[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        if re.fullmatch(r"python(?:\d+(?:\.\d+)*)?(?:\.exe)?", executable) is None:
            continue
        arguments = arguments[1:]
        while arguments:
            argument = arguments.pop(0)
            if argument == "-m":
                if arguments and arguments[0] == module:
                    return True
                break
            if argument in {"-W", "-X"} and arguments:
                arguments.pop(0)
            elif re.fullmatch(r"-[bBdEiIOPqsSuvx]+", argument) or argument.startswith(("-W", "-X")):
                continue
            else:
                if argument == "--" and arguments:
                    argument = arguments.pop(0)
                if (
                    not argument.startswith("-")
                    and (directory / argument.replace("\\", "/")).resolve() == (directory / f"{module}.py").resolve()
                ):
                    return True
                break
    return False


async def run_codex_host(
    client: PowerContextClient,
    catalog: ServerCandidateCatalog,
    scope_id: str,
    query: str,
    request: SelectionRequest,
    result: SelectionResult,
    directory: Path,
    *,
    timeout: int = 180,  # noqa: ASYNC109 - forwarded to the blocking subprocess timeout in a worker thread.
) -> dict[str, object]:
    """Run one real host arm and persist truthful usage evidence through the existing SDK."""

    if result.pool_digest != request.pool_digest:
        raise ValueError("host result does not belong to this task and candidate snapshot")  # noqa: TRY003
    selected = tuple(item for item in request.candidates if item.address in result.recommendations)
    if len(selected) != len(result.recommendations):
        raise ValueError("host recommendation is outside the authorized candidate snapshot")  # noqa: TRY003
    await catalog.revalidate(scope_id, query, selected)
    digest = prepare_workspace(directory, request, result)
    started = time.monotonic()
    exit_code, events = await asyncio.to_thread(_invoke_codex, directory, request.task, timeout)
    commands = [
        event["item"]
        for event in events
        if event.get("type") == "item.completed" and event.get("item", {}).get("type") == "command_execution"
    ]
    context_read = any(
        _runs_fixture_python(item.get("command", ""), "read_context", directory)
        and item.get("exit_code") == 0
        and f"POWERCONTEXT_CONTEXT_READ {digest}" in item.get("aggregated_output", "")
        for item in commands
    )
    tests_run = any(
        _runs_fixture_python(item.get("command", ""), "contract_test", directory)
        and item.get("exit_code") == 0
        and "CONTRACT_TEST_PASSED" in item.get("aggregated_output", "")
        for item in commands
    )
    generation_run = any(
        _runs_fixture_python(item.get("command", ""), "generate", directory)
        and item.get("exit_code") == 0
        and "CLIENT_GENERATION_PASSED" in item.get("aggregated_output", "")
        for item in commands
    )
    workflow_observed = context_read and generation_run and tests_run
    invocation_observed = workflow_observed and any(
        item.package_digest in _CONTRACT_WORKFLOW_DIGESTS for item in selected
    )
    success = exit_code == 0 and generation_run and tests_run and verify_workspace(directory, digest)
    evidence = {
        "host": "real Codex CLI, explicit context loading",
        "context_digest": digest,
        "context_read_observed": context_read,
        "contract_test_observed": tests_run,
        "generation_observed": generation_run,
        "skill_invocation_observed": invocation_observed,
        "task_success": success,
        "exit_code": exit_code,
        "elapsed_ms": (time.monotonic() - started) * 1000,
        "usage": [event.get("usage") for event in events if event.get("type") == "turn.completed"],
        "selection": result.model_dump(mode="json"),
    }
    source = await client.capture_content_source(
        CaptureContentSourceRequest(
            scope_id=scope_id,
            source_id="host-outcome:" + uuid.uuid4().hex,
            content=json.dumps(evidence, ensure_ascii=False),
        )
    )
    usage_sources = []
    for candidate in selected:
        if candidate.address.artifact.family != "skill":
            continue
        assert candidate.package_digest is not None  # noqa: S101 - Skill candidate validation guarantees this.
        invoked = workflow_observed and candidate.package_digest in _CONTRACT_WORKFLOW_DIGESTS
        usage = await client.record_skill_usage(
            RecordSkillUsageRequest.model_validate({
                "scope_id": candidate.address.scope_id,
                "observation_id": "host-use:" + uuid.uuid4().hex,
                "skill_ref": ArtifactReference.model_validate(candidate.address.artifact.model_dump()),
                "package_digest": candidate.package_digest,
                "target_id": "codex-explicit-context",
                "selected": True,
                "invoked": "true" if invoked else "unknown",
                "validation": "passed",
                "outcome": ("success" if success else "failure") if invoked else "unknown",
                "task_source": source.source,
            })
        )
        usage_sources.append(usage.source.model_dump(mode="json"))
    return {**evidence, "outcome_source": source.source.model_dump(mode="json"), "skill_usage_sources": usage_sources}
