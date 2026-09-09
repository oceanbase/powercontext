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

"""Run chronological API replay in an isolated process with a controlled UTC clock.

Run with `uv run --with time-machine==3.5.0 python scripts/dashboard_multiday.py`.
The clock is simulated; every source, extraction, revision and recall uses real APIs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import time_machine
from dashboard_replay import call, read_messages
from dashboard_review import generate_day
from dotenv import load_dotenv
from fastapi.testclient import TestClient

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


def require(value: bool, message: str) -> None:
    if not value:
        raise RuntimeError(message)


def artifact_request(scope: str, reference: dict[str, str | int]) -> tuple[str, str, dict[str, Any] | None]:
    family, artifact, revision = reference["family"], reference["artifact_id"], reference["revision"]
    if family in {"experience", "skill"}:
        return "POST", f"/v1/{family}/get", {"scope_id": scope, "artifact": reference}
    return "GET", f"/v1/scopes/{scope}/artifacts/{family}/{artifact}/revisions/{revision}", None


def verify_prior_artifacts(client: TestClient, output: Path, scope: str, day: int) -> None:
    for prior_day in range(day):
        references = json.loads((output / str(prior_day) / "references.json").read_text())
        for name, reference in references.items():
            method, route, payload = artifact_request(scope, reference)
            response = client.request(method, route, json=payload)
            require(response.status_code == 200, f"Prior {name} is no longer readable: {response.status_code}")
            original = json.loads((output / str(prior_day) / f"family-{name}.json").read_text())["response"]
            require(response.json() == original, f"Prior {name} revision changed")


def verify_artifacts(
    client: TestClient, directory: Path, scope: str, fresh: str, references: dict[str, dict[str, str | int]]
) -> None:
    for name, reference in references.items():
        method, route, payload = artifact_request(scope, reference)
        call(client, directory, f"family-{name}", method, route, payload)
        method, route, payload = artifact_request(fresh, reference)
        require(client.request(method, route, json=payload).status_code == 404, "Artifact crossed scope boundary")


def replay_settings(env_file: Path, output: Path) -> tuple[ServerSettings, str]:
    load_dotenv(env_file)
    settings = ServerSettings()
    if not settings.dashboard.enabled or settings.auth.token is None:
        raise ValueError("Enable DASHBOARD_ENABLED with ACCESS_MODE=enforced and AUTH_TOKEN in the env file")  # noqa: TRY003
    token = settings.auth.token.get_secret_value()
    model = settings.inference.generation_model
    if not model:
        raise ValueError("Configure a real generation model in the env file")  # noqa: TRY003
    settings = settings.model_copy(
        update={
            "database": SQLiteConfig(url=f"sqlite+aiosqlite:///{output}/data.db"),
            "mcp": McpConfig(enabled=False),
            "inference": settings.inference.model_copy(
                update={
                    "generation_model": model if ":" in model else "openai-chat:" + model,
                    "generation_timeout_seconds": 180,
                }
            ),
        }
    )
    return settings, token


def replay(arguments: argparse.Namespace) -> None:
    output = arguments.output.resolve()
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    settings, token = replay_settings(arguments.env_file, output)
    messages = [item for item in read_messages(arguments.session) if item[0] <= arguments.last_line]
    total = sum(len(item[2]) for item in messages)
    if not messages or total > 30000:
        raise ValueError("Select a nonempty window with at most 30000 content characters")  # noqa: TRY003
    days: list[list[tuple[int, str, str]]] = [[] for _ in range(3)]
    used = 0
    for message in messages:
        index = min(2, used * 3 // total)
        days[index].append(message)
        used += len(message[2])
    if not all(days):
        raise ValueError("The window must span three complete message groups")  # noqa: TRY003
    start = datetime.fromisoformat(arguments.start_date).replace(tzinfo=UTC, hour=12)
    report = {"clock": "simulated UTC", "session": str(arguments.session), "days": []}
    sources = []
    transcripts = []
    for day, messages_for_day in enumerate(days):
        directory = output / str(day)
        directory.mkdir(exist_ok=True, mode=0o700)
        date = start + timedelta(days=day)
        content = "\n\n".join(f"{role.upper()}\n{text}" for _, role, text in messages_for_day)
        (directory / "transcript.txt").write_text(content)
        transcripts.append(content)
        origin = {
            "session": str(arguments.session),
            "first_line": messages_for_day[0][0],
            "last_line": messages_for_day[-1][0],
            "sha256": hashlib.sha256(content.encode()).hexdigest(),
            "replay_day": date.date().isoformat(),
        }
        # Restart the Server each day to verify persistence across process lifecycles.
        with time_machine.travel(date, tick=True):
            app = create_server_app(settings=settings, scheduler_path=output / "scheduler.db")
            with TestClient(app, raise_server_exceptions=False) as client:
                client.headers["Authorization"] = f"Bearer {token}"
                default = call(client, directory, "server-default", "GET", "/v1/scopes/default")
                scope = call(
                    client,
                    output,
                    "scope",
                    "POST",
                    "/v1/scopes",
                    {
                        "title": "Dashboard",
                        "summary": "Dashboard implementation and UI quality",
                        "idempotency_key": "dashboard-multiday",
                    },
                )["scope_id"]
                fresh = call(
                    client,
                    output,
                    "fresh",
                    "POST",
                    "/v1/scopes",
                    {
                        "title": "Fresh start",
                        "summary": "An empty child scope",
                        "parent_scope_id": scope,
                        "idempotency_key": "dashboard-multiday-empty",
                    },
                )["scope_id"]
                call(
                    client,
                    directory,
                    "before-recall",
                    "POST",
                    "/v1/context/prepare",
                    {
                        "scope_id": scope,
                        "query": "Tabler",
                        "max_bytes": 8000,
                    },
                )
                call(
                    client,
                    directory,
                    "before-skills",
                    "POST",
                    "/v1/skill/library",
                    {"scope_id": scope, "query": "dashboard"},
                )
                verify_prior_artifacts(client, output, scope, day)
                captured = call(
                    client,
                    directory,
                    "capture",
                    "POST",
                    "/v1/sources/content",
                    {
                        "scope_id": scope,
                        "source_id": "codex-" + origin["sha256"][:24],
                        "content": content,
                        "metadata": origin,
                    },
                )
                sources.append(captured["source"])
                call(client, directory, "flush", "POST", "/v1/memory/flush", {"scope_id": scope})
                references = generate_day(client, directory, scope, sources, "\n\n".join(transcripts))
                verify_artifacts(client, directory, scope, fresh, references)
                call(
                    client, directory, "skills", "POST", "/v1/skill/library", {"scope_id": scope, "query": "dashboard"}
                )
                recalled = call(
                    client,
                    directory,
                    "recall",
                    "POST",
                    "/v1/context/prepare",
                    {
                        "scope_id": scope,
                        "query": "Tabler",
                        "max_bytes": 8000,
                    },
                )
                limited = call(
                    client,
                    directory,
                    "recall-small",
                    "POST",
                    "/v1/context/prepare",
                    {
                        "scope_id": scope,
                        "query": "Tabler",
                        "max_bytes": 1024,
                    },
                )
                require(limited["content_bytes"] <= 1024, "Recall exceeds its byte budget")
                unrelated = call(
                    client,
                    directory,
                    "fresh-recall",
                    "POST",
                    "/v1/context/prepare",
                    {
                        "scope_id": fresh,
                        "query": "Tabler",
                        "max_bytes": 8000,
                    },
                )
                require(unrelated["status"] == "empty", "A child inherited content without an explicit reference")
                entries = call(client, directory, "entries", "POST", "/v1/memory/entries/list", {"scope_id": scope})
                for entry in entries["entries"]:
                    got = client.post("/v1/memory/entries/get", json={"scope_id": scope, "citation": entry["citation"]})
                    require(got.status_code == 200 and got.json()["text"] == entry["text"], "Memory citation changed")
                for prior_day in range(day):
                    prior = json.loads((output / str(prior_day) / "entries.json").read_text())["response"]
                    for entry in prior["entries"]:
                        got = client.post(
                            "/v1/memory/entries/get", json={"scope_id": scope, "citation": entry["citation"]}
                        )
                        require(
                            got.status_code == 200 and got.json()["text"] == entry["text"],
                            "A prior day's immutable citation changed",
                        )
                stats = call(
                    client,
                    directory,
                    "stats",
                    "POST",
                    "/v1/stats",
                    {
                        "selection": {"mode": "exact", "scope_ids": [scope]},
                        "period": "7d",
                    },
                )
                require(stats["usage"]["period"]["end_date"] == date.date().isoformat(), "UTC period is incorrect")
                model_day = next(item for item in stats["usage"]["daily"] if item["date"] == date.date().isoformat())
                purposes = {item["purpose"] for item in model_day["by_purpose"] if item["generation"]["requests"] > 0}
                require(
                    {"memory_extraction", "experience_generation", "handoff_generation", "skill_generation"}
                    <= purposes,
                    "A generation family is missing from the current day's model usage",
                )
                today = next(item for item in stats["recall"]["daily"] if item["date"] == date.date().isoformat())
                require(today["preparations"] == 3, "Recall was not attributed to its replay day")
                for page in ("home", "notes", "handoff", "methods", "usage"):
                    response = client.get("/dashboard/" + page, params={"scope": scope})
                    require(response.status_code == 200, f"{page}: HTTP {response.status_code}")
                    (directory / f"{page}.html").write_text(response.text)
                require(client.get("/v1/scopes/default").json() == default, "Replay changed the server default")
                require(client.get("/dashboard/home").status_code == 200, "Default scope cannot be opened")
                report["days"].append({
                    **origin,
                    "entries": len(entries["entries"]),
                    "recall_status": recalled["status"],
                    "recall_bytes": recalled["content_bytes"],
                    "small_recall_bytes": limited["content_bytes"],
                    "artifacts": references,
                    "model_usage": model_day,
                    "daily": stats["recall"]["daily"],
                })
                (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
        print(f"Day {day + 1}: {date.date()} persisted and recalled", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--last-line", type=int, required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    replay(parser.parse_args())


if __name__ == "__main__":
    main()
