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
            requests = (await call("GET", base + "/documents"))["requests"]
            assert next(item for item in requests if item["request_id"] == first["request_id"])["state"] == "completed"
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
            requests = (await call("GET", base + "/documents"))["requests"]
            listed = next(item for item in requests if item["request_id"] == repaired["request_id"])
            assert listed["state"] == outcome["state"] and listed["error"] == outcome["error"]
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


def test_batch_invalid_paths_preserve_receipts_and_do_not_create_documents(tmp_path):
    plugins = tmp_path / "plugins"
    shutil.copytree(PLUGIN, plugins / "knowledge-base")
    entry = plugins / "knowledge-base" / "plugin.py"
    entry.write_text(entry.read_text() + FIXTURE_EMBEDDING)
    for name in ("a", "c"):
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
                json={"title": "Invalid paths", "space_scope_id": space["space_scope_id"], "idempotency_key": "kb"},
            )
            base = "/scopes/" + kb["scope_id"]
            invalid_paths = ["\x00.md", "directory\x00/file.md"]
            body = {
                "root": str(tmp_path),
                "paths": ["a.md", *invalid_paths, "c.md"],
                "idempotency_key": "batch",
            }
            result = (await call("POST", base + "/imports", json=body))["items"]
            assert [item["path"] for item in result] == body["paths"]
            assert [item["accepted"] for item in result] == [True, False, False, True]
            assert all(item.get("error") and "request_id" not in item for item in result[1:3])
            for item in (result[0], result[-1]):
                outcome = await wait_for(
                    lambda item=item: call("GET", base + "/imports/" + item["request_id"]),
                    lambda value: value["state"] in {"completed", "failed"},
                    log=tmp_path / "server.log",
                )
                assert outcome["state"] == "completed" and outcome["current_revision"] == 1
            assert (await call("POST", base + "/imports", json=body))["items"] == result

            # Both entry points reject invalid paths before accepting a processing request.
            for path in invalid_paths:
                rejected = await client.post(
                    P + base + "/uploads",
                    params={"relative_path": path, "idempotency_key": "invalid-upload"},
                    content=b"Invalid filename",
                )
                assert rejected.status_code == 422, rejected.text
            invalid_root = await call(
                "POST", base + "/imports", json={**body, "root": str(tmp_path) + "\x00", "idempotency_key": "bad-root"}
            )
            assert [item["path"] for item in invalid_root["items"]] == body["paths"]
            assert all(not item["accepted"] and "request_id" not in item for item in invalid_root["items"])
            listing = await call("GET", base + "/documents")
            assert len(listing["items"]) == 2
            assert {doc["content"]["relative_path"] for doc in listing["items"]} == {"a.md", "c.md"}
            assert all(doc["ref"]["revision"] == 1 for doc in listing["items"])
            assert {request["request_id"] for request in listing["requests"]} == {
                result[0]["request_id"],
                result[-1]["request_id"],
            }

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


def test_reprocess_cannot_replace_an_upload_published_while_reading_its_snapshot(tmp_path):
    plugins = tmp_path / "plugins"
    shutil.copytree(PLUGIN, plugins / "knowledge-base")
    entry = plugins / "knowledge-base" / "plugin.py"
    entry.write_text(entry.read_text() + FIXTURE_EMBEDDING + GATED_SNAPSHOT_READ)

    async def scenario():
        async with running_server(
            tmp_path,
            plugins,
            extra_env={
                "POWERCONTEXT_KB_DATA_DIR": str(tmp_path / "data"),
                "KNOWLEDGE_BASE_TEST_GATE": str(tmp_path),
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
                json={"title": "Concurrent upload", "space_scope_id": space["space_scope_id"], "idempotency_key": "kb"},
            )
            base = "/scopes/" + kb["scope_id"]

            async def upload(key, text):
                return await call(
                    "POST",
                    base + "/uploads",
                    params={"relative_path": "one.md", "idempotency_key": key},
                    content=text.encode(),
                )

            async def settled(receipt):
                return await wait_for(
                    lambda: call("GET", base + "/imports/" + receipt["request_id"]),
                    lambda result: result["state"] in {"completed", "failed"},
                    log=tmp_path / "server.log",
                )

            first = await upload("first", "# Old\n\nOld original content.")
            assert (await settled(first))["current_revision"] == 1
            path = base + "/documents/" + first["document_id"] + "/reprocess"
            body = {"idempotency_key": "racing-reprocess"}
            (tmp_path / "pause-read").touch()
            pending = asyncio.create_task(call("POST", path, json=body))
            try:
                await wait_for(
                    lambda: asyncio.sleep(0, (tmp_path / "waiting").exists()), bool, log=tmp_path / "server.log"
                )
                latest_text = "# Latest\n\nNew authoritative content."
                latest = await upload("latest", latest_text)
                assert (await settled(latest))["current_revision"] == 2
            finally:
                (tmp_path / "release").touch()
            receipt = await pending
            outcome = await settled(receipt)
            assert outcome["state"] == "failed", outcome
            assert "Document changed" in outcome["error"]
            assert outcome["current_revision"] == 2
            assert receipt["source"] == first["source"]
            assert await call("POST", path, json=body) == receipt
            assert (await settled(receipt))["state"] == "failed"

            # A fresh command uses the new original; the failed command never rebases on retry.
            fresh = await call("POST", path, json={"idempotency_key": "fresh-reprocess"})
            outcome = await settled(fresh)
            assert outcome["state"] == "completed" and outcome["current_revision"] == 3
            assert fresh["source"] == latest["source"]
            artifact = f"/v1/scopes/{kb['scope_id']}/artifacts/document/{first['document_id']}"
            head = await client.get(artifact)
            assert head.is_success, head.text
            assert head.json()["revision"] == 3
            for revision in (2, 3):
                chunks = base + f"/documents/{first['document_id']}/revisions/{revision}/chunks"
                listing = await call("GET", chunks)
                assert listing["total"] == 1
                chunk = await call("GET", chunks + "/" + listing["items"][0]["chunk_id"])
                assert chunk["text"] == latest_text

    asyncio.run(scenario())


GATED_SNAPSHOT_READ = """
import os, time
from pathlib import Path
from pc_knowledge_base.content import Snapshots
_original_read = Snapshots.read
def _gated_read(self, key, digest):
    gate = Path(os.environ['KNOWLEDGE_BASE_TEST_GATE'])
    if (gate / 'pause-read').exists():
        (gate / 'pause-read').unlink()
        (gate / 'waiting').touch()
        deadline = time.monotonic() + 60
        while not (gate / 'release').exists():
            if time.monotonic() > deadline:
                raise RuntimeError('snapshot read gate timed out')
            time.sleep(0.02)
    return _original_read(self, key, digest)
Snapshots.read = _gated_read
"""
