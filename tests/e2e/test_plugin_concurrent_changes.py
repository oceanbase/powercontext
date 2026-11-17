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

"""User changes on plugin families apply to the revision the caller read and report the revision they wrote."""

import asyncio
from pathlib import Path

from tests.e2e.plugin_server import create_scope, running_server, wait_for

# Each user-change method can pause at a gate so the test commits a competing write at that moment.
PLUGIN = """
import asyncio
from pathlib import Path

from powercontext.plugins import ArtifactPlugin, Rendering

GATES = Path({gates!r})


async def gate(name):
    (GATES / (name + ".waiting")).touch()
    while not (GATES / (name + ".open")).exists():
        await asyncio.sleep(0.05)


class Notes:
    def __init__(self, ctx):
        self.ctx = ctx

    async def render_artifact(self, request):
        return Rendering(text=request.artifact.content["text"])

    async def create_artifact(self, request, writer):
        async with writer.transaction() as tx:
            await tx.put(request.artifact_id, content=request.content)

    async def update_artifact(self, request, writer):
        content = dict(request.content)
        hold = content.pop("hold", False)
        # Like a plugin that forgets to pass expected_revision, this relies on the framework's check.
        async with writer.transaction() as tx:
            await tx.put(request.artifact_id, content=content)
        if hold:
            await gate("updated")

    async def forget_artifact(self, request, writer):
        await gate("forget")
        async with writer.transaction() as tx:
            await tx.set_lifecycle(request.artifact_id, "deprecated")

    async def merge_artifacts(self, request, writer):
        await gate("merge")
        async with writer.transaction() as tx:
            await tx.merge([item.artifact_id for item in request.inputs], into=request.into, content=request.content)


async def setup(ctx):
    return Notes(ctx)


plugin = ArtifactPlugin(family="notes", setup=setup)
"""


def test_user_changes_apply_to_the_read_revision_and_report_their_own_write(tmp_path: Path):
    plugins = tmp_path / "plugins"
    gates = tmp_path / "gates"
    gates.mkdir()
    (plugins / "notes").mkdir(parents=True)
    (plugins / "notes" / "plugin.py").write_text(PLUGIN.format(gates=str(gates)), encoding="utf-8")
    log = tmp_path / "server.log"

    async def scenario():
        async with running_server(tmp_path, plugins) as client:
            scope = await create_scope(client, "plugin-concurrent-changes")

            async def create(text):
                response = await client.post(
                    f"/v1/scopes/{scope}/artifacts", json={"family": "notes", "content": {"text": text}}
                )
                assert response.status_code == 201, response.text
                return f"/v1/scopes/{scope}/artifacts/notes/{response.json()['artifact_id']}"

            async def governance(path):
                response = await client.get(f"{path}/governance")
                assert response.status_code == 200, response.text
                return response.json()

            async def edit(path, content):
                etag = (await client.get(path)).headers["ETag"]
                return await client.put(path, json={"content": content}, headers={"If-Match": etag})

            async def paused(name):
                await wait_for(lambda: asyncio.sleep(0, (gates / f"{name}.waiting").exists()), bool, log=log)

            # A forget decided on revision 1 must not retire revision 2, committed after its precheck.
            note = await create("Deploy on Fridays")
            state = await governance(note)
            forget = asyncio.create_task(
                client.post(f"{note}/forget", json={"revision": 1, "state_version": state["state_version"]})
            )
            await paused("forget")
            assert (await edit(note, {"text": "Never deploy on Fridays"})).status_code == 200
            (gates / "forget.open").touch()
            stale_forget = await forget
            assert stale_forget.status_code == 409, stale_forget.text
            state = await governance(note)
            assert (state["lifecycle_state"], state["artifact"]["revision"]) == ("active", 2)

            # A merge decided on the inputs' read revisions fails when one of them changes first.
            first, second = await create("Rotate keys"), await create("Rotate certificates")
            inputs = []
            for path in (first, second):
                current = await governance(path)
                inputs.append({
                    "artifact_id": path.rsplit("/", 1)[1],
                    "revision": current["artifact"]["revision"],
                    "state_version": current["state_version"],
                })
            merge = asyncio.create_task(
                client.post(
                    f"/v1/scopes/{scope}/artifacts/notes/merges",
                    json={"inputs": inputs, "content": {"text": "Rotate keys and certificates"}},
                )
            )
            await paused("merge")
            assert (await edit(first, {"text": "Rotate keys monthly"})).status_code == 200
            (gates / "merge.open").touch()
            stale_merge = await merge
            assert stale_merge.status_code == 409, stale_merge.text
            for path in (first, second):
                current = await governance(path)
                assert (current["lifecycle_state"], current["merged_into_id"]) == ("active", None)

            # Each update reports the revision it committed, even after a later write moved the head.
            held = asyncio.create_task(edit(note, {"text": "Deploy on Tuesdays", "hold": True}))
            await paused("updated")
            later = await edit(note, {"text": "Deploy on Wednesdays"})
            assert later.status_code == 200, later.text
            (gates / "updated.open").touch()
            own = await held
            assert own.status_code == 200, own.text
            assert (own.json()["revision"], own.json()["content"]) == (3, {"text": "Deploy on Tuesdays"})
            assert (later.json()["revision"], later.json()["content"]) == (4, {"text": "Deploy on Wednesdays"})

    asyncio.run(scenario())
