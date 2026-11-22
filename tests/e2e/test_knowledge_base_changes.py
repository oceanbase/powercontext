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

"""Replacement authority and lifecycle races through the real Server and Worker."""

import asyncio
import hashlib
import json
import os
import shutil
from uuid import uuid4

import httpx

from tests.e2e.plugin_server import ADMIN_AUTHORIZATION_VALUE, running_server, wait_for
from tests.e2e.test_knowledge_base import FIXTURE_EMBEDDING, PLUGIN, P


def test_replacements_require_document_authority_and_edits_cannot_follow_forget(tmp_path):
    plugins = tmp_path / "plugins"
    shutil.copytree(PLUGIN, plugins / "knowledge-base")
    entry = plugins / "knowledge-base" / "plugin.py"
    entry.write_text(entry.read_text() + FIXTURE_EMBEDDING + GATED_EMBEDDING)
    identities = []
    for name, kind, token in (
        ("admin", "user", ADMIN_AUTHORIZATION_VALUE),
        ("contributor", "user", "contributor-token"),
        ("worker", "service", "worker-token"),
    ):
        identities.append({
            "credential_id": name,
            "token_digest": hashlib.sha256(token.encode()).hexdigest(),
            "subject": {"type": kind, "id": name},
        })
    identity_file = tmp_path / "identities.json"
    identity_file.write_text(json.dumps({"identities": identities}))
    identity_file.chmod(0o600)

    async def scenario():
        async with running_server(
            tmp_path,
            plugins,
            extra_env={
                "POWERCONTEXT_KB_DATA_DIR": str(tmp_path / "data"),
                "POWERCONTEXT_KB_IMPORT_ROOTS": str(tmp_path),
                "POWERCONTEXT_SERVER_AUTH_IDENTITIES_FILE": str(identity_file),
                "POWERCONTEXT_SERVER_AUTH_TOKEN": None,
                "POWERCONTEXT_SERVER_AUTH_ADMINISTRATORS": '["user:admin"]',
                "POWERCONTEXT_SERVER_ACCESS_BACKGROUND_PRINCIPAL_ID": "worker",
                "KNOWLEDGE_BASE_TEST_GATE": str(tmp_path),
                **(
                    {"POWERCONTEXT_SERVER_DATABASE": os.environ["POWERCONTEXT_TEST_KB_SERVER_DATABASE"]}
                    if os.environ.get("POWERCONTEXT_TEST_KB_SERVER_DATABASE")
                    else {}
                ),
            },
        ) as admin:

            async def call(client, method, path, **kwargs):
                response = await client.request(method, path, **kwargs)
                assert response.is_success, response.text
                return response.json()

            default = await call(admin, "GET", P + "/me/default-space")
            kb = await call(
                admin,
                "POST",
                P + "/knowledge-bases",
                json={
                    "title": "Permissions",
                    "space_scope_id": default["space_scope_id"],
                    "idempotency_key": uuid4().hex,
                },
            )
            scope = kb["scope_id"]
            base = P + f"/scopes/{scope}"
            for name, kind in (("admin", "user"), ("contributor", "user"), ("worker", "service")):
                await call(
                    admin,
                    "POST",
                    "/v1/access/bindings/create",
                    json={
                        "subject": {"type": kind, "id": name},
                        "resource": {"type": "scope", "scope_id": scope},
                        "role": "scope.contributor",
                        "idempotency_key": f"{scope}:{name}",
                    },
                )

            async def settled(receipt):
                return await wait_for(
                    lambda: call(admin, "GET", base + "/imports/" + receipt["request_id"]),
                    lambda result: result["state"] in {"completed", "failed"},
                    log=tmp_path / "server.log",
                )

            async with (
                httpx.AsyncClient(
                    base_url=admin.base_url, headers={"Authorization": "Bearer contributor-token"}
                ) as other,
                httpx.AsyncClient(base_url=admin.base_url, headers={"Authorization": "Bearer worker-token"}) as owner,
            ):
                # A contributor may import a new document, including one that fails deterministic parsing.
                failed = await call(
                    other,
                    "POST",
                    base + "/uploads",
                    params={"relative_path": "policy.md", "idempotency_key": "invalid"},
                    content=b"\xff",
                )
                assert (await settled(failed))["state"] == "failed"
                params = {"relative_path": "policy.md", "idempotency_key": "initial"}
                text = b"# Policy\n\nRequire approval before deployment."
                receipt = await call(admin, "POST", base + "/uploads", params=params, content=text)
                assert (await settled(receipt))["state"] == "completed"
                document = receipt["document_id"]
                artifact = f"/v1/scopes/{scope}/artifacts/document/{document}"
                chunks = base + f"/documents/{document}/revisions"
                chunk = (await call(admin, "GET", chunks + "/1/chunks"))["items"][0]["chunk_id"]
                command = {"content": {"operation": "edit_chunk", "chunk_id": chunk, "text": "Human correction"}}
                denied = await other.put(artifact, json=command, headers={"If-Match": '"revision:1"'})
                assert denied.status_code == 403
                await call(owner, "PUT", artifact, json=command, headers={"If-Match": '"revision:1"'})
                before = await call(admin, "GET", base + "/documents")
                for path in (
                    base + f"/documents/{document}/reprocess",
                    base + f"/imports/{failed['request_id']}/retry",
                ):
                    response = await other.post(
                        path, json={"idempotency_key": "replace", "manual_edits_policy": "replace"}
                    )
                    assert response.status_code == 403, response.text
                response = await other.post(
                    base + "/uploads",
                    params={"relative_path": "policy.md", "idempotency_key": "changed"},
                    content=b"Deploy immediately.",
                )
                assert response.status_code == 403, response.text
                assert (await call(admin, "GET", artifact))["revision"] == 2
                assert (await call(admin, "GET", base + "/documents"))["requests"] == before["requests"]
                for name in ("before", "protected", "after"):
                    (tmp_path / f"{name}.md").write_text(f"# {name}\n\nDirectory import content.")
                protected = await call(
                    admin,
                    "POST",
                    base + "/imports",
                    json={
                        "root": str(tmp_path),
                        "paths": ["protected.md"],
                        "idempotency_key": "protected",
                    },
                )
                assert (await settled(protected["items"][0]))["state"] == "completed"
                (tmp_path / "protected.md").write_text("Unauthorized replacement")
                batch = await call(
                    other,
                    "POST",
                    base + "/imports",
                    json={
                        "root": str(tmp_path),
                        "paths": ["before.md", "protected.md", "after.md"],
                        "idempotency_key": "batch",
                    },
                )
                assert [item["accepted"] for item in batch["items"]] == [True, False, True]
                assert batch["items"][1]["status"] == 403
                for item in (batch["items"][0], batch["items"][2]):
                    assert (await settled(item))["state"] == "completed"
                # Replaying accepted work remains valid even when the submitter does not own its generated Artifact.
                replay = await call(admin, "POST", base + "/uploads", params=params, content=text)
                assert replay == receipt
                # An authorized owner can replace manual edits through the same processing route.
                reparsed = await call(
                    owner,
                    "POST",
                    base + f"/documents/{document}/reprocess",
                    json={"idempotency_key": "authorized", "manual_edits_policy": "replace"},
                )
                assert (await settled(reparsed))["current_revision"] == 3
                chunk = (await call(admin, "GET", chunks + "/3/chunks"))["items"][0]["chunk_id"]
                edit = asyncio.create_task(
                    owner.put(
                        artifact,
                        json={"content": {"operation": "edit_chunk", "chunk_id": chunk, "text": "GATED_EDIT"}},
                        headers={"If-Match": '"revision:3"'},
                    )
                )
                try:
                    await wait_for(
                        lambda: asyncio.sleep(0, (tmp_path / "waiting").exists()), bool, log=tmp_path / "server.log"
                    )
                    governance = await call(owner, "GET", artifact + "/governance")
                    await call(
                        owner,
                        "POST",
                        artifact + "/forget",
                        json={"revision": 3, "state_version": governance["state_version"]},
                    )
                finally:
                    (tmp_path / "release").touch()
                response = await edit
                assert response.status_code == 409, response.text
                assert (await call(admin, "GET", artifact))["revision"] == 3
                assert (await call(owner, "GET", artifact + "/governance"))["lifecycle_state"] == "deprecated"
                history = await call(admin, "GET", chunks + f"/3/chunks/{chunk}")
                assert "GATED_EDIT" not in history["text"]
                hits = await call(admin, "POST", base + "/retrieve", json={"query": "approval"})
                assert all(hit["document"]["artifact_id"] != document for hit in hits["hits"])

    asyncio.run(scenario())


GATED_EMBEDDING = """
import asyncio, os
from pathlib import Path
_original_embed = _FixtureEmbedding.embed
async def _gated_embed(self, values, query=False):
    if 'GATED_EDIT' in values:
        gate = Path(os.environ['KNOWLEDGE_BASE_TEST_GATE'])
        (gate / 'waiting').touch()
        while not (gate / 'release').exists():
            await asyncio.sleep(0.02)
    return await _original_embed(self, values, query)
_FixtureEmbedding.embed = _gated_embed
"""
