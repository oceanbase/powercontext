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

"""A deployed plugin is called by the Server and read through the public Artifact API."""

import asyncio

from tests.e2e.plugin_server import capture, create_scope, items, running_server, wait_for
from tests.plugin_support import write_plugin

NOTES = """
from powercontext.plugins import ArtifactWriter, GenerateRequest, InvalidContent, Rendering, SearchHit, SearchResult


class Notes:
    def __init__(self, ctx):
        self.ctx = ctx

    async def render_artifact(self, request) -> Rendering:
        return Rendering(text="# Note\\n\\n" + request.artifact.content["text"])

    async def search_artifacts(self, request) -> SearchResult:
        # The plugin owns its search: here, a substring match over its current Artifacts.
        unsupported = set(request.search) - {"query", "limit"}
        if unsupported:
            raise InvalidContent("unsupported search fields: " + ", ".join(sorted(unsupported)))
        query = request.search["query"].casefold()
        page = await self.ctx.reads(request.scope_id).list(limit=200)
        hits = [
            SearchHit(ref=item.ref, text=item.content["text"], score=1.0)
            for item in page.items
            if query in item.content["text"].casefold()
        ]
        return SearchResult(hits=hits[: request.search.get("limit", 10)])

    async def generate(self, request: GenerateRequest, writer: ArtifactWriter) -> None:
        async with writer.transaction() as tx:
            for source in request.sources:
                text = source.projection["content"]
                await tx.put(writer.stable_id(source.ref.source_id), content={"text": text}, source_refs=[source.ref])

    async def create_artifact(self, request, writer) -> None:
        async with writer.transaction() as tx:
            await tx.put(request.artifact_id, content=request.content)

    async def update_artifact(self, request, writer) -> None:
        async with writer.transaction() as tx:
            await tx.put(request.artifact_id, content=request.content, expected_revision=request.expected_revision)

    async def forget_artifact(self, request, writer) -> None:
        async with writer.transaction() as tx:
            await tx.set_lifecycle(request.artifact_id, "deprecated")

    async def merge_artifacts(self, request, writer) -> None:
        async with writer.transaction() as tx:
            await tx.merge([item.artifact_id for item in request.inputs], into=request.into, content=request.content)

    async def restore_artifact(self, request, writer) -> None:
        async with writer.transaction() as tx:
            if request.operation == "undo_merge":
                await tx.undo_merge(request.artifact_id)
            elif request.revision is not None:
                await tx.revert(request.artifact_id, to_revision=request.revision)
            else:
                await tx.set_lifecycle(request.artifact_id, "active")


async def setup(ctx):
    return Notes(ctx)
"""


async def governance(client, path):
    response = await client.get(path)
    assert response.status_code == 200, response.text
    return response.json()


BROKEN = """
raise ImportError("missing dependency")
"""


