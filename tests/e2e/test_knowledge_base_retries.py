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

"""Accepted commands retain their input and batches preserve per-file receipts."""

import asyncio
import shutil

from tests.e2e.plugin_server import running_server, wait_for
from tests.e2e.test_knowledge_base import FIXTURE_EMBEDDING, PLUGIN, P


def test_reprocess_replay_keeps_the_accepted_snapshot_after_head_changes(tmp_path):
    plugins = tmp_path / "plugins"
    shutil.copytree(PLUGIN, plugins / "knowledge-base")
    entry = plugins / "knowledge-base" / "plugin.py"
    entry.write_text(entry.read_text() + FIXTURE_EMBEDDING + INTERRUPTED_SUBMISSION)

    async def scenario():
        async with running_server(
            tmp_path,
            plugins,
            extra_env={
                "POWERCONTEXT_KB_DATA_DIR": str(tmp_path / "data"),
                "KNOWLEDGE_BASE_TEST_INTERRUPT": str(tmp_path / "interrupt"),
            },
        ) as client:

            async def call(method, path, **kwargs):
                response = await client.request(method, P + path, **kwargs)
                assert response.is_success, response.text
                return response.json()

            space = await call("GET", "/me/default-space")
            kb = await call(
                "POST",
                "/knowledge-bases",
                json={
                    "title": "Replay",
                    "space_scope_id": space["space_scope_id"],
                    "idempotency_key": "kb",
                },
            )
            base = "/scopes/" + kb["scope_id"]

            async def upload(key, text, path="one.md"):
                return await call(
                    "POST",
                    base + "/uploads",
                    params={"relative_path": path, "idempotency_key": key},
                    content=text.encode(),
                )

            async def settled(receipt):
                return await wait_for(
                    lambda: call("GET", base + "/imports/" + receipt["request_id"]),
                    lambda result: result["state"] in {"completed", "failed"},
                    log=tmp_path / "server.log",
                )

            first = await upload("first", "# First\n\nOriginal content.")
            assert (await settled(first))["current_revision"] == 1
            path = base + "/documents/" + first["document_id"] + "/reprocess"
            body = {"idempotency_key": "reprocess"}
            receipt = await call("POST", path, json=body)
            assert (await settled(receipt))["current_revision"] == 2
            changed = await upload("changed", "# Second\n\nChanged original content.")
            assert (await settled(changed))["current_revision"] == 3
            settings = await call(
                "PATCH",
                base + "/settings",
                json={"version": kb["version"], "chunking": {"max_chars": 80, "overlap": 10}},
            )
            assert await call("POST", path, json=body) == receipt
            outcome = await settled(receipt)
            assert outcome["state"] == "completed" and outcome["current_revision"] == 3
            other = await upload("other", "Another document", "other.md")
            assert (await settled(other))["state"] == "completed"
            for target, parameters in (
                (path, {**body, "manual_edits_policy": "replace"}),
                (base + "/documents/" + other["document_id"] + "/reprocess", body),
                (base + "/imports/" + first["request_id"] + "/retry", body),
            ):
                conflict = await client.post(P + target, json=parameters)
                assert conflict.status_code == 409, conflict.text

            # Registration commits but Source submission is interrupted, as if the request connection was lost.
            (tmp_path / "interrupt").touch()
            interrupted_body = {"idempotency_key": "interrupted"}
            interrupted = await client.post(P + path, json=interrupted_body)
            assert interrupted.status_code == 500
            latest = await upload("latest", "# Third\n\nLatest original content.")
            assert (await settled(latest))["current_revision"] == 4
            await call(
                "PATCH",
                base + "/settings",
                json={"version": settings["version"], "chunking": {"max_chars": 160, "overlap": 20}},
            )
            repaired = await call("POST", path, json=interrupted_body)
            assert repaired["source"] == changed["source"]
            assert repaired["source"] != latest["source"]
            outcome = await settled(repaired)
            assert outcome["state"] == "failed" and outcome["current_revision"] == 4
            assert "Document changed" in outcome["error"]
            assert await call("POST", path, json=interrupted_body) == repaired
            governance = (
                await client.get(f"/v1/scopes/{kb['scope_id']}/artifacts/document/{first['document_id']}/governance")
            ).json()
            forgotten = await client.post(
                f"/v1/scopes/{kb['scope_id']}/artifacts/document/{first['document_id']}/forget",
                json={"revision": 4, "state_version": governance["state_version"]},
            )
            assert forgotten.is_success, forgotten.text
            assert await call("POST", path, json=body) == receipt
            outcome = await settled(receipt)
            assert outcome["current_revision"] == 4 and outcome["lifecycle_state"] == "deprecated"

    asyncio.run(scenario())


