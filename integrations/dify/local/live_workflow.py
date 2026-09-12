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

"""Exercise real Dify workflow -> daemon -> plugin -> PC HTTP calls."""

import asyncio
import json
import sys
from itertools import pairwise
from uuid import uuid4

import yaml
from console import Console
from prepare import ROOT, STATE

sys.path.insert(0, str(ROOT / "integrations/dify/powercontext"))
from bridge import MemoryIdentity, binding_key
from powercontext.client import PowerContextClient
from powercontext.http import CreateScopeRequest, SetScopeBindingRequest


def graph(operation, request, identity, credential):
    declaration = yaml.safe_load((ROOT / "integrations/dify/powercontext/tools" / f"{operation}.yaml").read_text())
    data = [
        {"type": "start", "title": "Start", "variables": []},
        {
            "type": "tool",
            "title": operation,
            "provider_id": "oceanbase/powercontext/powercontext",
            "provider_name": "powercontext",
            "provider_type": "builtin",
            "tool_name": operation,
            "tool_label": operation,
            "credential_id": credential,
            "tool_node_version": "2",
            "paramSchemas": declaration["parameters"],
            # Workflow's constant inputs pass through its text-template evaluator.
            "tool_configurations": {"memory_context": {"type": "constant", "value": json.dumps(identity)}},
            "tool_parameters": {"request": {"type": "constant", "value": json.dumps(request)}},
        },
        {
            "type": "end",
            "title": "End",
            "outputs": [
                {"variable": "result", "value_selector": ["tool", "json"], "value_type": "array[object]"},
                {"variable": "user_id", "value_selector": ["sys", "user_id"], "value_type": "string"},
            ],
        },
    ]
    names = ["start", "tool", "end"]
    nodes = [
        {"id": name, "type": "custom", "data": value, "position": {"x": i * 300, "y": 100}}
        for i, (name, value) in enumerate(zip(names, data, strict=True))
    ]
    edges = [
        {
            "id": f"{a}-{b}",
            "source": a,
            "target": b,
            "sourceHandle": "source",
            "targetHandle": "target",
            "type": "custom",
            "data": {"sourceType": data[i]["type"], "targetType": data[i + 1]["type"]},
        }
        for i, (a, b) in enumerate(pairwise(names))
    ]
    return {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 1}}


class Workflow:
    def __init__(self):
        self.console = Console()
        path = STATE / "workflow.json"
        if path.exists():
            self.app_id = json.loads(path.read_text())["id"]
        else:
            app = self.console.post("/apps", json={"name": "PC live acceptance", "mode": "workflow"})
            self.app_id = app["id"]
            path.write_text(json.dumps({"id": self.app_id}, indent=2) + "\n")
        self.credential = json.loads((STATE / "provider.json").read_text())["credentials"][0]["id"]
        self.events = []

    def invoke(self, operation, request, subject):
        identity = {"app_id": self.app_id, "subject_kind": "business", "subject_id": subject}
        current = self.console.client.get(f"/apps/{self.app_id}/workflows/draft")
        revision = current.json().get("hash") if current.is_success else None
        self.console.post(
            f"/apps/{self.app_id}/workflows/draft",
            json={
                "graph": graph(operation, request, identity, self.credential),
                "features": {},
                "hash": revision,
            },
        )
        events = []
        with self.console.client.stream(
            "POST", f"/apps/{self.app_id}/workflows/draft/run", json={"inputs": {}}
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if line.startswith("data:"):
                    events.append(json.loads(line[5:]))
        self.events.append({"operation": operation, "subject": subject, "events": events})
        finished = next((event for event in events if event.get("event") == "workflow_finished"), None)
        if not finished or finished["data"]["status"] != "succeeded":
            raise RuntimeError(json.dumps(events, ensure_ascii=False)[-2500:])
        outputs = finished["data"]["outputs"]
        print(operation, subject, "workflow succeeded")
        return outputs["result"][0]

    async def bind(self, subject):
        async with PowerContextClient("http://127.0.0.1:31800", token=self.console.secrets["PC_TOKEN"]) as client:
            scope = await client.create_scope(
                CreateScopeRequest(
                    title=f"Dify local {subject}",
                    summary="Isolated deployed acceptance",
                    idempotency_key=f"dify:{self.app_id}:{subject}",
                )
            )
            key = binding_key(
                "pc-dify-local", MemoryIdentity(app_id=self.app_id, subject_kind="business", subject_id=subject)
            )
            await client.set_scope_binding(SetScopeBindingRequest(key=key, scope_id=scope.scope_id))
            return scope.scope_id


def main():
    workflow = Workflow()
    scopes = {subject: asyncio.run(workflow.bind(subject)) for subject in ("first", "second")}
    marker = "silverorchard" + uuid4().hex[:10]
    try:
        written = workflow.invoke(
            "remember_memory", {"kind": "local-acceptance", "text": f"The project code is {marker}."}, "first"
        )
        assert written.get("status") != "error", written
        recalled = workflow.invoke("prepare_context", {"query": marker, "max_bytes": 2000}, "first")
        assert recalled["status"] == "ready" and marker in recalled["content"], recalled
        other = workflow.invoke("prepare_context", {"query": marker, "max_bytes": 2000}, "second")
        assert other["status"] == "empty", other
        missing = workflow.invoke("prepare_context", {"query": marker}, "unbound")
        assert missing["status"] == "error", missing
        captured = workflow.invoke(
            "capture_event",
            {
                "event_id": f"live:{uuid4()}",
                "event": "tool_result",
                "sequence": 1,
                "payload": {"result": marker, "api_key": "redact-this-local-test-value"},
                "max_bytes": 512,
            },
            "first",
        )
        assert captured["status"] == "accepted", captured

        async def inspect_source():
            async with PowerContextClient(
                "http://127.0.0.1:31800", token=workflow.console.secrets["PC_TOKEN"]
            ) as client:
                source = await client.get_source(scopes["first"], "content", captured["source"]["source_id"])
                serialized = source.model_dump_json()
                assert "redact-this-local-test-value" not in serialized
                assert "[REDACTED]" in serialized

        asyncio.run(inspect_source())
        print("PASS: actual workflow memory write, recall, business isolation, missing binding and capture.")
    finally:
        (STATE / "workflow-events.json").write_text(json.dumps(workflow.events, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    main()