def test_server_loads_plugins_dispatches_on_sources_and_serves_the_artifact_api(tmp_path):
    plugins = tmp_path / "plugins"
    write_plugin(plugins, "notes", NOTES)
    write_plugin(plugins, "broken", BROKEN)

    async def search(client, scope, query):
        response = await client.post(f"/v1/scopes/{scope}/artifacts/notes/search", json={"query": query})
        assert response.status_code == 200, response.text
        return [item["content"]["text"] for item in response.json()["results"]]

    async def scenario():
        async with running_server(tmp_path, plugins) as client:
            listing = {item["name"]: item for item in (await client.get("/v1/plugins")).json()["items"]}
            assert {name: item["status"] for name, item in listing.items()} == {"broken": "failed", "notes": "loaded"}
            assert listing["notes"]["source_types"] == ["content"] and listing["notes"]["triggers"] == ["sources"]
            assert "notes" in (await client.get("/v1/capabilities")).json()["artifact_families"]

            scope = await create_scope(client, "plugin-e2e")
            await capture(client, scope, "deploy", "Deploys go to the east region")
            await capture(client, scope, "keys", "Rotate signing keys monthly")
            log = tmp_path / "server.log"
            notes = await wait_for(lambda: items(client, scope, "notes"), lambda value: len(value) == 2, log=log)
            bodies = {}
            for item in notes:
                response = await client.get(f"/v1/scopes/{scope}/artifacts/notes/{item['artifact_id']}")
                assert response.status_code == 200, response.text
                bodies[item["artifact_id"]] = response.json()["content"]["text"]
            assert set(bodies.values()) == {"Deploys go to the east region", "Rotate signing keys monthly"}
            assert await search(client, scope, "region") == ["Deploys go to the east region"]
            # The whole search request reaches the plugin, which rejects what it does not support.
            rejected_search = await client.post(
                f"/v1/scopes/{scope}/artifacts/notes/search", json={"query": "region", "mode": "vector"}
            )
            assert rejected_search.status_code == 422, rejected_search.text
            east = next(key for key, text in bodies.items() if "east" in text)
            rendering = await client.get(f"/v1/scopes/{scope}/artifacts/notes/{east}/rendering")
            assert rendering.status_code == 200, rendering.text
            assert rendering.json() == {
                "family": "notes",
                "artifact_id": east,
                "revision": 1,
                "format": "markdown",
                "text": "# Note\n\nDeploys go to the east region",
            }
            builtin_rendering = await client.get(f"/v1/scopes/{scope}/artifacts/skill/x/rendering")
            assert builtin_rendering.status_code == 422, builtin_rendering.text
            prepared = await client.post(
                "/v1/context/prepare",
                json={
                    "scope_id": scope,
                    "query": "region",
                    "include_receipt": True,
                    "assembly": {"sections": [{"family": "notes", "limit": 2}]},
                },
            )
            assert prepared.status_code == 200, prepared.text
            assert "## notes" in prepared.json()["content"]
            assert "Deploys go to the east region" in prepared.json()["content"]
            plugin_search_on_builtin = await client.post(
                "/v1/context/prepare",
                json={
                    "scope_id": scope,
                    "query": "region",
                    "assembly": {"sections": [{"family": "memory", "limit": 2, "search": {"mode": "fts"}}]},
                },
            )
            assert plugin_search_on_builtin.status_code == 422, plugin_search_on_builtin.text

            # The generic create route takes a plugin family's content; core allocates the ID.
            created = await client.post(
                f"/v1/scopes/{scope}/artifacts", json={"family": "notes", "content": {"text": "Freeze on Fridays"}}
            )
            assert created.status_code == 201, created.text
            assert (created.json()["family"], created.json()["revision"]) == ("notes", 1)
            assert created.headers["ETag"] == '"revision:1"'
            assert (await client.get(created.headers["Location"])).json()["content"] == {"text": "Freeze on Fridays"}
            assert await search(client, scope, "fridays") == ["Freeze on Fridays"]
            await client.post(
                f"/v1/scopes/{scope}/artifacts/notes/{created.json()['artifact_id']}/forget",
                json={
                    "revision": 1,
                    "state_version": (await governance(client, f"{created.headers['Location']}/governance"))[
                        "state_version"
                    ],
                },
            )
            # Built-in names keep their own contract; a family no plugin provides is invalid.
            builtin = await client.post(
                f"/v1/scopes/{scope}/artifacts", json={"family": "memory", "content": {"text": "x"}}
            )
            assert builtin.status_code == 422, builtin.text
            unknown = await client.post(f"/v1/scopes/{scope}/artifacts", json={"family": "nope", "content": {}})
            assert unknown.status_code == 422, unknown.text

            async def progress():
                response = await client.get("/v1/plugins/notes/scopes")
                assert response.status_code == 200, response.text
                return {item["scope_id"]: item for item in response.json()["items"]}

            state = (await wait_for(progress, lambda value: not value[scope]["pending"], log=log))[scope]
            assert state["consumed_position"] == state["source_position"]

            # Users change plugin Artifacts through the generic Artifact API; the plugin's host process applies it.
            target = next(key for key, text in bodies.items() if "east" in text)
            path = f"/v1/scopes/{scope}/artifacts/notes/{target}"
            edited = await client.put(
                path,
                json={"content": {"text": "Deploys go to the west region"}},
                headers={"If-Match": (await client.get(path)).headers["ETag"]},
            )
            assert edited.status_code == 200, edited.text
            assert edited.json()["revision"] == 2
            assert await search(client, scope, "west") == ["Deploys go to the west region"]
            stale = await client.put(path, json={"content": {"text": "lost update"}}, headers={"If-Match": '"1"'})
            assert stale.status_code == 412, stale.text

            # Tags, forget, merge and restoration use the generic routes every registered family shares.
            tags_path = f"{path}/tags"
            tagged = await client.put(
                tags_path,
                json={"tags": ["release"]},
                headers={"If-Match": (await client.get(tags_path)).headers["ETag"]},
            )
            assert tagged.status_code == 200, tagged.text
            matches = await client.post(f"/v1/scopes/{scope}/artifact-tags/query", json={"tags": ["Release"]})
            assert matches.status_code == 200, matches.text
            assert [item["target"]["artifact_id"] for item in matches.json()["items"]] == [target]

            state = await governance(client, f"{path}/governance")
            assert (state["lifecycle_state"], state["artifact"]["revision"]) == ("active", 2)
            forgotten = await client.post(
                f"{path}/forget", json={"revision": 2, "state_version": state["state_version"]}
            )
            assert forgotten.status_code == 200, forgotten.text
            assert forgotten.json()["records"][0]["lifecycle_state"] == "deprecated"
            assert await search(client, scope, "region") == []
            stale_forget = await client.post(
                f"{path}/forget", json={"revision": 2, "state_version": state["state_version"]}
            )
            assert stale_forget.status_code == 409, stale_forget.text
            restored = await client.post(f"{path}/restorations", json={})
            assert restored.status_code == 200, restored.text
            assert await search(client, scope, "west") == ["Deploys go to the west region"]

            inputs = []
            for artifact_id in bodies:
                current = await governance(client, f"/v1/scopes/{scope}/artifacts/notes/{artifact_id}/governance")
                inputs.append({
                    "artifact_id": artifact_id,
                    "revision": current["artifact"]["revision"],
                    "state_version": current["state_version"],
                })
            merged = await client.post(
                f"/v1/scopes/{scope}/artifacts/notes/merges",
                json={"inputs": inputs, "content": {"text": "Deploys go west; rotate keys monthly"}},
            )
            assert merged.status_code == 200, merged.text
            records = {item["artifact"]["artifact_id"]: item for item in merged.json()["records"]}
            result = next(key for key in records if key not in bodies)
            assert {records[key]["merged_into_id"] for key in bodies} == {result}
            assert await search(client, scope, "monthly") == ["Deploys go west; rotate keys monthly"]
            undone = await client.post(
                f"/v1/scopes/{scope}/artifacts/notes/{result}/restorations", json={"operation": "undo_merge"}
            )
            assert undone.status_code == 200, undone.text
            assert [ref["artifact_id"] for ref in undone.json()["retired"]] == [result]
            assert sorted(await search(client, scope, "monthly")) == ["Rotate signing keys monthly"]

            # Families without a registered merge service are rejected before any work.
            for family in ("unknown", "skill"):
                rejected = await client.post(
                    f"/v1/scopes/{scope}/artifacts/{family}/x/forget", json={"revision": 1, "state_version": 0}
                )
                assert rejected.status_code == 422, rejected.text

    asyncio.run(scenario())


