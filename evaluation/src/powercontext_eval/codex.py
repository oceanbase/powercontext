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

"""Codex invocation and JSONL evidence contracts."""

from __future__ import annotations

import json
import re
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, BinaryIO, Protocol

from powercontext_eval.artifacts import ArtifactStore
from powercontext_eval.errors import CommandError, PowerContextEvalError
from powercontext_eval.models import Arm
from powercontext_eval.process import CommandResult

EXPECTED_CODEX_VERSION = "0.145.0"
DEFAULT_CODEX_MODEL = "gpt-5.6-sol"
DEFAULT_REASONING_EFFORT = "medium"
_SAFE_CODEX_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_CAPACITY_MARKER = "at capacity"
_CAPACITY_EVENT_TYPES = frozenset({"error", "turn.failed"})


def is_safe_codex_model(value: str) -> bool:
    """Return whether a model is one opaque argument without restricting the model catalog."""

    return _SAFE_CODEX_MODEL.fullmatch(value) is not None


class UnsafeCodexInvocation(PowerContextEvalError):
    """Dangerous Codex flags were requested outside an isolated task container."""


class CodexInfrastructureError(PowerContextEvalError):
    """Codex infrastructure failed before a benchmark patch could be evaluated."""


class CodexCapacityError(CodexInfrastructureError):
    """The upstream model pool was saturated, which a later attempt can survive."""


@dataclass(frozen=True)
class CodexInvocation:
    """The treatment-balanced Codex command."""

    arm: Arm
    inside_disposable_container: bool
    executable: str = "codex"
    model: str = DEFAULT_CODEX_MODEL
    reasoning_effort: str = DEFAULT_REASONING_EFFORT
    expected_version: str = EXPECTED_CODEX_VERSION
    recorder_python: str | None = None
    recorder_script: str | None = None
    recorder_sidecar: str | None = None

    def argv(self) -> tuple[str, ...]:
        """Build the exact invocation, failing closed on host use."""

        if not self.inside_disposable_container:
            raise UnsafeCodexInvocation("Dangerous Codex invocation requires a disposable task container")
        if not self.executable or self.executable.startswith("-") or "\0" in self.executable:
            raise UnsafeCodexInvocation("Codex executable is unsafe")
        if not is_safe_codex_model(self.model):
            raise UnsafeCodexInvocation("Codex model is unsafe")
        if not self.reasoning_effort or "\0" in self.reasoning_effort:
            raise UnsafeCodexInvocation("Codex reasoning effort is unsafe")
        switch = "--enable" if self.arm is Arm.ON else "--disable"
        codex_argv = (
            self.executable,
            "exec",
            "--ephemeral",
            "--ignore-rules",
            "--json",
            "--disable",
            "shell_snapshot",
            "--dangerously-bypass-approvals-and-sandbox",
            "--dangerously-bypass-hook-trust",
            "--model",
            self.model,
            "-c",
            f'model_reasoning_effort="{self.reasoning_effort}"',
            switch,
            "plugins",
            "-C",
            "/workspace",
            "-",
        )
        recorder_python = self.recorder_python
        recorder_script = self.recorder_script
        recorder_sidecar = self.recorder_sidecar
        if (recorder_python, recorder_script, recorder_sidecar) == (None, None, None):
            return codex_argv
        if recorder_python is None or recorder_script is None or recorder_sidecar is None:
            raise UnsafeCodexInvocation("Codex recorder configuration must be complete")
        recorder = (recorder_python, recorder_script, recorder_sidecar)
        if any(not value.startswith("/") or value.startswith("-") or "\0" in value for value in recorder):
            raise UnsafeCodexInvocation("Codex recorder paths are unsafe")
        return (
            recorder_python,
            recorder_script,
            "--sidecar",
            recorder_sidecar,
            "--",
            *codex_argv,
        )


@dataclass(frozen=True)
class CodexOutcome:
    """Parsed, retained output from a successful Codex turn."""

    last_message: str
    usage: Mapping[str, int] | None


