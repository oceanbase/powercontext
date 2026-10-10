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

from tests.e2e import helm_acceptance


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
    if payload.get("base") == "http://127.0.0.1:8000":
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
    monkeypatch.setattr(sys, "argv", ["helm_acceptance.py", "--namespace", "helm-test", "--release", "pc"])
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
