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

"""A bounded NDJSON client for the actual CLI app-server, including scoped permission replies."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from typing import Any
from uuid import uuid4


class NativeHost:
    def __init__(self, run: Any) -> None:
        self.run = run
        self.incoming: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self.responses: dict[int, dict[str, Any]] = {}
        self.sequence = 0
        self.allowed: set[str] = set()
        self.process = subprocess.Popen(
            [str(run.node), str(run.cli), "app-server", "--cwd", str(run.workspace), "--no-color"],
            cwd=run.workspace,
            env=run.environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        self.reader = threading.Thread(target=self.read, daemon=True)
        self.stderr_reader = threading.Thread(target=self.drain_stderr, daemon=True)
        self.reader.start()
        self.stderr_reader.start()

    def read(self) -> None:
        assert self.process.stdout is not None
        for line in self.process.stdout:
            try:
                message = json.loads(line)
            except ValueError:
                continue
            self.incoming.put(message)
        self.incoming.put(None)

    def drain_stderr(self) -> None:
        assert self.process.stderr is not None
        for _ in self.process.stderr:
            pass

    def send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message) + "\n")
        self.process.stdin.flush()

    def dispatch(self, message: dict[str, Any]) -> None:
        if "method" not in message:
            if isinstance(message.get("id"), int):
                self.responses[message["id"]] = message
            return
        if "id" not in message:
            return
        if message["method"] == "session/requestRuntimePreferences":
            self.send({
                "id": message["id"],
                "result": {
                    "nativeSearchEnhancementsEnabled": False,
                    "memoryEnabled": False,
                    "askUserQuestionAutoResolutionEnabled": False,
                },
            })
            return
        if message["method"] != "interaction/requestPermission":
            self.send({
                "id": message["id"],
                "error": {"code": -32601, "message": "Unsupported acceptance client request."},
            })
            return
        params = message.get("params", {})
        name = params.get("toolName", "")
        arguments = params.get("input", {})
        allowed = (
            message["method"] == "interaction/requestPermission"
            and name.startswith("mcp__plugin_powercontext_powercontext__")
            and name.rsplit("__", 1)[-1] in self.allowed
            and isinstance(arguments, dict)
            and (arguments.get("scope_id") == self.run.scope_id or name.endswith("__list_scopes"))
        )
        self.send({
            "id": message["id"],
            "result": {
                "decision": "allow" if allowed else "deny",
                "reason": "Disposable synthetic acceptance scope only."
                if allowed
                else "Outside acceptance authorization.",
            },
        })

    def request(self, method: str, params: dict[str, Any], *, timeout: float = 30) -> dict[str, Any]:
        self.sequence += 1
        request_id = self.sequence
        self.send({"id": request_id, "method": method, "params": params})
        deadline = min(self.run.budget, time.monotonic() + timeout)
        while request_id not in self.responses:
            remaining = deadline - time.monotonic()
            assert remaining > 0, "host_rpc_timeout"
            try:
                message = self.incoming.get(timeout=remaining)
            except queue.Empty:
                raise AssertionError("host_rpc_timeout") from None
            assert message is not None, "host_rpc_process_exited"
            self.dispatch(message)
        response = self.responses.pop(request_id)
        self.last_response = response
        assert "error" not in response, f"host_rpc_error_{method}"
        return response["result"]

    def create(self, *, write: bool = False) -> str:
        result = self.request(
            "session/create",
            {
                "workspace": {
                    "workspacePath": str(self.run.workspace),
                    "workspaceKey": "acceptance-" + self.run.run_id,
                },
                "mode": "build" if write else "plan",
                "model": self.run.host_selection,
                "titleGenerationEnabled": False,
                "thoughtLevel": self.run.host_selection["options"]["reasoningLevel"],
            },
        )
        return result["session"]["sessionId"]

    def prompt(self, session: str, text: str, *, operations: set[str] | None = None) -> dict[str, Any]:
        self.allowed = operations or set()
        baseline = self.request("session/events", {"sessionId": session})
        last_sequence = max((event["seq"] for event in baseline["events"]), default=0)
        self.request(
            "session/send",
            {"sessionId": session, "content": text, "inputId": uuid4().hex, "modelSelection": self.run.host_selection},
        )
        deadline = min(self.run.budget, time.monotonic() + (180 if self.run.live else 45))
        while time.monotonic() < deadline:
            events = self.request("session/events", {"sessionId": session})["events"]
            current = [event for event in events if event["seq"] > last_sequence]
            assert not any(event["type"] == "turn.failed" for event in current), "host_model_turn_failed"
            completed = next((event for event in current if event["type"] == "turn.completed"), None)
            if completed:
                return {"sessionId": session, "response": completed["payload"]["response"]}
            time.sleep(0.1)
        raise AssertionError("host_turn_timeout")

    def close(self) -> None:
        if self.process.stdin:
            self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.terminate()
            self.process.wait(timeout=10)
        self.reader.join(timeout=5)
        self.stderr_reader.join(timeout=5)
        for stream in (self.process.stdout, self.process.stderr):
            if stream:
                stream.close()