class CodexProcessRunner(Protocol):
    """Structural child-process adapter used by CodexRunner."""

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | Path,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        check: bool = True,
        secrets: Sequence[str] = (),
        input_bytes: bytes | None = None,
        stdout_sink: BinaryIO | None = None,
    ) -> CommandResult: ...


class CodexRunner:
    """Run Codex through a process adapter and retain only audited artifacts."""

    def __init__(self, process_runner: CodexProcessRunner) -> None:
        self._runner = process_runner

    def run(
        self,
        invocation: CodexInvocation,
        *,
        prompt: bytes,
        cwd: str | Path,
        store: ArtifactStore,
        timeout: float | None = None,
        env: Mapping[str, str] | None = None,
        secrets: tuple[str, ...] = (),
        scan_output_secrets: bool = True,
    ) -> CodexOutcome:
        """Send the exact prompt on stdin and parse strict JSONL output."""

        if not isinstance(prompt, bytes):
            raise TypeError("prompt must be exact bytes")
        with tempfile.TemporaryFile("w+b") as event_stream:
            try:
                result = self._runner.run(
                    invocation.argv(),
                    cwd=cwd,
                    timeout=timeout,
                    env=env,
                    secrets=secrets,
                    input_bytes=prompt,
                    stdout_sink=event_stream,
                )
            except CommandError as error:
                output_secrets = secrets if scan_output_secrets else ()
                self._append_compat_stdout(event_stream, error.result.stdout, output_secrets)
                self._retain_process_result(store, error.result, event_stream, output_secrets)
                if _stream_reports_upstream_capacity(event_stream):
                    raise CodexCapacityError("Codex reported the upstream model is at capacity") from None
                raise CodexInfrastructureError(_command_error_kind(error)) from None

            output_secrets = secrets if scan_output_secrets else ()
            self._append_compat_stdout(event_stream, result.stdout, output_secrets)
            self._retain_process_result(store, result, event_stream, output_secrets)
            try:
                event_stream.seek(0)
                last_message, usage = _summarize_jsonl_stream(event_stream)
            except (ValueError, TypeError) as error:
                raise CodexInfrastructureError(f"Codex JSONL is malformed: {error}") from None

        store.write_text("codex/last-message.txt", last_message)
        store.write_json("codex/usage.json", dict(usage) if usage is not None else {"status": "N/A"})
        return CodexOutcome(last_message, usage)

    @staticmethod
    def _append_compat_stdout(event_stream: BinaryIO, stdout: str, secrets: Sequence[str]) -> None:
        encoded = stdout.encode("utf-8")
        if _contains_secret(encoded, secrets):
            raise CodexInfrastructureError("Codex output contained an unredacted secret")
        if encoded:
            event_stream.write(encoded)

    @staticmethod
    def _retain_process_result(
        store: ArtifactStore,
        result: CommandResult,
        event_stream: BinaryIO,
        secrets: Sequence[str],
    ) -> None:
        stderr = result.stderr.encode("utf-8")
        if _contains_secret(stderr, secrets):
            raise CodexInfrastructureError("Codex output contained an unredacted secret")
        event_stream.flush()
        event_stream.seek(0)
        if _stream_contains_secret(event_stream, secrets):
            raise CodexInfrastructureError("Codex output contained an unredacted secret")
        event_stream.seek(0)
        store.write_stream("codex/events.jsonl", event_stream)
        store.write_bytes("codex/stderr.txt", result.stderr.encode("utf-8"))


def _command_error_kind(error: CommandError) -> str:
    if error.result.returncode == 124:
        return "Codex timed out"
    return f"Codex failed with exit status {error.result.returncode}"


