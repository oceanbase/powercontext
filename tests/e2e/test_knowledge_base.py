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

"""Knowledge-base workflows through a real Server, Worker and local embedding model."""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from tests.e2e.plugin_server import ADMIN_AUTHORIZATION_VALUE, running_server, wait_for

P = "/v1/plugins/knowledge-base/api"
PLUGIN = Path(__file__).resolve().parents[2] / "plugins" / "knowledge-base"


@pytest.mark.parametrize("real_model", [False, True], ids=["fixture-embedding", "real-embedding"])
def test_knowledge_base_import_edit_forget_and_space_selection(tmp_path, real_model):
    if real_model and not os.environ.get("POWERCONTEXT_TEST_KB_MODELS"):
        pytest.skip("requires downloaded local embedding model")
    if real_model:
        pytest.importorskip("fastembed")
    pytest.importorskip("opendal")
    directory = tmp_path / "plugins"
    shutil.copytree(PLUGIN, directory / "knowledge-base")
    if not real_model:
        entry = directory / "knowledge-base" / "plugin.py"
        entry.write_text(entry.read_text() + FIXTURE_MODEL)

    async def scenario():
        async with running_server(
            tmp_path,
            plugins=directory,
            extra_env={
                "POWERCONTEXT_KB_DATA_DIR": os.environ.get("POWERCONTEXT_TEST_KB_MODELS", str(tmp_path / "data")),
                "POWERCONTEXT_KB_IMPORT_ROOTS": str(tmp_path),
                "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "true",
                **(
                    {"POWERCONTEXT_SERVER_DATABASE": os.environ["POWERCONTEXT_TEST_KB_SERVER_DATABASE"]}
                    if os.environ.get("POWERCONTEXT_TEST_KB_SERVER_DATABASE")
                    else {}
                ),
            },
        ) as client:
            client.timeout = 180

            async def call(method, path, **kwargs):
                response = await client.request(method, P + path, **kwargs)
                assert response.is_success, response.text
                return response.json()

            default = await call("GET", "/me/default-space")
            assert (await call("GET", "/me/default-space"))["space_scope_id"] == default["space_scope_id"]
            selected_space = await call(
                "POST", "/spaces", json={"title": "Acceptance space", "idempotency_key": uuid4().hex}
            )
            default = await call(
                "PUT",
                "/me/default-space",
                json={"space_scope_id": selected_space["space_scope_id"], "version": default["version"]},
            )
            space = default["space_scope_id"]
            body = {"title": "Operations", "space_scope_id": space, "idempotency_key": "kb"}
            if not real_model:
                interrupted = await client.post(P + "/knowledge-bases", json=body)
                assert interrupted.status_code == 500
                assert not (await call("GET", "/knowledge-bases", params={"space_scope_id": space}))["items"]
                other = await call(
                    "POST", "/spaces", json={"title": "Interrupted creation", "idempotency_key": "interruption"}
                )
                default = await call(
                    "PUT",
                    "/me/default-space",
                    json={"space_scope_id": other["space_scope_id"], "version": default["version"]},
                )
            kb = await call("POST", "/knowledge-bases", json=body)
            assert (await call("POST", "/knowledge-bases", json=body))["scope_id"] == kb["scope_id"]
            scope = kb["scope_id"]
            base = f"/scopes/{scope}"
            missing_space = await client.post(P + "/knowledge-bases", json={"title": "x", "idempotency_key": "x"})
            assert missing_space.status_code == 422
            another = await call("POST", "/spaces", json={"title": "Development", "idempotency_key": "space"})
            await call(
                "PUT",
                "/me/default-space",
                json={"space_scope_id": another["space_scope_id"], "version": default["version"]},
            )
            assert (await call("POST", "/knowledge-bases", json=body))["scope_id"] == scope
            second = await call("POST", "/knowledge-bases", json={**body, "space_scope_id": another["space_scope_id"]})
            assert second["scope_id"] != scope
            stale = await client.put(
                P + "/me/default-space", json={"space_scope_id": space, "version": default["version"]}
            )
            assert stale.status_code == 409
            uploaded = await call(
                "POST",
                base + "/uploads",
                params={"relative_path": "restart.md", "idempotency_key": "upload"},
                content="# 重启流程\n\n重启 OceanBase 前必须检查备份。\n\n## 验证\n检查服务健康。".encode(),
                headers={"Content-Type": "application/octet-stream"},
            )
            document = uploaded["document_id"]
            artifact = f"/v1/scopes/{scope}/artifacts/document/{document}"

            async def status():
                return await call("GET", base + "/imports/" + uploaded["request_id"])

            completed = await wait_for(
                status, lambda r: r["state"] in {"completed", "failed"}, seconds=180, log=tmp_path / "server.log"
            )
            assert completed["state"] == "completed", completed
            current = (await client.get(artifact)).json()
            assert current["revision"] == 1
            chunks = base + f"/documents/{document}/revisions/1/chunks"
            listing = await call("GET", chunks)
            assert listing["total"] >= 2
            chunk = listing["items"][1]
            assert "text" not in chunk
            content = await call("GET", chunks + "/" + chunk["chunk_id"])
            assert content["text"]
            results = await call(
                "POST", "/retrieve", json={"space_scope_id": space, "query": "重启之前检查什么", "scope_ids": [scope]}
            )
            assert results["hits"]
            assert all(hit["scope_id"] == scope for hit in results["hits"])
            assert (await call("POST", base + "/retrieve", json={"query": "重启"}))["hits"]
            assert not (await call("POST", "/retrieve", json={"query": "重启", "scope_ids": []}))["hits"]
            assert not (await call("POST", "/retrieve", json={"query": "重启"}))["hits"]
            wrong = await client.post(P + "/retrieve", json={"query": "重启", "scope_ids": [scope]})
            assert wrong.status_code == 422
            edited = await client.put(
                artifact,
                json={
                    "content": {
                        "operation": "edit_chunk",
                        "chunk_id": chunk["chunk_id"],
                        "text": "重启前必须获得值班工程师批准。",
                    }
                },
                headers={"If-Match": '"revision:1"'},
            )
            assert edited.status_code == 200, edited.text
            assert edited.json()["revision"] == 2
            revision = (await client.get(artifact + "/revisions/2")).json()
            evidence_ref = next(ref for ref in revision["sources"] if ref["source_type"] == "content")
            evidence = await client.get(f"/v1/scopes/{scope}/sources/content/{evidence_ref['source_id']}")
            assert evidence.status_code == 200, evidence.text
            assert evidence.json()["content"] == {
                "operation": "edit_chunk",
                "chunk_id": chunk["chunk_id"],
                "text": "重启前必须获得值班工程师批准。",
            }
            # The browser bridge forwards the same caller and exact revision precondition.
            async with httpx.AsyncClient(
                base_url=client.base_url, cookies={"powercontext_dashboard_token": ADMIN_AUTHORIZATION_VALUE}
            ) as browser:
                bridge = f"/dashboard/plugins/knowledge-base/api/_artifacts/{scope}/{document}"
                assert (await browser.get(bridge, headers={"Sec-Fetch-Site": "same-origin"})).status_code == 200
                assert (await browser.get(bridge, headers={"Origin": "null"})).status_code == 403
                denied = await browser.put(
                    bridge,
                    json={"content": {"operation": "remove_chunk", "chunk_id": chunk["chunk_id"]}},
                    headers={"Sec-Fetch-Site": "same-origin", "If-Match": '"revision:1"'},
                )
                assert denied.status_code == 412, denied.text
            stale = await client.put(
                artifact,
                json={"content": {"operation": "remove_chunk", "chunk_id": chunk["chunk_id"]}},
                headers={"If-Match": '"revision:1"'},
            )
            assert stale.status_code == 412
            assert (await call("GET", chunks + "/" + chunk["chunk_id"]))["text"] == content["text"]
            hits = (
                await call("POST", "/retrieve", json={"space_scope_id": space, "query": "值班工程师", "mode": "fts"})
            )["hits"]
            assert hits and all(hit["document"]["revision"] == 2 for hit in hits)
            removed = await client.put(
                artifact,
                json={"content": {"operation": "remove_chunk", "chunk_id": chunk["chunk_id"]}},
                headers={"If-Match": '"revision:2"'},
            )
            assert removed.status_code == 200, removed.text
            assert removed.json()["revision"] == 3
            assert not (
                await call("POST", "/retrieve", json={"space_scope_id": space, "query": "值班工程师", "mode": "fts"})
            )["hits"]
            retry = await call(
                "POST", base + f"/documents/{document}/reprocess", json={"idempotency_key": "reprocess-protected"}
            )

            async def protected_status():
                return await call("GET", base + "/imports/" + retry["request_id"])

            outcome = await wait_for(
                protected_status, lambda r: r["state"] in {"completed", "failed"}, log=tmp_path / "server.log"
            )
            assert outcome["state"] == "failed" and outcome["current_revision"] == 3
            retry = await call(
                "POST",
                base + f"/documents/{document}/reprocess",
                json={"idempotency_key": "reprocess-replace", "manual_edits_policy": "replace"},
            )
            outcome = await wait_for(
                protected_status, lambda r: r["state"] in {"completed", "failed"}, log=tmp_path / "server.log"
            )
            assert outcome["state"] == "completed" and outcome["current_revision"] == 4
            old_status = await call("GET", base + "/imports/" + uploaded["request_id"])
            assert old_status["state"] == "completed"
            governance = (await client.get(artifact + "/governance")).json()
            forgotten = await client.post(
                artifact + "/forget", json={"revision": 4, "state_version": governance["state_version"]}
            )
            assert forgotten.status_code == 200, forgotten.text
            assert not (
                await call("POST", "/retrieve", json={"space_scope_id": space, "query": "重启", "mode": "hybrid"})
            )["hits"]
            assert (await call("GET", chunks + "/" + chunk["chunk_id"]))["text"] == content["text"]

            html = await call(
                "POST",
                base + "/uploads",
                params={"relative_path": "table.md", "idempotency_key": "html-table"},
                content=b"<table>\n<tr><td>Emergency shutdown procedure</td></tr>\n</table>\n",
            )

            async def html_status():
                return await call("GET", base + "/imports/" + html["request_id"])

            outcome = await wait_for(
                html_status, lambda result: result["state"] in {"completed", "failed"}, log=tmp_path / "server.log"
            )
            assert outcome["state"] == "completed", outcome
            hits = (await call("POST", base + "/retrieve", json={"query": "Emergency", "mode": "fts"}))["hits"]
            assert hits and "Emergency shutdown procedure" in hits[0]["text"]
            assert hits[0]["document"]["artifact_id"] == html["document_id"]

    asyncio.run(scenario())


# Deterministic embeddings belong only to the test plugin copy, never the installed plugin.
FIXTURE_EMBEDDING = """
import pc_knowledge_base.service as _service
class _FixtureEmbedding:
    dimension = 2
    profile = "fixture-model-v1"
    def __init__(self, model, cache): pass
    async def embed(self, values, query=False):
        return [[1.0, float(len(value)%7+1)/10] for value in values]
_service.Embedding = _FixtureEmbedding
"""

FIXTURE_MODEL = (
    FIXTURE_EMBEDDING
    + """
_original_add_resource = _service.Store.add_resource
_registration_interrupted = False
async def _interrupted_registration(self, scope, kind, key, value, space=None):
    global _registration_interrupted
    if kind == "scope" and not _registration_interrupted:
        _registration_interrupted = True
        raise RuntimeError("injected registration interruption")
    return await _original_add_resource(self,scope,kind,key,value,space)
_service.Store.add_resource = _interrupted_registration
"""
)
