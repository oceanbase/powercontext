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

"""A plugin's core records survive the operator's portable archive round trip."""

import asyncio
import json
import os
import shlex
import sys
import zipfile

from tests.e2e.plugin_server import create_scope, items, running_server, wait_for
from tests.e2e.test_plugins import NOTES, TICKETS


def test_cli_restores_plugin_history_and_custom_sources_without_running_plugins(tmp_path):
    plugins = tmp_path / "plugins"
    (plugins / "notes").mkdir(parents=True)
    (plugins / "notes" / "plugin.py").write_text(
        NOTES
        + '\nfrom powercontext.plugins import ArtifactPlugin\nplugin = ArtifactPlugin(family="notes", setup=setup)\n',
        encoding="utf-8",
    )
    (plugins / "tickets").mkdir()
    (plugins / "tickets" / "plugin.py").write_text(TICKETS, encoding="utf-8")
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    bundle = tmp_path / "plugins.pcb"

    async def archive(directory, *arguments, configured_plugins=True, expected=0):
        env = {key: value for key, value in os.environ.items() if not key.startswith("POWERCONTEXT_SERVER_")}
        env.update({
            "POWERCONTEXT_HOME": str(directory / "home"),
            "POWERCONTEXT_SERVER_DATABASE": json.dumps({
                "kind": "sqlite",
                "url": f"sqlite+aiosqlite:///{directory / 'server.db'}",
            }),
            "POWERCONTEXT_SERVER_RUNTIME": json.dumps({"artifact_processing_families": []}),
        })
        if configured_plugins:
            env["POWERCONTEXT_SERVER_PLUGINS_DIR"] = str(plugins)
        env_file = directory / "archive.env"
        env_file.write_text(
            "".join(
                f"{key}={shlex.quote(value)}\n" for key, value in env.items() if key.startswith("POWERCONTEXT_SERVER_")
            )
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "from powercontext.cli.app import main; main()",
            "archive",
            *map(str, arguments),
            "--env-file",
            str(env_file),
            env=env,
            cwd=directory,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 120)
        assert process.returncode == expected, stderr.decode()
        return json.loads(stdout)

    def revisions(path):
        with zipfile.ZipFile(path) as zipped:
            records = [json.loads(line) for line in zipped.read("records.ndjson").splitlines()]
        return [record for record in records if record["record_type"] == "artifact_revision"]

    async def scenario():
        async with running_server(source, plugins) as client:
            scope = await create_scope(client, "portable-plugin")
            created = await client.post(
                f"/v1/scopes/{scope}/artifacts", json={"family": "notes", "content": {"text": "original"}}
            )
            assert created.status_code == 201, created.text
            path = created.headers["Location"]
            updated = await client.put(
                path, headers={"If-Match": created.headers["ETag"]}, json={"content": {"text": "edited"}}
            )
            assert updated.status_code == 200, updated.text
            saved = updated.json()
            history = (await client.get(path + "/revisions")).json()
            ticket = await client.post(
                f"/v1/scopes/{scope}/sources",
                json={"source_type": "ticket", "content": {"key": "OPS-7", "title": "Portable ticket"}},
            )
            assert ticket.status_code == 201, ticket.text
            ticket_path = ticket.headers["Location"]
            ticket_snapshot = (await client.get(ticket_path)).json()
            generated = await wait_for(lambda: items(client, scope, "tickets"), lambda value: len(value) == 1)
            generated_path = f"/v1/scopes/{scope}/artifacts/tickets/{generated[0]['artifact_id']}"
            generated_snapshot = (await client.get(generated_path)).json()
        broken = plugins / "unavailable"
        broken.mkdir()
        (broken / "plugin.py").write_text('raise ImportError("fixture unavailable")\n', encoding="utf-8")
        await archive(source, "export", "--scope-id", scope, "--output", bundle)
        (broken / "plugin.py").unlink()
        broken.rmdir()
        rejected = await archive(target, "restore", bundle, "--dry-run", configured_plugins=False, expected=3)
        assert rejected["unsupported_artifact_families"] == ["notes", "tickets"]
        assert not (target / "server.db").exists()
        validation = await archive(target, "restore", bundle, "--dry-run")
        assert validation["compatible"] is True
        assert not (target / "server.db").exists()
        # A Server target needs the managed deployment schema, including local authorization tables.
        async with running_server(target) as client:
            assert (await client.get("/health/ready")).status_code == 200
        first = await archive(target, "restore", bundle, "--yes")
        assert first["inserted"] > 0 and first["projections_ready"] is True
        second = await archive(target, "restore", bundle, "--yes")
        assert second["inserted"] == 0 and second["already_present"] == first["record_count"]
        # Inspect only declarations during archiving: setup must not create host resources.
        assert not (target / "home" / "plugins").exists()
        async with running_server(target, plugins) as client:
            assert (await client.get(path)).json() == saved
            assert (await client.get(path + "/revisions")).json() == history
            assert (await client.get(path + "/rendering")).json()["text"].endswith("edited")
            assert (await client.get(ticket_path)).json() == ticket_snapshot
            assert (await client.get(generated_path)).json() == generated_snapshot
        copied = tmp_path / "copied.pcb"
        await archive(target, "export", "--scope-id", scope, "--output", copied)
        assert revisions(copied) == revisions(bundle)

    asyncio.run(scenario())
