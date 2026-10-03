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

"""Test runtime user isolation through Dify's actual published local Service API."""

import asyncio
import json
from uuid import uuid4

import httpx
from live_workflow import MemoryIdentity, PowerContextClient, Workflow, binding_key, graph
from powercontext.http import CreateScopeRequest, SetScopeBindingRequest
from prepare import STATE


def main():
    workflow = Workflow()
    console = workflow.console
    keys = console.get(f"/apps/{workflow.app_id}/api-keys")["data"]
    key = keys[0] if keys else console.post(f"/apps/{workflow.app_id}/api-keys", json={})
    client = httpx.Client(
        base_url="http://127.0.0.1:31501/v1", headers={"Authorization": "Bearer " + key["token"]}, timeout=120
    )
    records = []

    def publish(operation, request):
        current = console.get(f"/apps/{workflow.app_id}/workflows/draft")
        value = graph(operation, request, {}, workflow.credential)
        value["nodes"][1]["data"]["tool_configurations"] = {}
        console.post(
            f"/apps/{workflow.app_id}/workflows/draft", json={"graph": value, "features": {}, "hash": current["hash"]}
        )
        console.post(f"/apps/{workflow.app_id}/workflows/publish", json={"marked_name": "Local acceptance"})

    def invoke(user):
        response = client.post("/workflows/run", json={"inputs": {}, "response_mode": "blocking", "user": user})
        response.raise_for_status()
        result = response.json()
        records.append(result)
        assert result["data"]["status"] == "succeeded", result
        details = console.get(f"/apps/{workflow.app_id}/workflow-runs/{result['workflow_run_id']}")
        runtime_user_id = details["created_by_end_user"]["id"]
        return {**result["data"]["outputs"], "runtime_user_id": runtime_user_id}

    async def bind(subject):
        async with PowerContextClient("http://127.0.0.1:31800", token=console.secrets["PC_TOKEN"]) as pc:
            scope = await pc.create_scope(
                CreateScopeRequest(
                    title="Dify Service API user", summary="Live user isolation", idempotency_key=f"dify-user:{subject}"
                )
            )
            identity = MemoryIdentity(app_id=workflow.app_id, subject_id=subject)
            await pc.set_scope_binding(
                SetScopeBindingRequest(key=binding_key("pc-dify-local", identity), scope_id=scope.scope_id)
            )

    marker = "userfact" + uuid4().hex[:10]
    try:
        publish("prepare_context", {"query": marker})
        first = invoke("pc-acceptance-alice")
        second = invoke("pc-acceptance-bob")
        assert first["user_id"] != second["user_id"]
        assert first["runtime_user_id"] != second["runtime_user_id"]
        for result in (first, second):
            asyncio.run(bind(result["runtime_user_id"]))
        publish("remember_memory", {"kind": "user-acceptance", "text": f"My project code is {marker}."})
        written = invoke("pc-acceptance-alice")
        assert written["result"][0].get("status") != "error", written
        publish("prepare_context", {"query": marker, "max_bytes": 2000})
        recalled = invoke("pc-acceptance-alice")["result"][0]
        isolated = invoke("pc-acceptance-bob")["result"][0]
        assert recalled["status"] == "ready" and marker in recalled["content"], recalled
        assert isolated["status"] == "empty", isolated
        print(
            "PASS: published local Service API preserves trusted app/user identity; Alice recalls her fact, Bob sees empty memory."
        )
    finally:
        (STATE / "user-events.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n")
        client.close()


if __name__ == "__main__":
    main()