def test_generic_merge_routes_serve_registered_built_in_families(tmp_path):
    from tests.e2e.test_artifact_merge_experience import _content

    plugins = tmp_path / "plugins"
    plugins.mkdir()

    async def scenario():
        async with running_server(tmp_path, plugins) as client:
            scope = await create_scope(client, "generic-merge-e2e")
            created = []
            for name in ("canary", "pause"):
                response = await client.post(
                    f"/v1/scopes/{scope}/artifacts",
                    json={"family": "experience", "content": _content(name).model_dump(mode="json", exclude_none=True)},
                )
                assert response.status_code == 201, response.text
                created.append(response.json()["artifact_id"])
            inputs = []
            for artifact_id in created:
                state = await governance(client, f"/v1/scopes/{scope}/artifacts/experience/{artifact_id}/governance")
                inputs.append({"artifact_id": artifact_id, "revision": 1, "state_version": state["state_version"]})
            merged = await client.post(
                f"/v1/scopes/{scope}/artifacts/experience/merges",
                json={"inputs": inputs, "content": _content("combined").model_dump(mode="json", exclude_none=True)},
            )
            assert merged.status_code == 200, merged.text
            frozen = {item["artifact"]["artifact_id"]: item["merged_into_id"] for item in merged.json()["records"]}
            (result,) = set(frozen) - set(created)
            assert {frozen[key] for key in created} == {result}

            first = f"/v1/scopes/{scope}/artifacts/experience/{created[0]}"
            restored = await client.post(f"{first}/restorations", json={})
            assert restored.status_code == 200, restored.text
            assert [ref["artifact_id"] for ref in restored.json()["retired"]] == [result]
            state = await governance(client, f"{first}/governance")
            assert (state["lifecycle_state"], state["merged_into_id"]) == ("active", None)

    asyncio.run(scenario())


