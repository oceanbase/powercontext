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

"""Acceptance CLI safeguards with simulated kubectl responses, without a cluster."""

import json
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, McpConfig, ServerSettings
from tests.e2e import helm_acceptance, helm_probe


def api_pod(name, *, ready=True, terminating=False):
    metadata = {"name": name}
    if terminating:
        metadata["deletionTimestamp"] = "2026-10-05T00:00:00Z"
    return {
        "metadata": metadata,
        "status": {"conditions": [{"type": "Ready", "status": "True" if ready else "False"}]},
    }


def simulate_probe(state, args):
    payload = json.loads(args[-1])
    if payload["action"] == "create":
        state["mutations"].append(["create_scope"])
        return {"scope_id": f"test-scope-{len(state['mutations'])}"}
    if payload["action"] == "page":
        state["pages"].append((args[2], payload))
        if "cursor" not in payload:
            return {"items": [{"scope_id": "test-scope-2"}], "next_cursor": "signed-cursor"}
        if state["bad_cursor_key"]:
            raise RuntimeError("Scope pagination failed; check shared cursor signing keys")  # noqa: TRY003
        return {"items": [{"scope_id": "test-scope-3"}], "next_cursor": None}
    if payload["action"] == "read" and payload.get("base") == "http://127.0.0.1:8000":
        state["direct_reads"].append(args[2])
    return state["lease"] if payload["action"] == "lease" else {"scope_id": "test-scope"}


@pytest.fixture
def cluster(monkeypatch):
    mutations: list[list[str]] = []
    state = {
        "api_replicas": 2,
        "api_pods": [api_pod("api-old-0"), api_pod("api-old-1")],
        "replacement_pods": [api_pod("api-new-0"), api_pod("api-new-1")],
        "lease": ["original", 1],
        "mutations": mutations,
        "direct_reads": [],
        "pages": [],
        "bad_cursor_key": False,
    }

    def run(command, **kwargs):
        args = command[3:]  # kubectl --namespace helm-test
        if args[:2] == ["get", "deployments"]:
            response = {
                "items": [
                    {
                        "metadata": {"name": role, "labels": {"app.kubernetes.io/component": role}},
                        "spec": {"replicas": state["api_replicas"] if role == "api" else 1},
                    }
                    for role in ("api", "background")
                ]
            }
        elif args[:2] == ["get", "pods"]:
            response = {
                "items": state["api_pods"]
                if args[3].endswith("component=api")
                else [{"metadata": {"name": "background-original"}}]
            }
        elif args[:2] == ["get", "services"]:
            response = {"items": [{"metadata": {"name": "pc"}, "spec": {"ports": [{"port": 8000}]}}]}
        elif args[0] == "rollout":
            response = {}
        elif args[0] in ("delete", "scale"):
            mutations.append(args)
            if args[:2] == ["delete", "pods"]:
                state["api_pods"] = state["replacement_pods"]
            elif args[:2] == ["delete", "pod"]:
                state["lease"] = ["replacement", 2]
            response = {}
        elif args[0] == "exec":
            response = simulate_probe(state, args)
        else:
            pytest.fail(f"Unexpected kubectl command: {args}")
        return subprocess.CompletedProcess(command, 0, json.dumps(response), "")

    monkeypatch.setattr(helm_acceptance.subprocess, "run", run)
    monkeypatch.setattr(
        sys, "argv", ["helm_acceptance.py", "--namespace", "helm-test", "--release", "pc", "--confirm-disposable"]
    )
    return state


def test_rejects_insufficient_api_replicas_before_mutation(cluster):
    cluster["api_replicas"] = 1
    with pytest.raises(RuntimeError, match=r"api\.replicas >= 2"):
        helm_acceptance.main()
    assert cluster["mutations"] == []


@pytest.mark.parametrize("second", [None, api_pod("unready", ready=False), api_pod("terminating", terminating=True)])
def test_requires_two_ready_live_api_pods_before_mutation(cluster, second):
    cluster["api_pods"] = [api_pod("api-0")] + ([] if second is None else [second])
    with pytest.raises(RuntimeError, match="two Ready, non-terminating API Pods"):
        helm_acceptance.main()
    assert cluster["mutations"] == []