def _stream_reports_upstream_capacity(stream: BinaryIO) -> bool:
    """Report whether a failed run ended on an upstream model-pool capacity refusal."""

    stream.seek(0)
    for raw_line in stream:
        try:
            event = json.loads(raw_line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(event, dict) or event.get("type") not in _CAPACITY_EVENT_TYPES:
            continue
        error = event.get("error")
        messages = (event.get("message"), error.get("message") if isinstance(error, dict) else None)
        if any(isinstance(message, str) and _CAPACITY_MARKER in message for message in messages):
            return True
    return False


def _parse_jsonl(raw: str) -> tuple[dict[str, Any], ...]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(raw.splitlines(), start=1):
        if not line:
            raise ValueError(f"empty line {line_number}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError(f"invalid JSON on line {line_number}") from None
        if not isinstance(value, dict):
            raise TypeError(f"line {line_number} is not an object")
        events.append(value)
    if not events:
        raise ValueError("empty stream")
    return tuple(events)


def _summarize_jsonl_stream(stream: BinaryIO) -> tuple[str, Mapping[str, int] | None]:
    last_message: str | None = None
    usage: Mapping[str, int] | None = None
    saw_event = False
    for line_number, raw_line in enumerate(stream, start=1):
        saw_event = True
        try:
            line = raw_line.decode("utf-8")
        except UnicodeDecodeError:
            raise ValueError(f"invalid UTF-8 on line {line_number}") from None
        if not line.endswith("\n") or not line.rstrip("\n"):
            raise ValueError(f"invalid line framing on line {line_number}")
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            raise ValueError(f"invalid JSON on line {line_number}") from None
        if not isinstance(value, dict):
            raise TypeError(f"line {line_number} is not an object")
        direct_message = value.get("message")
        if value.get("type") == "agent_message" and isinstance(direct_message, str):
            last_message = direct_message
        item = value.get("item")
        if (
            value.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            last_message = item["text"]
        if value.get("type") == "turn.completed":
            raw_usage = value.get("usage")
            if raw_usage is None:
                usage = None
            elif not isinstance(raw_usage, dict):
                raise TypeError("turn.completed usage is not an object")
            else:
                parsed: dict[str, int] = {}
                for key, count in raw_usage.items():
                    if not isinstance(key, str) or isinstance(count, bool) or not isinstance(count, int) or count < 0:
                        raise ValueError("turn.completed usage must contain non-negative integer values")
                    parsed[key] = count
                usage = MappingProxyType(parsed)
    if not saw_event:
        raise ValueError("empty stream")
    if last_message is None:
        raise ValueError("no completed agent message")
    return last_message, usage


def _contains_secret(data: bytes, secrets: Sequence[str]) -> bool:
    return any(secret and secret.encode("utf-8") in data for secret in secrets)


def _stream_contains_secret(stream: BinaryIO, secrets: Sequence[str], *, chunk_size: int = 64 * 1024) -> bool:
    encoded = tuple(secret.encode("utf-8") for secret in secrets if secret)
    overlap = max((len(secret) for secret in encoded), default=1) - 1
    tail = b""
    while chunk := stream.read(chunk_size):
        scanned = tail + chunk
        if any(secret in scanned for secret in encoded):
            return True
        tail = scanned[-overlap:] if overlap else b""
    return False


def _last_agent_message(events: tuple[dict[str, Any], ...]) -> str:
    messages: list[str] = []
    for event in events:
        if event.get("type") == "agent_message" and isinstance(event.get("message"), str):
            messages.append(event["message"])
        item = event.get("item")
        if (
            event.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            messages.append(item["text"])
    if not messages:
        raise ValueError("no completed agent message")
    return messages[-1]


def _last_usage(events: tuple[dict[str, Any], ...]) -> Mapping[str, int] | None:
    usage: Mapping[str, int] | None = None
    for event in events:
        if event.get("type") != "turn.completed":
            continue
        raw_usage = event.get("usage")
        if raw_usage is None:
            usage = None
            continue
        if not isinstance(raw_usage, dict):
            raise TypeError("turn.completed usage is not an object")
        parsed: dict[str, int] = {}
        for key, value in raw_usage.items():
            if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("turn.completed usage must contain non-negative integer values")
            parsed[key] = value
        usage = MappingProxyType(parsed)
    return usage
