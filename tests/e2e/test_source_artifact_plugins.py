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

"""A Source plugin snapshot flows through a real Artifact Worker to searchable evidence."""

import asyncio
import shutil

from fastmcp import Client

from tests.e2e.test_artifact_plugins import EXAMPLE, artifacts, change, deploy_plugin, reload_plugin
from tests.source_helpers import ROOT, TOKENS, call, create_scope, grant, load_write_cli, read, running_server


def test_source_plugin_to_artifact_search_and_exact_read(tmp_path):
    asyncio.run(_source_to_artifact(tmp_path))


async def _source_to_artifact(directory):
    source_plugin = directory / "tickets"
    shutil.copytree(
        ROOT / "examples/source_plugins/tickets", source_plugin, ignore=shutil.ignore_patterns("__pycache__")
    )
    report_plugin = deploy_plugin(EXAMPLE, directory / "reports", None)
    entry = report_plugin / "plugin.py"
    # Use the delivered generator with the Source plugin's persisted text projection.
    entry.write_text(
        entry.read_text().replace(
            '"content": TEXT_EVIDENCE_PROJECTION_KEY', '"example-ticket": TEXT_EVIDENCE_PROJECTION_KEY'
        )
    )
    value = {
        "namespace": "support",
        "project": "powercontext",
        "ticket_id": "PC-123",
        "title": "数据库恢复",
        "body": "Outage resolved after database retry.",
    }
    environment = {"POWERCONTEXT_SERVER_ARTIFACT_PLUGINS_DIR": str(directory / "plugins")}
    (directory / "plugins").mkdir()
    async with running_server(directory, environment=environment) as (admin, url, _process):
        scope = await create_scope(admin, "source-artifact-chain")
        await grant(admin, scope, "deployer")
        await grant(admin, scope)
        await load_write_cli(source_plugin / "write.yml", url)
        await reload_plugin(admin, directory, report_plugin, 0)
        await change(admin, "enable", 1, "enabled")
        async with Client(url + "/mcp", auth=TOKENS["host"]) as host:
            captured = await call(
                host,
                "pc_source_add",
                {
                    "scope_id": scope,
                    "capability": "ticket.write",
                    "capability_version": "1",
                    "input": value,
                },
            )
        source_ref = captured["result"]["source_refs"][0]
        assert source_ref["source_type"] == "example-ticket"
        assert (await read(admin, scope, source_ref))["content"]["payload"]["capture"]["body"] == value["body"]
        [artifact] = await artifacts(admin, scope, 1)
        async with asyncio.timeout(30):
            while True:
                response = await admin.post(
                    f"/v1/scopes/{scope}/artifacts/incident-report/search", json={"query": "数据库恢复"}
                )
                if response.status_code == 200 and response.json()["results"]:
                    break
                assert response.status_code in (200, 503), response.text
                await asyncio.sleep(0.1)
        [hit] = response.json()["results"]
        assert hit["artifact_id"] == artifact["artifact_id"]
        assert hit["revision"] == artifact["revision"]
        exact = await admin.get(
            f"/v1/scopes/{scope}/artifacts/incident-report/{hit['artifact_id']}/revisions/{hit['revision']}"
        )
        assert exact.status_code == 200, exact.text
        assert exact.json()["content"]["evidence_text"] == value["title"] + "\n\n" + value["body"]
        assert exact.json()["sources"] == [source_ref]
