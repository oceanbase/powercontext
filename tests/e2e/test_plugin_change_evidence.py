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

"""User changes keep what the caller submitted, and background writes respect a forget that came first."""

import asyncio
from pathlib import Path

from tests.e2e.plugin_server import capture, create_scope, items, running_server, wait_for
from tests.plugin_support import write_plugin

# One draft per Scope. Regeneration pauses at a gate after reading the draft, so the test can forget it meanwhile.
DRAFTS = """
import asyncio
from pathlib import Path

from powercontext.plugins import Rendering

GATES = Path({gates!r})


class Drafts:
    def __init__(self, ctx):
        self.ctx = ctx

    async def render_artifact(self, request):
        return Rendering(text=request.artifact.content["text"])

    async def generate(self, request, writer):
        draft = writer.stable_id("draft")
        current = await writer.get(draft)
        if current is not None:
            (GATES / "regenerate.waiting").touch()
            while not (GATES / "regenerate.open").exists():
                await asyncio.sleep(0.05)
            if current.lifecycle_state != "active":
                (GATES / "skipped").touch()
                async with writer.transaction():
                    pass
                return
        async with writer.transaction() as tx:
            for source in request.sources:
                await tx.put(draft, content={{"text": source.projection["content"]}}, source_refs=[source.ref])

    async def update_artifact(self, request, writer):
        # The request describes a change; the plugin computes the content and keeps the draft's Sources.
        current = await writer.get(request.artifact_id)
        text = current.content["text"] + request.content["append"]
        async with writer.transaction() as tx:
            await tx.put(request.artifact_id, content={{"text": text}}, source_refs=current.lineage.sources)

    async def forget_artifact(self, request, writer):
        async with writer.transaction() as tx:
            await tx.set_lifecycle(request.artifact_id, "deprecated")


async def setup(ctx):
    return Drafts(ctx)
"""


def _server(tmp_path: Path):
    plugins = tmp_path / "plugins"
    gates = tmp_path / "gates"
    gates.mkdir()
    write_plugin(plugins, "drafts", DRAFTS.format(gates=str(gates)))
    return plugins, gates


async def _draft(client, scope: str) -> str:
    await capture(client, scope, "first", "Restart after the backup")
    found = await wait_for(lambda: items(client, scope, "drafts"), lambda value: len(value) == 1)
    return f"/v1/scopes/{scope}/artifacts/drafts/{found[0]['artifact_id']}"


def test_an_edit_that_keeps_its_sources_commits_and_records_the_submitted_content(tmp_path: Path):
    plugins, _ = _server(tmp_path)

    async def scenario():
        # The Server enforces access control, so the edit authorizes the kept Sources inside its transaction.
        async with running_server(tmp_path, plugins) as client:
            scope = await create_scope(client, "plugin-change-evidence")
            draft = await _draft(client, scope)
            etag = (await client.get(draft)).headers["ETag"]
            submitted = {"append": " completes."}
            edited = await client.put(draft, json={"content": submitted}, headers={"If-Match": etag})
            assert edited.status_code == 200, edited.text
            body = edited.json()
            assert (body["revision"], body["content"]) == (2, {"text": "Restart after the backup completes."})

            assert {ref["source_id"] for ref in body["sources"]} > {"first"}
            (evidence,) = (ref for ref in body["sources"] if ref["source_id"] != "first")
            stored = await client.get(f"/v1/scopes/{scope}/sources/{evidence['source_type']}/{evidence['source_id']}")
            assert stored.status_code == 200, stored.text
            # The evidence is the request's content, not the content the plugin computed from it.
            assert stored.json()["content"] == submitted, stored.text

    asyncio.run(scenario())


def test_a_background_write_does_not_revise_a_draft_forgotten_after_it_was_read(tmp_path: Path):
    plugins, gates = _server(tmp_path)
    log = tmp_path / "server.log"

    async def scenario():
        async with running_server(tmp_path, plugins) as client:
            scope = await create_scope(client, "plugin-forgotten-draft")
            draft = await _draft(client, scope)

            async def reached(name):
                await wait_for(lambda: asyncio.sleep(0, (gates / name).exists()), bool, seconds=120, log=log)

            # Regeneration reads the active draft, then the user forgets it before the write.
            await capture(client, scope, "second", "Restart at any time")
            await reached("regenerate.waiting")
            state = (await client.get(f"{draft}/governance")).json()
            forgotten = await client.post(
                f"{draft}/forget", json={"revision": 1, "state_version": state["state_version"]}
            )
            assert forgotten.status_code == 200, forgotten.text
            (gates / "regenerate.open").touch()

            # Forgetting left the revision at 1, so only the governance check can reject the stale write.
            # The retried invocation reads the forgotten draft and leaves it alone.
            await reached("skipped")
            state = (await client.get(f"{draft}/governance")).json()
            assert (state["lifecycle_state"], state["artifact"]["revision"]) == ("deprecated", 1)

    asyncio.run(scenario())
