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

"""Installed release -> built wheel acceptance; every required phase must succeed.

No checkout runtime imports, migration fallbacks, skips or expected failures.
Fixtures establish dependencies rather than relying on test execution order.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urlsplit

import httpx
import pytest
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
TOKEN = "isolated-upgrade-acceptance-token"  # noqa: S105 -- disposable fixture, never a deployment credential
AUTOMATIC_MEMORY = "Upgrade fixture background processing retains source evidence."


class SourceAdapter(TypedDict):
    candidate_prefix: str
    candidate_tables: set[str]
    revision: str
    database_prefix: str


SOURCE_ADAPTERS: dict[str, SourceAdapter] = {
    "1.3.0": {
        "candidate_prefix": "/v1/artifact-candidates",
        "candidate_tables": {"pc_artifact_candidate_heads", "pc_artifact_candidate_versions"},
        "revision": "s1300",
        "database_prefix": "pc_upgrade",
    },
    "1.4.0": {
        "candidate_prefix": "/v1/candidates",
        "candidate_tables": {"pc_candidate_heads", "pc_candidate_versions"},
        "revision": "r1400",
        "database_prefix": "pc_upgrade_140",
    },
}


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from objects(child)
    elif isinstance(value, str):
        try:
            decoded = json.loads(value)
        except ValueError:
            try:
                decoded, _ = json.JSONDecoder().raw_decode(value[value.index("{") :])
            except ValueError:
                return
        if isinstance(decoded, (dict, list)):
            yield from objects(decoded)


@pytest.fixture(scope="session")
def model_gateway():
    stages = []
    embeddings = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # noqa: A002 -- stdlib signature
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path.endswith("/embeddings"):
                model = re.fullmatch(r"upgrade-vector-([34])", body["model"])
                assert model is not None, "Unregistered controlled embedding model"
                dimension = int(model[1])
                inputs = body["input"]
                if isinstance(inputs, str):
                    inputs = [inputs]
                embeddings.append(dimension)
                response = json.dumps({
                    "object": "list",
                    "model": body["model"],
                    "data": [
                        {"object": "embedding", "index": index, "embedding": [1.0, *([0.0] * (dimension - 1))]}
                        for index in range(len(inputs))
                    ],
                    "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
                }).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(response)))
                self.end_headers()
                self.wfile.write(response)
                return
            payload = next((obj for obj in objects(body["messages"]) if "evidence" in obj), None)
            value: dict[str, Any]
            if payload is None:
                value = {"ready": True}
            elif "proposal" in payload:
                stages.append("reconcile")
                proposal = payload["proposal"]
                duplicate = next((item for item in payload["related"] if item["text"] == proposal["text"]), None)
                value = {
                    "action": "noop" if duplicate else "create",
                    "compared_ids": [item["item_id"] for item in payload["related"]],
                    "target_ids": [duplicate["item_id"]] if duplicate else [],
                    "content": None if duplicate else {"kind": proposal["kind"], "text": proposal["text"]},
                    "evidence_ids": [] if duplicate else proposal["evidence_ids"],
                    "reason": "Controlled upgrade acceptance",
                }
            else:
                stages.append("extract")
                value = {
                    "candidates": [
                        {"kind": "fact", "text": AUTOMATIC_MEMORY, "evidence_ids": [item["evidence_id"]]}
                        for item in payload["evidence"]
                        if AUTOMATIC_MEMORY in json.dumps(item["content"])
                    ]
                }
            # Released schemas forbid extra fields; follow the requested output
            # schema instead of the target checkout's current content model.
            description_requested = any(
                {"kind", "text", "description"} <= obj.get("properties", {}).keys()
                for schema in objects(body)
                if {"candidates", "action"} & schema.get("properties", {}).keys()
                for obj in objects(schema)
            )
            contents = [*value.get("candidates", []), value.get("content")]
            for content in (item for item in contents if description_requested and item is not None):
                content["description"] = AUTOMATIC_MEMORY
            output = json.dumps(value)
            tools = body.get("tools", [])
            message = {"role": "assistant", "content": output}
            if tools:
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "upgrade-generation",
                            "type": "function",
                            "function": {"name": tools[0]["function"]["name"], "arguments": output},
                        }
                    ],
                }
            response = json.dumps({
                "id": "upgrade-fixture",
                "object": "chat.completion",
                "created": 1,
                "model": "upgrade-fixture",
                "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if tools else "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

    gateway = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=gateway.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"url": f"http://127.0.0.1:{gateway.server_port}/v1", "stages": stages, "embeddings": embeddings}
    finally:
        gateway.shutdown()
        gateway.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("description", [False, True], ids=["released", "current"])
@pytest.mark.parametrize("stage", ["extract", "create", "noop"])
@pytest.mark.parametrize("transport", ["prompted", "tool", "native"])
def test_model_gateway_matches_requested_memory_schema(model_gateway, description, stage, transport):
    properties = {"kind": {"type": "string"}, "text": {"type": "string"}}
    if description:
        properties["description"] = {"type": "string", "minLength": 1, "maxLength": 200}
    if stage == "extract":
        properties["evidence_ids"] = {"type": "array", "items": {"type": "string"}, "minItems": 1}
    record = {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}
    if stage == "extract":
        output_properties = {"candidates": {"type": "array", "items": {"$ref": "#/$defs/Record"}}}
    else:
        output_properties = {
            "action": {"enum": ["create", "noop"]},
            "compared_ids": {"type": "array", "items": {"type": "string"}},
            "target_ids": {"type": "array", "items": {"type": "string"}},
            "content": {"anyOf": [{"$ref": "#/$defs/Record"}, {"type": "null"}]},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
        }
    schema = {
        "$defs": {"Record": record},
        "type": "object",
        "properties": output_properties,
        "required": list(output_properties),
        "additionalProperties": False,
    }
    payload = {"evidence": [{"evidence_id": "source-1", "content": {"content": AUTOMATIC_MEMORY}}]}
    if stage != "extract":
        payload.update({
            "proposal": {
                "kind": "fact",
                "text": AUTOMATIC_MEMORY,
                "description": "Input metadata must not choose the output schema",
                "evidence_ids": ["source-1"],
            },
            "related": [{"item_id": "existing", "text": AUTOMATIC_MEMORY}] if stage == "noop" else [],
        })
    body = {"model": "upgrade-fixture", "messages": [{"role": "user", "content": json.dumps(payload)}]}
    if transport == "prompted":
        body["messages"].insert(
            0, {"role": "system", "content": "Respond using this JSON schema:\n" + json.dumps(schema)}
        )
    elif transport == "tool":
        body["tools"] = [{"type": "function", "function": {"name": "final_result", "parameters": schema}}]
    else:
        body["response_format"] = {"type": "json_schema", "json_schema": {"name": "final_result", "schema": schema}}
    with httpx.Client(trust_env=False) as client:
        response = client.post(model_gateway["url"] + "/chat/completions", json=body)
    response.raise_for_status()
    message = response.json()["choices"][0]["message"]
    if transport == "tool":
        assert message["tool_calls"][0]["function"]["name"] == "final_result"
        output = json.loads(message["tool_calls"][0]["function"]["arguments"])
    else:
        output = json.loads(message["content"])
    Draft202012Validator(schema).validate(output)
    if stage == "noop":
        assert output["action"] == "noop" and output["content"] is None and output["evidence_ids"] == []
    else:
        content = output["candidates"][0] if stage == "extract" else output["content"]
        assert content["text"] == AUTOMATIC_MEMORY
        assert ("description" in content) == description


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def console(python):
    return str(python.with_name("powercontext.exe" if os.name == "nt" else "powercontext"))


def command(arguments, environment, work, log):
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(
            arguments, cwd=work, env=environment, stdout=stream, stderr=subprocess.STDOUT, timeout=1800, check=False
        )
    assert result.returncode == 0, f"Command failed ({result.returncode}); see {log}"
    return log.read_text(encoding="utf-8")


def post(client, path, body):
    response = client.post(path, json=body)
    assert response.is_success, f"{path}: HTTP {response.status_code}: {response.text}"
    return response.json()


@contextmanager
def server(python, environment, work, log):
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(
            [console(python), "server", "run", "--no-env-file", "--host", "127.0.0.1", "--port", str(port)],
            cwd=work,
            env=environment,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=os.name != "nt",
            text=True,
        )
        try:
            with httpx.Client(
                base_url=f"http://127.0.0.1:{port}",
                headers={"Authorization": f"Bearer {TOKEN}"},
                trust_env=False,
                timeout=30,
            ) as client:
                deadline = time.monotonic() + 180
                while True:
                    assert process.poll() is None, f"Server exited before readiness; see {log}"
                    try:
                        response = client.get("/health/ready")
                        if response.status_code == 200 and response.json().get("status") in {"ready", "degraded"}:
                            break
                    except httpx.TransportError:
                        pass
                    assert time.monotonic() < deadline, f"Server readiness timed out; see {log}"
                    time.sleep(0.2)
                yield client
        finally:
            if process.poll() is None:
                # Let the Server close SQL sessions and its embedded engine in
                # order. Signalling the whole group also kills seekDB mid-close.
                process.terminate()
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    if os.name == "nt":
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=10)


def environment_for(case, *, fresh=False):
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POWERCONTEXT_", "PYTEST_", "OPENAI_", "ANTHROPIC_", "OTEL_"))
        and key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    environment.update({
        "POWERCONTEXT_HOME": str(case["work"] / ("fresh-home" if fresh else "home")),
        "POWERCONTEXT_SERVER_AUTH_TOKEN": TOKEN,
        "POWERCONTEXT_SERVER_ACCESS_MODE": "enforced",
        "POWERCONTEXT_SERVER_CURSOR_SIGNING_SECRET": "isolated-upgrade-cursor-signing-key-0001",
        "POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_ROLE": "all",
        "POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_FAMILIES": '["memory"]',
        "POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS": "3600",
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL": "openai-chat:upgrade-fixture",
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL": case["model"]["url"],
        "OPENAI_API_KEY": "isolated-upgrade-fixture",
        "POWERCONTEXT_SERVER_MCP_ENABLED": "false",
        "NO_PROXY": "localhost,127.0.0.1",
        "no_proxy": "localhost,127.0.0.1",
    })
    if case["vector_dimension"]:
        dimension = case["vector_dimension"]
        environment.update({
            "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL": f"openai:upgrade-vector-{dimension}",
            "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_BASE_URL": case["model"]["url"],
            "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID": f"upgrade-vector-{dimension}-unit",
            "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION": str(dimension),
            "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_NORMALIZATION": "unit",
        })
    if case["code_enabled"]:
        scope = case.get("code_scope")
        environment["POWERCONTEXT_SERVER_CODE"] = json.dumps({
            "enabled": True,
            "cache_dir": str(case["work"] / ("fresh-code-cache" if fresh else "code-cache")),
            "repositories": {} if fresh or scope is None else {scope: {"root": str(case["work"] / "repository")}},
        })
    database = case["work"] / ("fresh" if fresh else "database")
    backend = case["backend"]
    environment["POWERCONTEXT_SERVER_DATABASE_KIND"] = backend
    if backend == "sqlite":
        environment["POWERCONTEXT_SERVER_DATABASE_URL"] = f"sqlite+aiosqlite:///{database}"
    elif backend == "seekdb":
        environment["POWERCONTEXT_SERVER_DATABASE_PATH"] = str(database)
        if not fresh:
            environment["POWERCONTEXT_SERVER_DATABASE_DATABASE"] = "test"
    else:
        name = "PC_UPGRADE_OCEANBASE_FRESH_URL" if fresh else "PC_UPGRADE_OCEANBASE_URL"
        url = os.environ[name]
        parsed = urlsplit(url)
        expected = "/pc_upgrade_fresh" if fresh else "/pc_upgrade_test"
        assert parsed.scheme == "mysql+aoceanbase" and parsed.hostname in {"127.0.0.1", "localhost"}
        suffix = {
            "full-server-no-vector": "",
            "full-server-code": "_code",
            "full-server-vector": "_vector",
            "full-server-vector-code": "_vector_code",
        }[case["configuration"]]
        prefix = SOURCE_ADAPTERS[case["source_version"]]["database_prefix"]
        profile_path = f"/{prefix}{suffix}_{'fresh' if fresh else 'test'}"
        if parsed.path == expected:
            parsed = parsed._replace(path=profile_path)
        else:
            assert re.fullmatch(re.escape(profile_path) + r"_[0-9a-f]{8}", parsed.path), (
                "Only dedicated disposable source/profile databases may be used"
            )
        url = parsed.geturl()
        environment["POWERCONTEXT_SERVER_DATABASE_URL"] = url
        environment["PC_UPGRADE_OCEANBASE_URL"] = url
    return environment


def snapshot(case, role, name, *, fresh=False):
    path = case["report"] / f"{name}-schema.json"
    command(
        [
            str(case[role]),
            "-I",
            str(ROOT / "scripts/upgrade_database_probe.py"),
            *(["--canonical"] if role == "target" else []),
            "--backend",
            case["backend"],
            "--database",
            str(case["work"] / ("fresh" if fresh else "database")),
            *(["--seekdb-database", "powercontext"] if case["backend"] == "seekdb" and fresh else []),
            "--output",
            str(path),
        ],
        environment_for(case, fresh=fresh),
        case["work"],
        case["report"] / f"{name}-probe.log",
    )
    result = json.loads(path.read_text())
    if case["backend"] == "seekdb":
        assert result["database"] == ("powercontext" if fresh else "test")
    return result


def task_state(case, role, operation, scope):
    path = case["report"] / f"task-{operation}.json"
    command(
        [
            str(case[role]),
            "-I",
            str(ROOT / "tests/release_upgrades/processing_fixture.py"),
            operation,
            scope,
            str(path),
            case["source_version"],
        ],
        environment_for(case),
        case["work"],
        case["report"] / f"task-{operation}.log",
    )
    return json.loads(path.read_text())


def verify_authorization(case, role, historical):
    fixture = case["report"] / "authorization-fixture.json"
    fixture.write_text(json.dumps(historical), encoding="utf-8")
    command(
        [str(case[role]), "-I", str(ROOT / "tests/release_upgrades/authorization_fixture.py"), str(fixture)],
        environment_for(case),
        case["work"],
        case["report"] / f"{role}-authorization.log",
    )


def seed(client, *, candidate_prefix):
    scopes = [
        post(
            client,
            "/v1/scopes",
            {
                "title": f"Upgrade fixture {i}",
                "summary": "Installed release acceptance",
                "idempotency_key": f"upgrade-scope-{i}",
            },
        )
        for i in range(3)
    ]
    scope = scopes[0]["scope_id"]
    memory = [
        post(client, "/v1/memory/remember", {"scope_id": scope, "kind": "fact", "text": text})["records"][0]
        for text in ("Upgrade fixture preserves historical storage.", "Upgrade fixture retains exact references.")
    ]
    captured = post(
        client,
        "/v1/sources/content",
        {
            "scope_id": scope,
            "source_id": "upgrade-evidence",
            "content": "Historical source evidence for upgrade candidate review.",
        },
    )["source"]
    proposal = {
        "situation": "A release upgrades storage",
        "action": "Run migration acceptance",
        "outcome": "History survives",
        "lesson": "Validate real old databases",
    }
    body = {"scope_id": scope, "proposal": proposal, "source_refs": [captured], "artifact_refs": []}
    candidate = post(client, "/v1/experience/propose", body)
    first = candidate
    candidate = post(
        client,
        f"{candidate_prefix}/revise",
        {
            **body,
            "candidate_id": candidate["candidate_id"],
            "expected_version": candidate["version"],
            "proposal": {**proposal, "lesson": "Validate every required backend"},
        },
    )
    published = post(client, "/v1/experience/propose", body)
    published = post(
        client,
        f"{candidate_prefix}/approve",
        {"scope_id": scope, "candidate_id": published["candidate_id"], "expected_version": published["version"]},
    )
    artifact = post(client, "/v1/experience/get", {"scope_id": scope, "artifact": published["result_artifact"]})
    grant = {
        "subject": {"type": "user", "id": "upgrade-viewer"},
        "resource": {"type": "scope", "scope_id": scope},
        "role": "scope.viewer",
        "idempotency_key": "upgrade-viewer",
    }
    binding = post(client, "/v1/access/bindings/create", grant)
    first_page = client.get("/v1/scopes", params={"limit": 1}).json()
    cursor = first_page["next_cursor"]
    assert cursor
    continuation = client.get("/v1/scopes", params={"limit": 1, "cursor": cursor}).json()
    assert continuation["items"]
    readback = post(client, "/v1/memory/entries/list", {"scope_id": scope})["entries"]
    assert {digest(item) for item in readback} >= {digest(item) for item in memory}
    return {
        "scope": scope,
        "ungranted_scope": scopes[1]["scope_id"],
        "memory": memory,
        "candidate": candidate,
        "first_candidate": first,
        "published": published,
        "artifact": artifact,
        "grant": grant,
        "binding": binding,
        "cursor": cursor,
        "continuation": continuation,
    }


@pytest.fixture(scope="session")
def case(model_gateway):
    # Missing gate inputs are errors, never skips or an implicit local database.
    source_version = os.environ["PC_UPGRADE_SOURCE_VERSION"]
    assert source_version in SOURCE_ADAPTERS, "Add a reviewed adapter for another source release"
    configuration = os.environ["PC_UPGRADE_CONFIGURATION"]
    assert configuration in {
        "full-server-no-vector",
        "full-server-code",
        "full-server-vector",
        "full-server-vector-code",
    }, "Unregistered acceptance configuration"
    code_enabled = configuration.endswith("-code")
    return {
        "source_version": source_version,
        "source": Path(os.environ["PC_UPGRADE_SOURCE_PYTHON"]),
        "target": Path(os.environ["PC_UPGRADE_TARGET_PYTHON"]),
        "backend": os.environ["PC_UPGRADE_BACKEND"],
        "work": Path(os.environ["PC_UPGRADE_WORK"]),
        "report": Path(os.environ["PC_UPGRADE_REPORT_DIR"]),
        "model": model_gateway,
        "configuration": configuration,
        "code_enabled": code_enabled,
        "vector_dimension": {"full-server-vector": 3, "full-server-vector-code": 4}.get(configuration, 0),
    }


def code_cache_snapshot(case):
    directory = case["work"] / "code-cache"
    return {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.rglob("*"))
        if path.is_file() and path.suffix in {".json", ".sqlite"}
    }


def historical_code(case, scope):
    repository = case["work"] / "repository"
    repository.mkdir()
    (repository / "evidence.py").write_text("def upgrade_code_evidence():\n    return 2084\n", encoding="utf-8")
    for arguments in (("init",), ("add", ".")):
        command(
            ["git", "-C", str(repository), *arguments],
            environment_for(case),
            case["work"],
            case["report"] / f"code-git-{arguments[0]}.log",
        )
    case["code_scope"] = scope
    indexed = json.loads(
        command(
            [console(case["source"]), "code", "index", "--scope", scope],
            environment_for(case),
            case["work"],
            case["report"] / "source-code-index.log",
        )
    )
    with server(
        case["source"], environment_for(case), case["work"], case["report"] / "source-code-server.log"
    ) as client:
        result = post(
            client,
            f"/v1/scopes/{scope}/code/query",
            {"operation": {"kind": "symbols", "query": "upgrade_code_evidence"}},
        )
        assert result["status"] == "ok" and result["fingerprint"] == indexed["fingerprint"]
        symbols = [item for item in result["items"] if item["name"] == "upgrade_code_evidence"]
        assert symbols and all(item["path"] == "evidence.py" for item in symbols)
    cache = code_cache_snapshot(case)
    graph_file = "/graph.json" if case["backend"] == "seekdb" else "/graph.sqlite"
    assert any(name.endswith(graph_file) for name in cache)
    return {"fingerprint": result["fingerprint"], "symbols": symbols, "cache_sha256": cache}


@pytest.fixture(scope="session")
def historical(case):
    if case["backend"] == "oceanbase":
        assert not snapshot(case, "source", "empty")["tables"], "Refusing to write into a nonempty test database"
        assert not snapshot(case, "target", "fresh-empty", fresh=True)["tables"]
    with server(case["source"], environment_for(case), case["work"], case["report"] / "source-server.log") as client:
        value = seed(client, candidate_prefix=SOURCE_ADAPTERS[case["source_version"]]["candidate_prefix"])
        if case["vector_dimension"]:
            value["topic_content"] = {
                "title": "Upgrade topic evidence",
                "summary": "Historical semantic retrieval",
                "detail": "# Upgrade evidence\nHistorical Topic vectors must remain usable after maintenance.",
            }
            value["topic"] = post(
                client,
                f"/v1/scopes/{value['scope']}/artifacts",
                {
                    "family": "topic-memory",
                    "content": value["topic_content"],
                },
            )
            assert_vector_reads(client, value)
    verify_authorization(case, "source", value)
    if case["code_enabled"]:
        value["code"] = historical_code(case, value["scope"])
    value["task"] = task_state(case, "source", "seed", value["scope"])
    value["schema"] = snapshot(case, "source", "source")
    assert SOURCE_ADAPTERS[case["source_version"]]["candidate_tables"] | {"pc_scopes", "pc_artifacts"} <= set(
        value["schema"]["tables"]
    )
    if case["vector_dimension"]:
        assert set(value["schema"]["vectors"]) == {
            "pc_atomic_memory_current",
            "pc_topic_memory_vector_topics",
            "pc_topic_memory_vector_chunks",
        }
        assert all(item["rows"] > 0 for item in value["schema"]["vectors"].values())
    (case["report"] / "source-fixture.json").write_text(json.dumps(value, indent=2) + "\n")
    return value


@pytest.fixture(scope="session")
def rejected_legacy_startup(case, historical):
    before = snapshot(case, "target", "before-rejected-startup")
    if case["backend"] == "seekdb":
        implicit_environment = environment_for(case)
        implicit_environment.pop("POWERCONTEXT_SERVER_DATABASE_DATABASE")
        implicit_log = case["report"] / "implicit-legacy-target-server.log"
        with (
            pytest.raises(AssertionError, match="Server exited before readiness"),
            server(case["target"], implicit_environment, case["work"], implicit_log),
        ):
            pytest.fail("The target Server switched away from the historical seekdb database")
        assert "explicit_database_required" in implicit_log.read_text()
        assert "POWERCONTEXT_SERVER_DATABASE_DATABASE=test" in implicit_log.read_text()
    log = case["report"] / "legacy-target-server.log"
    with (
        pytest.raises(AssertionError, match="Server exited before readiness"),
        server(case["target"], environment_for(case), case["work"], log),
    ):
        pytest.fail("The target Server admitted an unmigrated database")
    assert "migration_required" in log.read_text()
    after = snapshot(case, "target", "after-rejected-startup")
    assert canonical_schema(before) == canonical_schema(after)
    assert before["data_sha256"] == after["data_sha256"]
    return True


@pytest.fixture(scope="session")
def migrated(case, historical, rejected_legacy_startup):
    embedding_requests = len(case["model"]["embeddings"])
    environment = environment_for(case)
    env_file = case["work"] / "deployment.env"
    env_file.write_text(
        "".join(
            f"{key}={shlex.quote(value)}\n" for key, value in environment.items() if key.startswith("POWERCONTEXT_")
        )
    )
    common = ["--env-file", str(env_file)]
    if case["backend"] == "oceanbase":
        common += ["--evidence-dir", str(case["report"] / "maintenance"), "--lock-coordination", "single-host"]

    def migration(operation, *options, label=""):
        text = command(
            [console(case["target"]), "server", "db-migrate", operation, *common, *options],
            environment,
            case["work"],
            case["report"] / f"migration-{label}{operation}.log",
        )
        result = json.loads(text)
        assert not result.get("error"), result
        return result

    status = migration("status")
    backup_policy = "auto" if case["backend"] == "sqlite" else "skip"
    plan = migration("plan", "--backup", backup_policy)
    assert plan.get("plan_id"), "The public plan must identify exactly what will be applied"
    expected_source_name = "v" + case["source_version"].replace(".", "_") + "_000"
    assert status["source_migration"] == plan["source_migration"]
    assert plan["source_migration"]["name"] == expected_source_name
    assert plan["source_migration"]["revision"] == SOURCE_ADAPTERS[case["source_version"]]["revision"]
    assert [item["revision"] for item in plan["pending_migrations"]] == plan["revisions"]
    assert [item["name"] for item in plan["pending_migrations"][:3]] == ["v1_4_1_001", "v1_4_1_002", "v1_4_1_003"]
    assert plan["target_migration"] == plan["pending_migrations"][-1]
    # SQLite exercises its supported automatic recovery point. The other
    # disposable backend fixtures explicitly accept no backup.
    applied = migration(
        "apply",
        "--backup",
        backup_policy,
        "--plan-id",
        plan["plan_id"],
        "--yes",
        "--maintenance-confirmed",
        *(["--accept-no-backup"] if backup_policy == "skip" else []),
    )
    verified = migration("verify")
    assert applied["migration"] == verified["migration"] == plan["target_migration"]
    assert verified.get("state") == "ready" and verified.get("server_ready") is True, (
        "Full Server readiness is required; registered-bundle verification is insufficient",
        verified,
    )
    before_repeat = snapshot(case, "target", "before-repeat")
    repeat = migration("plan", "--backup", "skip", label="repeat-")
    repeated = migration(
        "apply",
        "--backup",
        "skip",
        "--plan-id",
        repeat["plan_id"],
        "--yes",
        "--maintenance-confirmed",
        "--accept-no-backup",
        label="repeat-",
    )
    assert repeated["changed"] is False
    assert migration("verify", label="repeat-")["server_ready"] is True
    after_repeat = snapshot(case, "target", "after-repeat")
    assert canonical_schema(before_repeat) == canonical_schema(after_repeat)
    assert before_repeat["data_sha256"] == after_repeat["data_sha256"]
    # Capture pending work before later behavioral tests legitimately dirty the
    # same scope by publishing new Artifacts. Compare exact old counters here.
    processing = task_state(case, "target", "verify", historical["scope"])
    result = {
        "applied": applied,
        "verified": verified,
        "repeated": repeated,
        "schema": snapshot(case, "target", "migrated"),
        "historical": historical,
        "processing": processing,
    }
    if case["vector_dimension"]:
        assert result["schema"]["vectors"] == historical["schema"]["vectors"]
        assert len(case["model"]["embeddings"]) == embedding_requests, "Maintenance must not re-embed history"
    return result


@pytest.fixture(scope="session")
def restored_backup(case, migrated):
    if case["backend"] != "sqlite":
        return None
    backup = Path(migrated["applied"].get("backup_ref") or "")
    assert backup.is_file(), "SQLite installed-release acceptance requires an actual automatic backup"
    before = hashlib.sha256(backup.read_bytes()).hexdigest()
    restored = {
        **case,
        "work": case["work"] / "restored",
        "report": case["report"] / "restored",
    }
    restored["work"].mkdir()
    restored["report"].mkdir()
    shutil.copyfile(backup, restored["work"] / "database")
    historical = migrated["historical"]
    if case["code_enabled"]:
        # Code caches are separate from the database recovery point. The
        # operator must preserve those immutable files as well.
        for name in ("code-cache", "repository"):
            shutil.copytree(case["work"] / name, restored["work"] / name)
        assert code_cache_snapshot(restored) == historical["code"]["cache_sha256"]
    source = snapshot(restored, "source", "restored-source")
    assert canonical_schema(source) == canonical_schema(historical["schema"])
    assert source["data_sha256"] == historical["schema"]["data_sha256"]
    with server(
        restored["source"], environment_for(restored), restored["work"], restored["report"] / "source-server.log"
    ) as client:
        records = post(client, "/v1/memory/entries/list", {"scope_id": historical["scope"]})["entries"]
        assert {digest(item) for item in records} == {digest(item) for item in historical["memory"]}
        candidate = post(
            client,
            SOURCE_ADAPTERS[case["source_version"]]["candidate_prefix"] + "/get",
            {"scope_id": historical["scope"], "candidate_id": historical["candidate"]["candidate_id"]},
        )
        assert candidate == historical["candidate"]
        assert post(client, "/v1/access/bindings/create", historical["grant"]) == historical["binding"]
        continuation = client.get("/v1/scopes", params={"limit": 1, "cursor": historical["cursor"]})
        assert continuation.status_code == 200
        assert continuation.json()["items"] == historical["continuation"]["items"]
        if case["vector_dimension"]:
            assert_vector_reads(client, historical)
    environment = environment_for(restored)

    def migration(operation, *options):
        return json.loads(
            command(
                [console(restored["target"]), "server", "db-migrate", operation, *options],
                environment,
                restored["work"],
                restored["report"] / f"migration-{operation}.log",
            )
        )

    plan = migration("plan", "--backup", "auto")
    assert plan["source_revision"] == SOURCE_ADAPTERS[case["source_version"]]["revision"]
    applied = migration("apply", "--backup", "auto", "--plan-id", plan["plan_id"], "--yes", "--maintenance-confirmed")
    assert applied["changed"] is True and applied["backup_state"] == "completed"
    assert migration("verify")["server_ready"] is True
    with server(restored["target"], environment, restored["work"], restored["report"] / "target-server.log") as client:
        records = post(client, "/v1/memory/entries/list", {"scope_id": historical["scope"]})["entries"]
        assert {digest(item) for item in records} == {digest(item) for item in historical["memory"]}
        found = post(
            client, "/v1/memory/search", {"scope_id": historical["scope"], "query": "Upgrade fixture", "mode": "fts"}
        )["hits"]
        assert {item["memory"]["text"] for item in found} >= {item["text"] for item in historical["memory"]}
        candidate = post(
            client,
            "/v1/candidates/get",
            {"scope_id": historical["scope"], "candidate_id": historical["candidate"]["candidate_id"]},
        )
        assert candidate["proposal"] == historical["candidate"]["proposal"]
        assert candidate["version"] == historical["candidate"]["version"]
        text = "A restored backup accepts writes after its registered upgrade."
        written = post(client, "/v1/memory/remember", {"scope_id": historical["scope"], "kind": "fact", "text": text})[
            "records"
        ]
        assert written and written[0]["text"] == text
        if case["vector_dimension"]:
            assert_vector_reads(client, historical, new_text=text)
    assert hashlib.sha256(backup.read_bytes()).hexdigest() == before
    if case["code_enabled"]:
        assert code_cache_snapshot(restored) == historical["code"]["cache_sha256"]
    return {
        "method": "sqlite_online",
        "source_schema_and_rows_preserved": True,
        "source_server_readback": True,
        "target_upgrade_and_read_write": True,
        "backup_sha256_unchanged": before,
        "separate_code_cache_preserved": case["code_enabled"],
    }


@pytest.fixture
def target(case, migrated, preserved_code, vector_admission):
    with server(case["target"], environment_for(case), case["work"], case["report"] / "target-server.log") as client:
        yield client


@pytest.fixture(scope="session")
def vector_admission(case, migrated):
    if not case["vector_dimension"]:
        return None
    before = snapshot(case, "target", "before-profile-drift")
    environment = environment_for(case)
    environment["POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID"] = "different-embedding-space"
    log = case["report"] / "profile-drift-server.log"
    with (
        pytest.raises(AssertionError, match="Server exited before readiness"),
        server(case["target"], environment, case["work"], log),
    ):
        pytest.fail("Managed storage admitted a different embedding profile")
    assert "embedding_profile_mismatch" in log.read_text()
    after = snapshot(case, "target", "after-profile-drift")
    assert canonical_schema(before) == canonical_schema(after)
    assert before["data_sha256"] == after["data_sha256"]
    if case["backend"] == "sqlite":
        clone = {**case, "work": case["work"] / "missing-helper", "report": case["report"] / "missing-helper"}
        clone["work"].mkdir()
        clone["report"].mkdir()
        command(
            [
                str(case["target"]),
                "-I",
                "-c",
                "import sqlite3,sys\n"
                "from contextlib import closing\n"
                "with closing(sqlite3.connect(sys.argv[1])) as source, "
                "closing(sqlite3.connect(sys.argv[2])) as target:\n"
                " source.backup(target)\n"
                " target.execute('DROP TRIGGER pc_atomic_memory_vec_insert')\n"
                " target.commit()\n",
                str(case["work"] / "database"),
                str(clone["work"] / "database"),
            ],
            environment_for(case),
            case["work"],
            clone["report"] / "remove-helper.log",
        )
        before = snapshot(clone, "target", "before-missing-helper-startup")
        log = clone["report"] / "server.log"
        with (
            pytest.raises(AssertionError, match="Server exited before readiness"),
            server(clone["target"], environment_for(clone), clone["work"], log),
        ):
            pytest.fail("Managed startup repaired a missing vector helper")
        assert "incompatible_schema" in log.read_text()
        after = snapshot(clone, "target", "after-missing-helper-startup")
        assert canonical_schema(before) == canonical_schema(after)
        assert before["data_sha256"] == after["data_sha256"]
    return {"profile_drift_rejected": True, "missing_sqlite_helper_rejected": case["backend"] == "sqlite"}


@pytest.fixture(scope="session")
def preserved_code(case, migrated):
    if not case["code_enabled"]:
        return None
    historical = migrated["historical"]
    source = historical["code"]
    assert code_cache_snapshot(case) == source["cache_sha256"]
    if case["backend"] == "seekdb":
        names = ("pc_code_generations", "pc_code_nodes", "pc_code_edges")
        before = historical["schema"]["data_sha256"]
        after = migrated["schema"]["data_sha256"]
        assert {name: before[name] for name in names} == {name: after[name] for name in names}
    with server(
        case["target"], environment_for(case), case["work"], case["report"] / "target-code-server.log"
    ) as client:
        result = post(
            client,
            f"/v1/scopes/{historical['scope']}/code/query",
            {"operation": {"kind": "symbols", "query": "upgrade_code_evidence"}},
        )
        assert result["status"] == "ok" and result["fingerprint"] == source["fingerprint"]
        symbols = [item for item in result["items"] if item["name"] == "upgrade_code_evidence"]
        assert symbols == source["symbols"]
    assert code_cache_snapshot(case) == source["cache_sha256"]
    return {
        "fingerprint": source["fingerprint"],
        "immutable_cache_files_preserved": True,
        "symbol_ids_preserved": True,
        "server_query_ready": True,
    }


@pytest.fixture(scope="session")
def processed_background(case, migrated):
    historical = migrated["historical"]
    environment = environment_for(case)
    environment["POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS"] = "0.05"
    source_id = "upgrade-background-worker"
    stages = case["model"]["stages"]
    start = len(stages)
    with server(case["target"], environment, case["work"], case["report"] / "worker-server.log") as client:
        post(
            client,
            "/v1/sources/content",
            {"scope_id": historical["scope"], "source_id": source_id, "content": AUTOMATIC_MEMORY},
        )
        deadline = time.monotonic() + 90
        while True:
            entries = post(client, "/v1/memory/entries/list", {"scope_id": historical["scope"]})["entries"]
            generated = next((item for item in entries if item["text"] == AUTOMATIC_MEMORY), None)
            metrics = client.get("/metrics")
            metrics.raise_for_status()
            report = re.search(
                r'^powercontext_server_model_usage_worker_reports_total\{outcome="received"\} (\S+)',
                metrics.text,
                re.MULTILINE,
            )
            if generated and report and float(report[1]) > 0:
                break
            assert time.monotonic() < deadline, "The installed target Worker did not publish and report completion"
            time.sleep(0.2)
        artifact = generated["artifact"]
        revision = client.get(
            f"/v1/scopes/{historical['scope']}/artifacts/{artifact['family']}/"
            f"{artifact['artifact_id']}/revisions/{artifact['revision']}"
        )
        revision.raise_for_status()
        assert source_id in {item["source_id"] for item in revision.json()["sources"]}
        assert {"extract", "reconcile"} <= set(stages[start:])
        found = post(
            client,
            "/v1/memory/search",
            {"scope_id": historical["scope"], "query": "background processing", "mode": "fts"},
        )["hits"]
        assert AUTOMATIC_MEMORY in {item["memory"]["text"] for item in found}
    return {
        "model_stages": sorted(set(stages[start:])),
        "worker_report_received": True,
        "source_evidence_verified": True,
    }


def test_source_release_database(historical):
    assert historical["memory"] and historical["candidate"]["version"] == 2
    assert historical["task"]["requested_generation"] > historical["task"]["handled_generation"]


def test_legacy_startup_is_rejected(rejected_legacy_startup):
    assert rejected_legacy_startup


def test_repeated_migration(migrated):
    assert migrated["repeated"]["changed"] is False


def test_public_migration_commands(migrated, restored_backup):
    assert migrated["verified"]["server_ready"] is True


def test_target_startup(target):
    response = target.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["status"] in {"ready", "degraded"}  # No inference provider in this explicit-write profile.


def assert_vector_reads(client, historical, *, new_text=None):
    scope = historical["scope"]
    hits = post(client, "/v1/memory/search", {"scope_id": scope, "query": "Upgrade fixture", "mode": "vector"})["hits"]
    expected = {item["text"] for item in historical["memory"]}
    if new_text is not None:
        expected.add(new_text)
    assert {item["memory"]["text"] for item in hits} >= expected
    topics = post(
        client,
        f"/v1/scopes/{scope}/artifacts/topic-memory/search",
        {"query": "Upgrade evidence", "mode": "hybrid", "include_scores": True},
    )["results"]
    topic = next(item for item in topics if item["artifact_id"] == historical["topic"]["artifact_id"])
    assert topic["content"] == historical["topic_content"]
    assert topic["revision"] == historical["topic"]["revision"]
    assert {"topic_vector", "detail_vector"} <= topic["scores"]["channels"].keys()


def test_read_write_and_retrieval(case, target, historical):
    scope = historical["scope"]
    records = post(target, "/v1/memory/entries/list", {"scope_id": scope})["entries"]
    assert {digest(item) for item in records} >= {digest(item) for item in historical["memory"]}
    text = "Upgrade fixture accepts new writes after migration."
    written = post(target, "/v1/memory/remember", {"scope_id": scope, "kind": "fact", "text": text})["records"]
    assert written and written[0]["text"] == text
    found = post(target, "/v1/memory/search", {"scope_id": scope, "query": "Upgrade fixture", "mode": "fts"})["hits"]
    texts = {item["memory"]["text"] for item in found}
    assert texts >= {text, *(item["text"] for item in historical["memory"])}
    if case["vector_dimension"]:
        assert_vector_reads(target, historical, new_text=text)


def test_authorization(case, migrated):
    historical = migrated["historical"]
    with server(
        case["target"], environment_for(case), case["work"], case["report"] / "authorization-server.log"
    ) as client:
        assert client.get("/v1/scopes", headers={"Authorization": "Bearer invalid-fixture-token"}).status_code == 401
        # The stable idempotency key must resolve to the SAME persisted grant.
        assert post(client, "/v1/access/bindings/create", historical["grant"]) == historical["binding"]
    # Stop the CLI before opening the same database with the installed SDK,
    # especially for the embedded engine's exclusive process ownership.
    verify_authorization(case, "target", historical)


def test_candidates_and_processing(case, migrated):
    historical = migrated["historical"]
    # Native typed SDK loading/acknowledgment verifies stored invocation formats
    # and generation idempotency; model-driven Worker quality is a separate suite.
    state = migrated["processing"]
    assert {key: state["before"][key] for key in historical["task"]} == historical["task"]
    assert state["after"]["handled_generation"] == historical["task"]["requested_generation"]
    with server(case["target"], environment_for(case), case["work"], case["report"] / "candidate-server.log") as client:
        candidate = historical["candidate"]
        request = {"scope_id": historical["scope"], "candidate_id": candidate["candidate_id"]}
        current = post(client, "/v1/candidates/get", request)
        for key in ("candidate_id", "version", "status", "proposal", "source_refs", "artifact_refs"):
            assert current[key] == candidate[key]
        approved = post(client, "/v1/candidates/approve", {**request, "expected_version": candidate["version"]})
        assert approved["status"] == "approved" and approved["result_artifact"]
        repeated = client.post("/v1/candidates/approve", json={**request, "expected_version": candidate["version"]})
        assert repeated.status_code == 200 and repeated.json() == approved, (
            "Approval retry must return the same publication"
        )
        stale = client.post("/v1/candidates/approve", json={**request, "expected_version": candidate["version"] - 1})
        assert stale.status_code == 409, "A stale revision must not publish a duplicate Artifact"


def test_background_processing(processed_background):
    assert processed_background["worker_report_received"] and processed_background["source_evidence_verified"]


def test_preserved_history_and_cursors(target, historical):
    expected = historical["continuation"]
    page = target.get("/v1/scopes", params={"limit": 1, "cursor": historical["cursor"]})
    assert page.status_code == 200
    assert page.json()["items"] == expected["items"]
    assert bool(page.json()["next_cursor"]) == bool(expected["next_cursor"])
    if expected["next_cursor"]:
        # New tokens have new expiry times; both old and newly issued tokens
        # must continue from the same persisted position.
        old_next = target.get("/v1/scopes", params={"limit": 1, "cursor": expected["next_cursor"]})
        new_next = target.get("/v1/scopes", params={"limit": 1, "cursor": page.json()["next_cursor"]})
        assert old_next.status_code == new_next.status_code == 200
        assert old_next.json()["items"] == new_next.json()["items"]
    artifact = post(
        target,
        "/v1/experience/get",
        {"scope_id": historical["scope"], "artifact": historical["published"]["result_artifact"]},
    )
    assert artifact == historical["artifact"]
    history = post(
        target,
        "/v1/candidates/history",
        {"scope_id": historical["scope"], "candidate_id": historical["candidate"]["candidate_id"]},
    )["versions"]
    for version in (historical["first_candidate"], historical["candidate"]):
        stored = next(item for item in history if item["version"] == version["version"])
        for key in ("version", "proposal", "source_refs", "artifact_refs"):
            assert stored[key] == version[key]


# Quoted tokens and comments are opaque: whitespace, quote characters and
# AUTO_INCREMENT text inside them are schema content, not formatting.
DDL_TOKENS = re.compile(
    r"""'(?:''|\\.|[^'\\])*' | "(?:""|\\.|[^"\\])*" | `(?:``|\\.|[^`\\])*`
    | \[(?:\]\]|[^\]])*\] | /\*.*?\*/ | --[^\r\n]*
    | [A-Za-z_\u0080-\U0010ffff][A-Za-z_0-9$\u0080-\U0010ffff]* | [0-9]+ | ->> | -> | <= | >= | <> | != | \|\| | << | >> | [^\s]
    """,
    re.VERBOSE | re.DOTALL,
)


def canonical_schema(snapshot):
    if snapshot.get("logical_tables") is not None:
        return {
            "tables": snapshot["logical_tables"],
            "views": snapshot.get("views", {}),
            "triggers": snapshot.get("triggers", {}),
        }

    def normalize(sql):
        tokens = DDL_TOKENS.findall(sql)
        table = [lexeme.upper() for lexeme in tokens[:2]] == ["CREATE", "TABLE"]
        depth = 0
        closed_definition = False
        normalized = []
        index = 0
        while index < len(tokens):
            lexeme = tokens[index]
            # Only ignore the table-level next-id counter after the definition.
            # Column AUTO_INCREMENT attributes and all quoted contents remain.
            if (
                table
                and closed_definition
                and depth == 0
                and lexeme.upper() == "AUTO_INCREMENT"
                and tokens[index + 1 : index + 2] == ["="]
                and index + 2 < len(tokens)
                and tokens[index + 2].isdigit()
            ):
                index += 3
                continue
            if lexeme == "(":
                depth += 1
            elif lexeme == ")":
                depth -= 1
                closed_definition = depth == 0
            normalized.append(lexeme)
            index += 1
        return tuple(normalized)

    return {
        kind: {name: normalize(sql or "") for name, sql in snapshot.get(kind, {}).items()}
        for kind in ("tables", "indexes", "views", "triggers")
    }


def test_fresh_upgrade_equivalence(
    case, migrated, processed_background, preserved_code, restored_backup, vector_admission
):
    if case["backend"] == "oceanbase":
        environment = environment_for(case, fresh=True)
        env_file = case["work"] / "fresh.env"
        env_file.write_text(
            "".join(
                f"{key}={shlex.quote(value)}\n" for key, value in environment.items() if key.startswith("POWERCONTEXT_")
            )
        )
        common = [
            "--env-file",
            str(env_file),
            "--evidence-dir",
            str(case["report"] / "fresh-maintenance"),
            "--lock-coordination",
            "single-host",
        ]
        plan = json.loads(
            command(
                [console(case["target"]), "server", "db-migrate", "plan", *common, "--backup", "skip"],
                environment,
                case["work"],
                case["report"] / "fresh-plan.log",
            )
        )
        command(
            [
                console(case["target"]),
                "server",
                "db-migrate",
                "apply",
                *common,
                "--backup",
                "skip",
                "--plan-id",
                plan["plan_id"],
                "--yes",
                "--maintenance-confirmed",
                "--accept-no-backup",
            ],
            environment,
            case["work"],
            case["report"] / "fresh-apply.log",
        )
    with server(
        case["target"], environment_for(case, fresh=True), case["work"], case["report"] / "fresh-server.log"
    ) as client:
        fresh = seed(client, candidate_prefix="/v1/candidates")
        hits = post(
            client, "/v1/memory/search", {"scope_id": fresh["scope"], "query": "Upgrade fixture", "mode": "fts"}
        )["hits"]
        assert {item["memory"]["text"] for item in hits} >= {item["text"] for item in fresh["memory"]}
        if case["vector_dimension"]:
            hits = post(
                client, "/v1/memory/search", {"scope_id": fresh["scope"], "query": "Upgrade fixture", "mode": "vector"}
            )["hits"]
            assert {item["memory"]["text"] for item in hits} >= {item["text"] for item in fresh["memory"]}
    inventory = snapshot(case, "target", "fresh", fresh=True)
    actual = canonical_schema(migrated["schema"])
    expected = canonical_schema(inventory)
    assert actual == expected, (
        "Upgraded and fresh database objects differ; no blanket table/constraint exclusions allowed"
    )
    evidence = {
        "backup_restore": restored_backup,
        "backend": case["backend"],
        "engine_version": inventory["engine_version"],
        "driver_version": inventory["driver_version"],
        "source_schema": digest(migrated["historical"]["schema"]),
        "target_schema": digest(actual),
        "fresh_schema": digest(expected),
        "background_processing": processed_background,
        "configuration": case["configuration"],
        "code": preserved_code,
        "vectors": None
        if not case["vector_dimension"]
        else {
            "dimension": case["vector_dimension"],
            "historical_atomic_and_topic_embeddings_preserved": True,
            "historical_and_new_vector_reads": True,
            "maintenance_embedding_requests": 0,
            "fresh_vector_read": True,
            **vector_admission,
        },
    }
    if case["backend"] == "oceanbase":
        evidence.update(tenant_mode=inventory["tenant_mode"], proxy_version=inventory["proxy_version"])
    Path(os.environ["PC_UPGRADE_EVIDENCE"]).write_text(json.dumps(evidence, indent=2) + "\n")
