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

"""Bounded client for Pi's real JSONL RPC and explicit durable-write confirmations."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from uuid import uuid4


class PiHost:
    def __init__(self, node: str, cli: Path, plugin: Path, workspace: Path, environment: dict[str, str]) -> None:
        self.messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.events: list[dict[str, Any]] = []
        self.confirmations: list[str] = []
        self.allowed = {"create_work_contract", "capture_content_source", "handoff_current_work", "commit_handoff"}
        self.process = subprocess.Popen(
            [
                node,
                str(cli),
                "--mode",
                "rpc",
                "--no-session",
                "--no-extensions",
                "--no-skills",
                "--no-prompt-templates",
                "--no-context-files",
                "--provider",
                "continuity",
                "--model",
                "fixture",
                "--thinking",
                "off",
                "-e",
                str(plugin / "extensions/powercontext.ts"),
            ],
            cwd=workspace,
            env=environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.stderr = threading.Thread(target=self._drain, daemon=True)
        self.reader.start()
        self.stderr.start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                try:
                    self.messages.put(json.loads(line))
                except ValueError:
                    continue
        finally:
            self.messages.put(None)

    def _drain(self) -> None:
        assert self.process.stderr is not None
        # Do not retain provider data or raw transcripts in evidence.
        while self.process.stderr.read(4096):
            pass

    def send(self, value: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(value) + "\n")
        self.process.stdin.flush()

    def next(self, deadline: float) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        assert remaining > 0, "pi_rpc_timeout"
        try:
            value = self.messages.get(timeout=remaining)
        except queue.Empty:
            raise AssertionError("pi_rpc_timeout") from None
        assert value is not None, "pi_rpc_process_exited"
        if value.get("type") == "extension_ui_request" and value.get("method") == "confirm":
            operation = next((name for name in self.allowed if f"{name} operation" in value.get("message", "")), None)
            self.send({"type": "extension_ui_response", "id": value["id"], "confirmed": operation is not None})
            if operation:
                self.confirmations.append(operation)
        self.events.append(value)
        return value

    def request(self, command: str, **parameters: Any) -> dict[str, Any]:
        identity = uuid4().hex
        self.send({"id": identity, "type": command, **parameters})
        deadline = time.monotonic() + 90
        while True:
            value = self.next(deadline)
            if value.get("type") == "response" and value.get("id") == identity:
                assert value.get("success"), "pi_rpc_command_failed"
                return value.get("data", {})

    def prompt(self, text: str, *, allow_tool_errors: bool = False) -> str:
        state = self.request("get_state")
        before = len(self.events)
        self.request("prompt", message=text)
        deadline = time.monotonic() + 90
        while not any(event.get("type") == "agent_end" for event in self.events[before:]):
            self.next(deadline)
        failures = [
            event
            for event in self.events[before:]
            if event.get("type") == "tool_execution_end" and event.get("isError")
        ]
        assert allow_tool_errors or not failures, "pi_native_tool_failed"
        assert state.get("sessionId"), "pi_session_identity_missing"
        return state["sessionId"]

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=10)
        self.reader.join(timeout=5)
        self.stderr.join(timeout=5)
        for stream in (self.process.stdout, self.process.stderr):
            if stream:
                stream.close()
