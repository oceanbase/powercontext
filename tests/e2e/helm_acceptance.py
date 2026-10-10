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

"""Opt-in destructive Pod acceptance checks for an isolated Helm test deployment."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from uuid import uuid4


def check_failover(kubectl, pods, execute, rollout, background_name):
    original_pod = pods("background")[0]
    original_lease = None
    for _ in range(30):
        original_lease = execute({"action": "lease"})
        if original_lease:
            break
        time.sleep(2)
    if not original_lease:
        raise RuntimeError("No active global lease; configure a background capability before running this test")  # noqa: TRY003
    try:
        kubectl("scale", "deployment/" + background_name, "--replicas=2")
        rollout("background")
        if execute({"action": "lease"}) != original_lease:
            raise RuntimeError("Leader changed before fault injection; rerun against a stable deployment")  # noqa: TRY003
        kubectl("delete", "pod", original_pod, "--wait=true")
        for _ in range(60):
            lease = execute({"action": "lease"})
            if lease and lease[0] != original_lease[0] and lease[1] > original_lease[1]:
                print("PASS: global lease acquired by a different holder with a newer generation")
                break
            time.sleep(2)
        else:
            raise RuntimeError("Background leader did not recover within 120 seconds")  # noqa: TRY003
    finally:
        kubectl("scale", "deployment/" + background_name, "--replicas=1")
        rollout("background")


def validate_topology(deployments) -> None:
    if set(deployments) != {"api", "background"}:
        raise RuntimeError("Expected API and background Deployments for this release")  # noqa: TRY003
    if deployments["api"]["spec"].get("replicas", 1) < 2:
        raise RuntimeError("Acceptance requires api.replicas >= 2; use values-acceptance.yaml")  # noqa: TRY003
    if deployments["background"]["spec"]["replicas"] != 1:
        raise RuntimeError("Start with exactly one background replica in an isolated test deployment")  # noqa: TRY003


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--namespace", required=True)
    parser.add_argument("--release", required=True)
    args = parser.parse_args()
    selector = f"app.kubernetes.io/name=powercontext,app.kubernetes.io/instance={args.release}"

    def kubectl(*command: str, source: str | None = None) -> str:
        result = subprocess.run(
            ["kubectl", "--namespace", args.namespace, *command],  # noqa: S607
            input=source,
            text=True,
            capture_output=True,
            check=False,
            timeout=360,
        )
        if result.returncode:
            # Do not echo application errors that may contain connection details.
            raise RuntimeError("kubectl operation failed; inspect the test deployment locally")  # noqa: TRY003
        return result.stdout

    def pods(role: str, *, ready_only: bool = False) -> list[str]:
        data = json.loads(kubectl("get", "pods", "-l", selector + f",app.kubernetes.io/component={role}", "-o", "json"))
        return [
            item["metadata"]["name"]
            for item in data["items"]
            if not item["metadata"].get("deletionTimestamp")
            and (
                not ready_only
                or any(
                    condition["type"] == "Ready" and condition["status"] == "True"
                    for condition in item.get("status", {}).get("conditions", [])
                )
            )
        ]

    def ready_api_pods() -> list[str]:
        names = pods("api", ready_only=True)
        if len(names) < 2:
            raise RuntimeError("Acceptance requires at least two Ready, non-terminating API Pods")  # noqa: TRY003
        return names

    def execute(payload: dict[str, str], pod: str | None = None):
        pod = pod or ready_api_pods()[0]
        source = Path(__file__).with_name("helm_probe.py").read_text()
        return json.loads(kubectl("exec", "-i", pod, "--", "python", "-", json.dumps(payload), source=source))

    def rollout(role: str) -> None:
        kubectl("rollout", "status", f"deployment/{deployments[role]['metadata']['name']}", "--timeout=300s")

    items = json.loads(kubectl("get", "deployments", "-l", selector, "-o", "json"))["items"]
    deployments = {item["metadata"]["labels"]["app.kubernetes.io/component"]: item for item in items}
    validate_topology(deployments)
    background = deployments["background"]
    rollout("api")
    rollout("background")
    services = json.loads(kubectl("get", "services", "-l", selector, "-o", "json"))["items"]
    if len(services) != 1:
        raise RuntimeError("Expected one API Service for this release")  # noqa: TRY003
    service = services[0]
    base = f"http://{service['metadata']['name']}:{service['spec']['ports'][0]['port']}"
    scope = execute({"base": base, "action": "create", "key": "helm-" + str(uuid4())})
    kubectl("delete", "pods", "-l", selector + ",app.kubernetes.io/component=api", "--wait=true")
    rollout("api")
    execute({"base": base, "action": "read", "scope": scope["scope_id"]})
    for pod in ready_api_pods():
        execute({"base": "http://127.0.0.1:8000", "action": "read", "scope": scope["scope_id"]}, pod)
    print("PASS: authenticated HTTP/MCP through Service and persisted Scope readable from every replacement API Pod")

    check_failover(kubectl, pods, execute, rollout, background["metadata"]["name"])
    print("Test Scope retained:", scope["scope_id"])


if __name__ == "__main__":
    main()