TICKETS = """
from pydantic import BaseModel, Field

from powercontext.plugins import ArtifactPlugin, Inputs, Rendering
from powercontext.sources import Source, SourceMaterialization


class TicketInput(BaseModel):
    key: str = Field(pattern=r"^[A-Z]+-[0-9]+$")
    title: str = Field(min_length=1)


class TicketSource(Source):
    title: str


class TicketProjection:
    name = "tickets.ticket"
    version = "1"
    source_class = TicketSource
    output_class = TicketInput

    def project(self, source, /):
        return TicketInput(key=source.name, title=source.title)


class TicketDefinition:
    input_class = TicketInput
    name = "ticket"
    source_class = TicketSource
    version = "1"
    projections = (TicketProjection(),)

    async def resolve(self, value, /):
        return TicketSource(
            name=value.key, materialization=SourceMaterialization.CAPTURED, description="A ticket", title=value.title
        )

    async def read(self, source, /):
        return TicketProjection().project(source)


class Tickets:
    async def render_artifact(self, request):
        return Rendering(format="text", text=request.artifact.content["title"])

    async def generate(self, request, writer):
        async with writer.transaction() as tx:
            for source in request.sources:
                key = source.projection["key"]
                await tx.put(writer.stable_id(key), content=source.projection, source_refs=[source.ref])


async def setup(ctx):
    return Tickets()


plugin = ArtifactPlugin(
    family="tickets",
    inputs=Inputs(source_types=("ticket",), projection="tickets.ticket@1"),
    on_sources=True,
    definitions=(TicketDefinition(),),
    setup=setup,
)
"""


def test_generic_source_api_writes_plugin_source_types(tmp_path):
    plugins = tmp_path / "plugins"
    (plugins / "tickets").mkdir(parents=True)
    (plugins / "tickets" / "plugin.py").write_text(TICKETS, encoding="utf-8")

    async def scenario():
        async with running_server(tmp_path, plugins) as client:
            scope = await create_scope(client, "plugin-sources-e2e")
            sources = f"/v1/scopes/{scope}/sources"
            ticket = {"source_type": "ticket", "content": {"key": "OPS-7", "title": "Disk full on db-1"}}
            created = await client.post(sources, json=ticket)
            assert created.status_code == 201, created.text
            assert (created.json()["source_type"], created.json()["source_id"]) == ("ticket", "OPS-7")
            stored = await client.get(created.headers["Location"])
            assert stored.status_code == 200, stored.text
            # The same input resolves to the same Source.
            assert (await client.post(sources, json=ticket)).json()["position"] == created.json()["position"]

            invalid = await client.post(sources, json={"source_type": "ticket", "content": {"key": "bad"}})
            assert invalid.status_code == 422, invalid.text
            unknown = await client.post(sources, json={"source_type": "nope", "content": {}})
            assert unknown.status_code == 404, unknown.text
            content = await client.post(sources, json={"content": "built-in content still works"})
            assert content.status_code == 201, content.text

            tickets = await wait_for(
                lambda: items(client, scope, "tickets"), lambda value: len(value) == 1, log=tmp_path / "server.log"
            )
            body = await client.get(f"/v1/scopes/{scope}/artifacts/tickets/{tickets[0]['artifact_id']}")
            assert body.json()["content"] == {"key": "OPS-7", "title": "Disk full on db-1"}

    asyncio.run(scenario())