def test_rechecks_api_replicas_after_replacement(cluster, capsys):
    cluster["replacement_pods"] = [api_pod("api-new-0")]
    with pytest.raises(RuntimeError, match="two Ready, non-terminating API Pods"):
        helm_acceptance.main()
    assert "PASS:" not in capsys.readouterr().out


def test_checks_each_replacement_api_pod(cluster, capsys):
    helm_acceptance.main()
    assert set(cluster["direct_reads"]) == {"api-new-0", "api-new-1"}
    assert "authenticated HTTP/MCP through Service" in capsys.readouterr().out


def test_requires_explicit_confirmation_before_any_cluster_operation(cluster, monkeypatch):
    monkeypatch.setattr(sys, "argv", sys.argv[:-1])
    monkeypatch.setattr(helm_acceptance.subprocess, "run", lambda *args, **kwargs: pytest.fail("Cluster accessed"))
    with pytest.raises(SystemExit) as error:
        helm_acceptance.main()
    assert error.value.code == 2


def test_cursor_is_consumed_by_a_different_api_pod(cluster, capsys):
    cluster["replacement_pods"].append(api_pod("api-new-2"))
    helm_acceptance.main()
    first, *resumed = cluster["pages"]
    assert first[0] == "api-new-0"
    assert "cursor" not in first[1]
    assert {pod for pod, _ in resumed} == {"api-new-1", "api-new-2"}
    assert all(
        payload["cursor"] == "signed-cursor" and payload["parent"] == first[1]["parent"] for _, payload in resumed
    )
    assert "cursor issued by one API Pod resumes" in capsys.readouterr().out


def test_cursor_rejection_cannot_report_acceptance_success(cluster, capsys):
    cluster["bad_cursor_key"] = True
    with pytest.raises(RuntimeError, match="shared cursor signing keys"):
        helm_acceptance.main()
    assert "cursor issued by one API Pod resumes" not in capsys.readouterr().out


@pytest.mark.parametrize("shared_key", [True, False])
def test_page_probe_checks_real_server_cursor_signatures(tmp_path, monkeypatch, shared_key):
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'pagination.db'}")

    def app(key):
        return create_server_app(
            settings=ServerSettings(
                database=database,
                cursor_signing_secret=SecretStr(key * 32),
                auth=BearerAuthConfig(enabled=True, token=SecretStr("helm-test-token")),
                access=AccessControlConfig(mode="enforced"),
                mcp=McpConfig(enabled=False),
            )
        )

    headers = {"Authorization": "Bearer helm-test-token"}
    with TestClient(app("a"), headers=headers) as first:
        parent = first.post(
            "/v1/scopes", json={"title": "Parent", "summary": "Test", "idempotency_key": "parent"}
        ).json()
        children = set()
        for key in ("first", "second"):
            response = first.post(
                "/v1/scopes",
                json={"title": key, "summary": "Test", "idempotency_key": key, "parent_scope_id": parent["scope_id"]},
            )
            assert response.status_code == 201
            children.add(response.json()["scope_id"])

        def request(_base, path):
            response = first.get(path)
            return response.status_code, response.json(), response.headers

        monkeypatch.setattr(helm_probe, "request", request)
        payload = {"base": "http://127.0.0.1:8000", "parent": parent["scope_id"]}
        page = helm_probe.page_scopes(payload)
        assert page["next_cursor"]

    with TestClient(app("a" if shared_key else "b"), headers=headers) as second:

        def request(_base, path):
            response = second.get(path)
            return response.status_code, response.json(), response.headers

        monkeypatch.setattr(helm_probe, "request", request)
        if shared_key:
            resumed = helm_probe.page_scopes({**payload, "cursor": page["next_cursor"]})
            assert {item["scope_id"] for item in [*page["items"], *resumed["items"]]} == children
            assert not resumed["next_cursor"]
        else:
            with pytest.raises(AssertionError, match="shared cursor signing keys"):
                helm_probe.page_scopes({**payload, "cursor": page["next_cursor"]})
