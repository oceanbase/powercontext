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

"""A revoked default must not prevent an explicit authorized space selection."""

import asyncio
import hashlib
import json
import shutil

import httpx

from tests.e2e.plugin_server import ADMIN_AUTHORIZATION_VALUE, running_server
from tests.e2e.test_knowledge_base import FIXTURE_EMBEDDING, PLUGIN, P


def test_revoked_default_can_be_read_and_replaced_without_implicit_fallback(tmp_path):
    plugins = tmp_path / "plugins"
    shutil.copytree(PLUGIN, plugins / "knowledge-base")
    entry = plugins / "knowledge-base" / "plugin.py"
    entry.write_text(entry.read_text() + FIXTURE_EMBEDDING)
    identities = tmp_path / "identities.json"
    identities.write_text(
        json.dumps({
            "identities": [
                {
                    "credential_id": name,
                    "token_digest": hashlib.sha256(token.encode()).hexdigest(),
                    "subject": {"type": kind, "id": name},
                }
                for name, kind, token in [
                    ("admin", "user", ADMIN_AUTHORIZATION_VALUE),
                    ("reader", "user", "reader-token"),
                    ("worker", "service", "worker-token"),
                ]
            ]
        })
    )
    identities.chmod(0o600)

    async def scenario():
        async with (
            running_server(
                tmp_path,
                plugins,
                extra_env={
                    "POWERCONTEXT_KB_DATA_DIR": str(tmp_path / "data"),
                    "POWERCONTEXT_SERVER_AUTH_IDENTITIES_FILE": str(identities),
                    "POWERCONTEXT_SERVER_AUTH_TOKEN": None,
                    "POWERCONTEXT_SERVER_AUTH_ADMINISTRATORS": '["user:admin"]',
                    "POWERCONTEXT_SERVER_ACCESS_BACKGROUND_PRINCIPAL_ID": "worker",
                },
            ) as admin,
            httpx.AsyncClient(base_url=admin.base_url, headers={"Authorization": "Bearer reader-token"}) as reader,
        ):

            async def call(client, method, path, **kwargs):
                response = await client.request(method, path, **kwargs)
                assert response.is_success, response.text
                return response.json()

            spaces, grants = [], []
            for title in ("A", "B"):
                space = await call(admin, "POST", P + "/spaces", json={"title": title, "idempotency_key": title})
                spaces.append(space["space_scope_id"])
                kb = await call(
                    admin,
                    "POST",
                    P + "/knowledge-bases",
                    json={"title": title, "space_scope_id": spaces[-1], "idempotency_key": title},
                )
                grants.append(
                    await call(
                        admin,
                        "POST",
                        "/v1/access/bindings/create",
                        json={
                            "subject": {"type": "user", "id": "reader"},
                            "resource": {"type": "scope", "scope_id": kb["scope_id"]},
                            "role": "scope.viewer",
                            "idempotency_key": title,
                        },
                    )
                )
            initial = await call(reader, "GET", P + "/me/default-space")
            selected = await call(
                reader,
                "PUT",
                P + "/me/default-space",
                json={"space_scope_id": spaces[0], "version": initial["version"]},
            )
            await call(
                admin,
                "POST",
                "/v1/access/bindings/revoke",
                json={
                    "binding_id": grants[0]["binding_id"],
                    "expected_version": grants[0]["version"],
                    "idempotency_key": "revoke-A",
                },
            )
            unavailable = await call(reader, "GET", P + "/me/default-space")
            assert unavailable["space_scope_id"] == spaces[0] and unavailable["version"] == selected["version"]
            assert unavailable["available"] is False
            visible = await call(reader, "GET", P + "/spaces")
            assert {s["space_scope_id"] for s in visible["items"]} == {initial["space_scope_id"], spaces[1]}
            for method, path, body in [("GET", "/knowledge-bases", None), ("POST", "/retrieve", {"query": "anything"})]:
                denied = await reader.request(method, P + path, json=body)
                assert denied.status_code == 404
            assert (await call(reader, "GET", P + "/knowledge-bases", params={"space_scope_id": spaces[1]}))["items"]
            await call(
                reader,
                "PUT",
                P + "/me/default-space",
                json={"space_scope_id": spaces[1], "version": unavailable["version"]},
            )
            restored = await call(reader, "GET", P + "/me/default-space")
            assert restored["available"] is True and restored["space_scope_id"] == spaces[1]
            assert (await call(reader, "GET", P + "/knowledge-bases"))["items"]

    asyncio.run(scenario())