def test_batch_conflicts_preserve_receipts_and_continue_with_later_files(tmp_path):
    plugins = tmp_path / "plugins"
    shutil.copytree(PLUGIN, plugins / "knowledge-base")
    entry = plugins / "knowledge-base" / "plugin.py"
    entry.write_text(entry.read_text() + FIXTURE_EMBEDDING)
    for name in ("a", "b", "c"):
        (tmp_path / f"{name}.md").write_text(f"# {name}\n\nOriginal {name} content.")

    async def scenario():
        async with running_server(
            tmp_path,
            plugins,
            extra_env={
                "POWERCONTEXT_KB_DATA_DIR": str(tmp_path / "data"),
                "POWERCONTEXT_KB_IMPORT_ROOTS": str(tmp_path),
            },
        ) as client:

            async def call(method, path, **kwargs):
                response = await client.request(method, P + path, **kwargs)
                assert response.is_success, response.text
                return response.json()

            space = await call("GET", "/me/default-space")
            kb = await call(
                "POST",
                "/knowledge-bases",
                json={
                    "title": "Batch",
                    "space_scope_id": space["space_scope_id"],
                    "idempotency_key": "kb",
                },
            )
            base = "/scopes/" + kb["scope_id"]
            body = {"root": str(tmp_path), "paths": ["b.md"], "idempotency_key": "batch"}
            previous = (await call("POST", base + "/imports", json=body))["items"][0]

            async def settled(receipt):
                return await wait_for(
                    lambda: call("GET", base + "/imports/" + receipt["request_id"]),
                    lambda result: result["state"] in {"completed", "failed"},
                    log=tmp_path / "server.log",
                )

            assert (await settled(previous))["state"] == "completed"
            (tmp_path / "b.md").write_text("# b\n\nChanged during retry.")
            body["paths"] = ["a.md", "b.md", "c.md"]
            result = (await call("POST", base + "/imports", json=body))["items"]
            assert [item["path"] for item in result] == body["paths"]
            assert [item["accepted"] for item in result] == [True, False, True]
            assert result[1]["status"] == 409
            assert "different file bytes" in result[1]["error"]
            for item in (result[0], result[2]):
                assert (await settled(item))["state"] == "completed"
            assert (await call("POST", base + "/imports", json=body))["items"] == result
            documents = (await call("GET", base + "/documents"))["items"]
            assert {doc["content"]["relative_path"] for doc in documents} == {"a.md", "b.md", "c.md"}
            assert all(doc["ref"]["revision"] == 1 for doc in documents)
            b = await call("POST", base + "/retrieve", json={"query": "Original", "mode": "fts"})
            assert any(hit["document"]["artifact_id"] == previous["document_id"] for hit in b["hits"])

    asyncio.run(scenario())


INTERRUPTED_SUBMISSION = """
import os
from pathlib import Path
from pc_knowledge_base.sources import Definition
_original_resolve = Definition.resolve
async def _interrupted_resolve(self, value, /):
    gate = Path(os.environ['KNOWLEDGE_BASE_TEST_INTERRUPT'])
    if self.name == 'knowledge-base-file' and gate.exists():
        gate.unlink()
        raise RuntimeError('injected interruption before Source submission')
    return await _original_resolve(self, value)
Definition.resolve = _interrupted_resolve
"""
