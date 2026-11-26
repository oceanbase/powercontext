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

"""Document search preserves matching chunks when the framework assembles context."""

import asyncio
import shutil

from tests.e2e.plugin_server import running_server, wait_for
from tests.e2e.test_knowledge_base import FIXTURE_EMBEDDING, PLUGIN, P


def test_document_context_keeps_all_matched_chunks_and_current_versions(tmp_path):
    plugins = tmp_path / "plugins"
    shutil.copytree(PLUGIN, plugins / "knowledge-base")
    entry = plugins / "knowledge-base" / "plugin.py"
    entry.write_text(entry.read_text() + FIXTURE_EMBEDDING)

    async def scenario():
        async with running_server(
            tmp_path, plugins, extra_env={"POWERCONTEXT_KB_DATA_DIR": str(tmp_path / "data")}
        ) as client:

            async def call(method, path, **kwargs):
                response = await client.request(method, path, **kwargs)
                assert response.is_success, response.text
                return response.json()

            space = await call("GET", P + "/me/default-space")
            kb = await call(
                "POST",
                P + "/knowledge-bases",
                json={"title": "Context", "space_scope_id": space["space_scope_id"], "idempotency_key": "kb"},
            )
            scope = kb["scope_id"]
            base = P + "/scopes/" + scope

            async def upload(path, text):
                receipt = await call(
                    "POST",
                    base + "/uploads",
                    params={"relative_path": path, "idempotency_key": path},
                    content=text.encode(),
                )
                status = await wait_for(
                    lambda: call("GET", base + "/imports/" + receipt["request_id"]),
                    lambda r: r["state"] in {"completed", "failed"},
                    log=tmp_path / "server.log",
                )
                assert status["state"] == "completed", status
                return receipt

            async def context(limit):
                return await call(
                    "POST",
                    "/v1/context/prepare",
                    json={
                        "scope_id": scope,
                        "query": "Restart",
                        "max_bytes": 16000,
                        "include_receipt": True,
                        "assembly": {"sections": [{"family": "document", "limit": limit, "search": {"mode": "fts"}}]},
                    },
                )

            first = await upload(
                "runbook.md",
                "## Restart prerequisites\n\n> ### Example\n>\n> Quoted guidance only.\n\n"
                "Restart requires BACKUP_APPROVED. See [guide][g].\n\n"
                "## Restart execution\n\nRestart requires SERVICE_DRAINED.\n\n[g]: https://example.com/guide",
            )
            hits = (await call("POST", base + "/retrieve", json={"query": "Restart", "mode": "fts"}))["hits"]
            assert len(hits) == 2
            assert any("https://example.com/guide" in hit["text"] for hit in hits)
            for hit in hits:
                expected_heading = "Restart prerequisites" if "BACKUP_APPROVED" in hit["text"] else "Restart execution"
                assert hit["heading_path"] == [expected_heading]
            chunks = base + f"/documents/{first['document_id']}/revisions/1/chunks"
            assert [item["heading_path"] for item in (await call("GET", chunks))["items"]] == [
                ["Restart prerequisites"],
                ["Restart execution"],
            ]
            for limit in (8, 1):
                prepared = await context(limit)
                assert "BACKUP_APPROVED" in prepared["content"]
                assert "https://example.com/guide" in prepared["content"]
                assert "SERVICE_DRAINED" in prepared["content"]
                for hit in hits:
                    assert hit["chunk_id"] in prepared["content"]
                assert '"line_start"' in prepared["content"] and '"line_end"' in prepared["content"]
                assert '"block_char_start"' in prepared["content"] and '"block_char_end"' in prepared["content"]
                assert '"span_unit"' not in prepared["content"]
                assert len(prepared["receipt"]["selected"]) == 1
                assert not any(item["reason"] == "duplicate" for item in prepared["receipt"]["omitted"])

            second = await upload("verify.md", "# Restart verification\n\nRestart requires HEALTH_VERIFIED.")
            generic = await call(
                "POST",
                f"/v1/scopes/{scope}/artifacts/document/search",
                json={"query": "Restart", "mode": "fts", "limit": 2},
            )
            assert len(generic["results"]) == 2
            assert {item["artifact_id"] for item in generic["results"]} == {
                first["document_id"],
                second["document_id"],
            }
            prepared = await context(2)
            assert all(
                marker in prepared["content"] for marker in ("BACKUP_APPROVED", "SERVICE_DRAINED", "HEALTH_VERIFIED")
            )
            assert len(prepared["receipt"]["selected"]) == 2

            artifact = f"/v1/scopes/{scope}/artifacts/document/{first['document_id']}"
            invalid_reference = await call(
                "POST",
                base + "/uploads",
                params={"relative_path": "runbook.md", "idempotency_key": "oversized-reference"},
                content=("[guide][g]\n\n[g]: https://example.com/" + "x" * 400).encode(),
            )
            failed = await wait_for(
                lambda: call("GET", base + "/imports/" + invalid_reference["request_id"]),
                lambda status: status["state"] in {"completed", "failed"},
                log=tmp_path / "server.log",
            )
            assert failed["state"] == "failed" and "increase max_chars" in failed["error"]
            assert (await call("GET", artifact))["revision"] == 1
            assert "BACKUP_APPROVED" in (await context(2))["content"]

            removed = next(hit for hit in hits if "BACKUP_APPROVED" in hit["text"])
            await call(
                "PUT",
                artifact,
                headers={"If-Match": '"revision:1"'},
                json={"content": {"operation": "remove_chunk", "chunk_id": removed["chunk_id"]}},
            )
            prepared = await context(2)
            assert "BACKUP_APPROVED" not in prepared["content"]
            assert "SERVICE_DRAINED" in prepared["content"]
            assert "HEALTH_VERIFIED" in prepared["content"]
            revisions = {
                item["artifact_address"]["artifact"]["artifact_id"]: item["artifact_address"]["artifact"]["revision"]
                for item in prepared["receipt"]["selected"]
            }
            assert revisions == {first["document_id"]: 2, second["document_id"]: 1}
            governance = await call("GET", artifact + "/governance")
            await call("POST", artifact + "/forget", json={"revision": 2, "state_version": governance["state_version"]})
            prepared = await context(2)
            assert "SERVICE_DRAINED" not in prepared["content"]
            assert "HEALTH_VERIFIED" in prepared["content"]
            assert len(prepared["receipt"]["selected"]) == 1

    asyncio.run(scenario())
