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

"""Plugin families follow the shared Artifact access policy on every read and write path."""

import asyncio
import hashlib
import json

import httpx

from tests.e2e.plugin_server import ADMIN_AUTHORIZATION_VALUE, create_scope, running_server

PLUGIN = """
from powercontext.plugins import ApiRequest, ArtifactPlugin, PluginApi, Rendering, Route, SearchHit, SearchResult


class Notes:
    def __init__(self, ctx):
        self.ctx = ctx

    async def render_artifact(self, request):
        return Rendering(text=request.artifact.content["text"])

    async def search_artifacts(self, request):
        page = await self.ctx.reads(request.scope_id).list(limit=50)
        return SearchResult(hits=[SearchHit(ref=item.ref, text=item.content["text"], score=1.0) for item in page.items])

    async def create_artifact(self, request, writer):
        async with writer.transaction() as tx:
            await tx.put(request.artifact_id, content=request.content)

    async def tag(self, request: ApiRequest):
        writer = self.ctx.writer(request.scope_id)
        async with writer.transaction() as tx:
            await tx.set_tags(request.path_params["artifact_id"], ["pinned"])

    async def retire(self, request: ApiRequest):
        writer = self.ctx.writer(request.scope_id)
        async with writer.transaction() as tx:
            await tx.set_lifecycle(request.path_params["artifact_id"], "retired")

    async def bind(self, request: ApiRequest):
        writer = self.ctx.writer(request.scope_id)
        current = await writer.get(request.path_params["artifact_id"])
        async with writer.transaction() as tx:
            await tx.bind_handle("latest-" + current.ref.artifact_id, current.ref)


async def setup(ctx):
    return Notes(ctx)


def _route(name):
    return Route(
        method="POST",
        path="/scopes/{scope_id}/notes/{artifact_id}/" + name,
        handler=name,
        access="scope.contribute",
        scope_param="scope_id",
    )


plugin = ArtifactPlugin(
    family="notes",
    setup=setup,
    api=PluginApi(routes=(_route("tag"), _route("retire"), _route("bind"))),
)
"""

USERS = ("alice", "bob", "carol", "dave")


def _identities(tmp_path):
    def entry(name, token):
        digest = hashlib.sha256(token.encode()).hexdigest()
        return {"credential_id": name, "token_digest": digest, "subject": {"type": "user", "id": name}}

    path = tmp_path / "identities.json"
    entries = [entry("admin", ADMIN_AUTHORIZATION_VALUE), *(entry(name, f"{name}-token") for name in USERS)]
    path.write_text(json.dumps({"identities": entries}), encoding="utf-8")
    path.chmod(0o600)
    return {
        "POWERCONTEXT_SERVER_AUTH_IDENTITIES_FILE": str(path),
        "POWERCONTEXT_SERVER_AUTH_ADMINISTRATORS": json.dumps(["user:admin"]),
        "POWERCONTEXT_SERVER_AUTH_TOKEN": None,
        "POWERCONTEXT_SERVER_ACCESS_BACKGROUND_PRINCIPAL_ID": "plugin-worker",
    }


async def _grant(admin: httpx.AsyncClient, user: str, scope: str, role: str) -> None:
    response = await admin.post(
        "/v1/access/bindings/create",
        json={
            "subject": {"type": "user", "id": user},
            "resource": {"type": "scope", "scope_id": scope},
            "role": role,
            "idempotency_key": f"{user}-{scope}-{role}",
        },
    )
    assert response.status_code == 201, response.text


def test_plugin_family_reads_and_direct_writes_need_the_callers_permission(tmp_path):
    plugins = tmp_path / "plugins"
    (plugins / "notes").mkdir(parents=True)
    (plugins / "notes" / "plugin.py").write_text(PLUGIN, encoding="utf-8")

    async def scenario():
        async with running_server(tmp_path, plugins, extra_env=_identities(tmp_path)) as admin:
            shared = await create_scope(admin, "plugin-access-shared")
            private = await create_scope(admin, "plugin-access-private")
            await _grant(admin, "alice", shared, "scope.contributor")
            await _grant(admin, "bob", shared, "scope.contributor")
            await _grant(admin, "carol", shared, "scope.viewer")
            await _grant(admin, "dave", private, "scope.contributor")

            users = {
                name: httpx.AsyncClient(
                    base_url=admin.base_url, headers={"Authorization": f"Bearer {name}-token"}, timeout=20
                )
                for name in USERS
            }
            alice, bob, carol, dave = (users[name] for name in USERS)
            try:
                secret = await dave.post(
                    f"/v1/scopes/{private}/artifacts", json={"family": "notes", "content": {"text": "private plan"}}
                )
                assert secret.status_code == 201, secret.text
                created = await alice.post(
                    f"/v1/scopes/{shared}/artifacts", json={"family": "notes", "content": {"text": "alice's note"}}
                )
                assert created.status_code == 201, created.text
                note = created.json()["artifact_id"]

                # A Scope viewer reads plugin Artifacts only in the Scope it was granted.
                path = f"/v1/scopes/{shared}/artifacts/notes/{note}"
                for url in (path, f"{path}/rendering"):
                    assert (await carol.get(url)).status_code == 200, url
                found = await carol.post(f"/v1/scopes/{shared}/artifacts/notes/search", json={"query": "note"})
                assert found.status_code == 200, found.text
                hidden = f"/v1/scopes/{private}/artifacts/notes/{secret.json()['artifact_id']}"
                for url in (hidden, f"{hidden}/rendering", f"/v1/scopes/{private}/artifacts/notes"):
                    assert (await carol.get(url)).status_code == 403, url
                searched = await carol.post(f"/v1/scopes/{private}/artifacts/notes/search", json={"query": "plan"})
                assert searched.status_code == 403, searched.text
                for scope, expected in ((shared, 200), (private, 403)):
                    prepared = await carol.post(
                        "/v1/context/prepare",
                        json={
                            "scope_id": scope,
                            "query": "note",
                            "assembly": {"sections": [{"family": "notes", "limit": 2}]},
                        },
                    )
                    assert prepared.status_code == expected, prepared.text

                # Another contributor cannot change Alice's note through a plugin route either.
                routes = f"/v1/plugins/notes/api/scopes/{shared}/notes/{note}"
                for action in ("tag", "retire", "bind"):
                    denied = await bob.post(f"{routes}/{action}")
                    assert denied.status_code == 403, (action, denied.text)
                state = (await alice.get(f"{path}/governance")).json()
                assert state["lifecycle_state"] == "active"
                assert (await alice.get(f"{path}/tags")).json()["tags"] == []

                for action in ("tag", "bind", "retire"):
                    allowed = await alice.post(f"{routes}/{action}")
                    assert allowed.status_code == 204, (action, allowed.text)
                assert (await alice.get(f"{path}/governance")).json()["lifecycle_state"] == "retired"
            finally:
                for client in users.values():
                    await client.aclose()

    asyncio.run(scenario())
