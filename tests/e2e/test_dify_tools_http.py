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

"""Registered Dify SDK tools against a real HTTP/SQLite PowerContext Server.

Generation is deterministic; no Dify daemon, application UI or live model is used.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[2]
DIFY = ROOT / "integrations/dify"
SDK_PYTHON = DIFY / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
TOKEN = "disposable-dify-http-test"  # noqa: S105 - public disposable fixture credential


class SdkDriver:
    def __init__(self, process: subprocess.Popen[str], server_url: str):
        self.process = process
        self.server_url = server_url
        self.called: set[str] = set()

    def job(self, name: str, scope_id: str, parameters: dict[str, Any], **credentials: Any) -> dict[str, Any]:
        return {
            "tool": name,
            "parameters": parameters,
            "host_cast": bool(
                os.environ.get("POWERCONTEXT_DIFY_SOURCE") and os.environ.get("POWERCONTEXT_DIFY_DAEMON_SOURCE")
            ),
            "credentials": {
                "server_url": self.server_url,
                "api_token": TOKEN,
                "scope_id": scope_id,
                **credentials,
            },
        }

    def batch(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        assert self.process.stdin is not None and self.process.stdout is not None
        self.process.stdin.write(json.dumps(jobs, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        assert line, f"SDK test driver exited with {self.process.poll()}"
        return json.loads(line)

    def call(
        self, name: str, scope_id: str, parameters: dict[str, Any], *, host_cast: bool | None = None
    ) -> dict[str, Any]:
        job = self.job(name, scope_id, parameters)
        if host_cast is not None:
            job["host_cast"] = host_cast
        result = self.batch([job])[0]
        assert result["ok"], json.dumps(result, ensure_ascii=False)
        self.called.add(name)
        return result["data"]


@pytest.fixture
def sdk_http(tmp_path: Path) -> Iterator[tuple[SdkDriver, httpx.Client, str, str]]:
    if not SDK_PYTHON.is_file():
        pytest.skip("Run uv sync --locked --project integrations/dify --python 3.12 for Dify SDK HTTP tests")
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    env = {**os.environ, "PYTHONUTF8": "1"}
    with (tmp_path / "server.log").open("w", encoding="utf-8") as server_log:
        server = subprocess.Popen(
            [
                sys.executable,
                "-X",
                "utf8",
                str(Path(__file__).with_name("dify_server.py")),
                "--port",
                str(port),
                "--database",
                str(tmp_path / "runtime.db"),
            ],
            cwd=ROOT,
            env=env,
            stdout=server_log,
            stderr=subprocess.STDOUT,
        )
        driver = None
        try:
            with httpx.Client(base_url=url, headers={"Authorization": "Bearer " + TOKEN}, timeout=15) as http:
                deadline = time.monotonic() + 40
                while True:
                    if server.poll() is not None:
                        pytest.fail((tmp_path / "server.log").read_text(encoding="utf-8"))
                    try:
                        if http.get("/health/ready").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    assert time.monotonic() < deadline, "Disposable Server startup timed out"
                    time.sleep(0.1)
                scopes = []
                for name in ("A", "B"):
                    response = http.post(
                        "/v1/scopes",
                        json={
                            "title": f"Dify Scope {name}",
                            "summary": "Disposable HTTP tool acceptance.",
                            "idempotency_key": f"dify-http-{name}",
                        },
                    )
                    assert response.status_code == 201, response.text
                    scopes.append(response.json()["scope_id"])
                with (tmp_path / "sdk.log").open("w", encoding="utf-8") as sdk_log:
                    driver = subprocess.Popen(
                        [str(SDK_PYTHON), "-X", "utf8", str(DIFY / "tests/sdk_driver.py")],
                        cwd=ROOT,
                        env=env,
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=sdk_log,
                        encoding="utf-8",
                    )
                    yield SdkDriver(driver, url), http, scopes[0], scopes[1]
        finally:
            for process in (driver, server):
                if process is not None:
                    process.terminate()
                    try:
                        process.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=10)


def test_all_19_sdk_tools_preserve_memory_handoff_and_candidate_readback(sdk_http) -> None:
    sdk, http, a, b = sdk_http
    remembered = sdk.call("pc_remember", a, {"kind": "decision", "text": "中文固定范围验收: use scoped HTTP."})
    citation = remembered["entry"]["citation"]
    assert sdk.call("pc_memory_get", a, {"citation": json.dumps(citation)})["text"].startswith("中文")
    assert sdk.call("pc_memory_list", a, {})["entries"]
    assert sdk.call("pc_search", a, {"query": "中文固定范围验收", "mode": "fts"})["hits"]
    context = sdk.call("pc_prepare_context", a, {"query": "中文固定范围验收"})
    assert context["status"] == "ready"
    assert context["content_bytes"] == len(context["content"].encode("utf-8"))
    assert "中文固定范围验收" in context["content"]
    revised = sdk.call(
        "pc_memory_revise",
        a,
        {
            "citation": json.dumps(citation),
            "kind": "constraint",
            "text": "中文固定范围验收: Scope credentials stay fixed.",
        },
    )
    assert sdk.call("pc_memory_get", a, {"citation": json.dumps(revised["entry"]["citation"])})["kind"] == "constraint"

    captured = sdk.call(
        "pc_capture_source",
        a,
        {
            "source_id": "dify:中文 turn/1",
            "content": "Verified scoped context over HTTP.",
            "metadata": json.dumps({"tags": {"component": "dify"}, "count": 2, "nested": {"ok": True}}),
        },
    )
    source = captured["source"]
    evidence = [{"kind": "source", "source_ref": source}]
    activation = sdk.call(
        "pc_handoff_activate",
        a,
        {
            "boundary_source": json.dumps(source),
            "objective": "Continue the verified integration.",
        },
    )
    assert activation["draft"] is not None
    draft = sdk.call(
        "pc_handoff_prepare",
        a,
        {
            "objective": "Transfer the integration state.",
            "evidence": json.dumps(evidence),
        },
    )
    prepared = sdk.call("pc_handoff_finalize", a, {"draft": json.dumps(draft, ensure_ascii=False)})
    temporary = sdk.call(
        "pc_handoff_continue",
        a,
        {
            "selection": "prepared",
            "prepared": json.dumps(prepared, ensure_ascii=False),
        },
    )
    assert temporary["status"] == "resolved"
    committed = sdk.call("pc_handoff_commit", a, {"handoff": json.dumps(prepared)})
    exact = sdk.call("pc_handoff_continue", a, {"selection": "exact", "revision": json.dumps(committed["reference"])})
    latest = sdk.call("pc_handoff_continue", a, {"selection": "latest"})
    assert exact["content"] == latest["content"]
    assert "中文交接状态" in json.dumps(exact, ensure_ascii=False)

    generated = sdk.call("pc_experience_generate", a, {"source_refs": json.dumps([source])})
    candidate = generated["candidate"]
    assert generated["status"] == "pending"
    inspected = sdk.call("pc_review_get", a, {"candidate_id": candidate["candidate_id"]})
    assert inspected["candidate_id"] == candidate["candidate_id"]
    page = sdk.call("pc_review_list", a, {"family": '"experience"'})
    assert candidate["candidate_id"] in {item["candidate_id"] for item in page["candidates"]}

    # Approval belongs to the administrator, not to the 19-tool plugin.
    approved = http.post(
        "/v1/candidates/approve",
        json={
            "scope_id": a,
            "candidate_id": candidate["candidate_id"],
            "expected_version": candidate["version"],
        },
    )
    assert approved.status_code == 200, approved.text
    experience_ref = approved.json()["result_artifact"]
    experience = sdk.call("pc_experience_get", a, {"artifact": json.dumps(experience_ref)})
    assert experience["artifact"] == experience_ref
    skill_candidate = sdk.call(
        "pc_skill_generate",
        a,
        {
            "origin": "experience",
            "artifact_refs": json.dumps([experience_ref]),
        },
    )["candidate"]
    approved = http.post(
        "/v1/candidates/approve",
        json={
            "scope_id": a,
            "candidate_id": skill_candidate["candidate_id"],
            "expected_version": skill_candidate["version"],
        },
    )
    assert approved.status_code == 200, approved.text
    skill_ref = approved.json()["result_artifact"]
    skill = sdk.call("pc_skill_get", a, {"artifact": json.dumps(skill_ref)})
    assert skill["artifact"] == skill_ref

    retirement = sdk.call("pc_memory_retire", a, {"citation": json.dumps(revised["entry"]["citation"])})
    retired = sdk.call("pc_memory_get", a, {"citation": json.dumps(retirement["entry"]["citation"])})
    assert retired["state"] == "inactive"
    assert sdk.call("pc_search", b, {"query": "中文固定范围验收", "mode": "fts"})["hits"] == []
    catalog = json.loads((DIFY / "plugin/powercontext_dify/contract.json").read_text(encoding="utf-8"))["tools"]
    assert sdk.called == set(catalog)


def test_sdk_concurrent_scopes_and_rejected_credentials(sdk_http) -> None:
    sdk, _http, a, b = sdk_http
    sdk.call("pc_remember", a, {"kind": "decision", "text": "quasaralphascopeonly"})
    sdk.call("pc_remember", b, {"kind": "decision", "text": "nebulabetascopeonly"})
    results = sdk.batch([
        sdk.job("pc_search", a, {"query": "nebulabetascopeonly", "mode": "fts"}),
        sdk.job("pc_search", b, {"query": "quasaralphascopeonly", "mode": "fts"}),
    ])
    assert all(result["ok"] and not result["data"]["hits"] for result in results), json.dumps(results)
    results = sdk.batch([
        sdk.job("pc_search", a, {"query": "needle"}, api_token="wrong-disposable-token"),  # noqa: S106
        sdk.job("pc_search", "", {"query": "needle"}, binding_external_id="deployment:missing"),
    ])
    assert [result["error"]["code"] for result in results] == ["authentication_failed", "not_found"]


@pytest.mark.skipif(
    not (os.environ.get("POWERCONTEXT_DIFY_SOURCE") and os.environ.get("POWERCONTEXT_DIFY_DAEMON_SOURCE")),
    reason="Set the pinned Dify and daemon source paths",
)
def test_host_cast_nullable_inputs_preserve_handoff_and_generation_readback(sdk_http) -> None:
    sdk, _http, scope, _other = sdk_http
    source = sdk.call(
        "pc_capture_source", scope, {"source_id": "dify:nullable", "content": "Verified nullable host inputs."}
    )["source"]
    draft = sdk.call(
        "pc_handoff_prepare",
        scope,
        {"objective": "Continue verified work.", "evidence": json.dumps([{"kind": "source", "source_ref": source}])},
    )
    prepared = sdk.call("pc_handoff_finalize", scope, {"draft": json.dumps(draft)})
    committed = sdk.call("pc_handoff_commit", scope, {"handoff": json.dumps(prepared)})
    direct = sdk.call("pc_handoff_continue", scope, {"selection": "latest"})
    for selection, handoff, revision in (
        ("latest", None, None),
        ("exact", None, committed["reference"]),
        ("prepared", prepared, None),
    ):
        continued = sdk.call(
            "pc_handoff_continue",
            scope,
            {"selection": selection, "prepared": json.dumps(handoff), "revision": json.dumps(revision)},
            host_cast=True,
        )
        assert continued["status"] == "resolved"
        assert continued["content"] == direct["content"]

    for tool in ("pc_experience_generate", "pc_skill_generate"):
        parameters = {"source_refs": json.dumps([source]), "target": "null", "reason": "null"}
        if tool == "pc_skill_generate":
            parameters["origin"] = "source"
        generated = sdk.call(tool, scope, parameters, host_cast=True)
        assert generated["status"] == "pending"
        candidate = generated["candidate"]
        inspected = sdk.call("pc_review_get", scope, {"candidate_id": candidate["candidate_id"]})
        assert inspected["candidate_id"] == candidate["candidate_id"]
        assert inspected["target"] is None
    page = sdk.call("pc_review_list", scope, {"family": "null", "cursor": "null"}, host_cast=True)
    assert {candidate["family"] for candidate in page["candidates"]} == {"experience", "skill"}
