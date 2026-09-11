# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Replay bounded existing Codex messages through a running Server's public APIs.

Raw transcripts and responses belong in a private output directory, never in the source tree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import httpx
from starlette.testclient import TestClient


def read_messages(path: Path) -> list[tuple[int, str, str]]:
    messages = []
    for index, line in enumerate(path.open(), 1):
        item = json.loads(line)
        value = item.get("payload", {})
        if item.get("type") != "response_item" or value.get("type") != "message":
            continue
        if value.get("role") not in {"user", "assistant"} or value.get("channel") == "analysis":
            continue
        content = "\n".join(
            part.get("text", "")
            for part in value.get("content", [])
            if part.get("type") in {"input_text", "output_text", "text"}
        )
        if not content or any(
            marker in content
            for marker in ("<environment_context>", "# AGENTS.md instructions", "<skill>", "<recommended_plugins>")
        ):
            continue
        messages.append((index, value["role"], content))
    return messages


def window(path: Path, max_chars: int) -> tuple[str, dict[str, Any]]:
    messages = read_messages(path)
    chosen = []
    count = 0
    for item in messages:
        size = len(item[1]) + 1 + len(item[2]) + (2 if chosen else 0)
        if count + size > max_chars:
            break
        chosen.append(item)
        count += size
    if len(chosen) < 2:
        raise ValueError("The window must contain at least two complete messages")  # noqa: TRY003
    content = "\n\n".join(f"{role.upper()}\n{text}" for _, role, text in chosen)
    return content, {
        "session_file": str(path),
        "first_line": chosen[0][0],
        "last_line": chosen[-1][0],
        "messages": len(chosen),
        "characters": len(content),
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
    }


def call(
    client: httpx.Client | TestClient,
    output: Path,
    name: str,
    method: str,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cached = output / f"{name}.json"
    identity = {"method": method, "path": path, "request": payload}
    if method != "GET" and cached.exists():
        previous = json.loads(cached.read_text())
        if any(previous.get(key) != value for key, value in identity.items()):
            raise ValueError(f"Request changed; use a separate output directory: {cached}")  # noqa: TRY003
        if previous["status"] is None:
            raise RuntimeError(f"Outcome unknown; reconcile through the API before resuming: {cached}")  # noqa: TRY003
        if previous["status"] < 400:
            return previous["response"]
        attempt = len(list(output.glob(f"{name}.attempt-*.json"))) + 1
        cached.rename(output / f"{name}.attempt-{attempt}.json")
    try:
        response = client.request(method, path, json=payload)
    except httpx.TransportError as error:
        cached.write_text(json.dumps({**identity, "status": None, "error": type(error).__name__}) + "\n")
        raise RuntimeError(f"Outcome unknown; inspect {cached} before retrying") from error  # noqa: TRY003
    result = response.json()
    cached.write_text(
        json.dumps(
            {"method": method, "path": path, "status": response.status_code, "request": payload, "response": result},
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    if response.is_error:
        raise RuntimeError(f"{name}: HTTP {response.status_code}; inspect {cached} before resuming")  # noqa: TRY003
    print(f"{name}: HTTP {response.status_code}", flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--session", type=Path, action="append", required=True)
    parser.add_argument("--title", action="append", required=True)
    parser.add_argument("--summary", action="append", required=True)
    parser.add_argument("--max-chars", type=int, default=16000)
    arguments = parser.parse_args()
    if not len(arguments.session) == len(arguments.title) == len(arguments.summary):
        parser.error("Supply one title and summary for each session")
    if arguments.max_chars < 1:
        parser.error("max-chars must be positive")
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    manifest = []
    token = os.environ.get("POWERCONTEXT_REPLAY_TOKEN")
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(base_url=arguments.base_url, timeout=240, trust_env=False, headers=headers) as client:
        ready = call(client, output, "readiness", "GET", "/health/ready")
        if ready["checks"].get("inference.generation") != "ready":
            raise RuntimeError(f"Generation is not ready; inspect {output / 'readiness.json'}")  # noqa: TRY003
        root = call(
            client,
            output,
            "root-scope",
            "POST",
            "/v1/scopes",
            {
                "title": "PowerContext",
                "summary": "PowerContext development sessions",
                "idempotency_key": f"dashboard-replay:{output.name}:root",
            },
        )
        for index, path in enumerate(arguments.session):
            content, origin = window(path, arguments.max_chars)
            directory = output / str(index)
            directory.mkdir(exist_ok=True, mode=0o700)
            (directory / "transcript.txt").write_text(content)
            scope = call(
                client,
                directory,
                "scope",
                "POST",
                "/v1/scopes",
                {
                    "title": arguments.title[index],
                    "summary": arguments.summary[index],
                    "parent_scope_id": root["scope_id"],
                    "idempotency_key": f"dashboard-replay:{origin['sha256']}",
                },
            )
            captured = call(
                client,
                directory,
                "capture",
                "POST",
                "/v1/sources/content",
                {
                    "scope_id": scope["scope_id"],
                    "source_id": f"codex-{origin['sha256'][:24]}",
                    "content": content,
                    "metadata": origin,
                },
            )
            manifest.append({**origin, "scope_id": scope["scope_id"], "source": captured["source"]})
            (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
            call(client, directory, "flush", "POST", "/v1/memory/flush", {"scope_id": scope["scope_id"]})
            call(client, directory, "memory", "GET", "/v1/scopes/" + scope["scope_id"])
            call(
                client,
                directory,
                "experience",
                "POST",
                "/v1/experience/generate",
                {
                    "scope_id": scope["scope_id"],
                    "source_refs": [captured["source"]],
                    "artifact_refs": [],
                    "reason": "Extract a reusable engineering lesson supported by the recorded decisions and actions. Preserve uncertainty; do not claim unobserved tests or completion.",
                },
            )
            call(
                client,
                directory,
                "handoff-draft",
                "POST",
                "/v1/handoff/prepare",
                {
                    "scope_id": scope["scope_id"],
                    "objective": "Continue the work recorded in this Codex session, retaining its user constraints and unresolved work.",
                    "evidence": [{"kind": "source", "source_ref": captured["source"]}],
                    "max_bytes": 12000,
                },
            )
        call(
            client,
            output,
            "fresh-scope",
            "POST",
            "/v1/scopes",
            {
                "title": "Fresh start",
                "summary": "An empty child scope for checking the initial reading journey",
                "parent_scope_id": root["scope_id"],
                "idempotency_key": f"dashboard-replay:{output.name}:empty",
            },
        )


if __name__ == "__main__":
    main()
