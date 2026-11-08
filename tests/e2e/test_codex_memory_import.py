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

"""Real plugin CLI to authenticated Server acceptance, including provenance and retry semantics."""

from __future__ import annotations

import asyncio
import json
import os
import sys

from integrations.codex.memory_source_plugin.files import Config
from tests.source_helpers import ROOT, TOKENS, create_scope, grant, protected, running_server
from tests.test_codex_memory_source import put

MODULE = "integrations.codex.memory_source_plugin"


async def invoke(directory, *arguments, expected=0):
    environment = dict(os.environ)
    environment["POWERCONTEXT_CLIENT_CONFIG_FILE"] = str(directory / "empty-client-settings.json")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        MODULE,
        *arguments,
        cwd=ROOT,
        env=environment,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 45)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == expected, (stdout + stderr).decode()
    return json.loads(stdout)


async def register(directory, url):
    credential = protected(directory / "admin.token", TOKENS["deployer"])
    return await invoke(directory, "register", "--server-url", url, "--credential-file", str(credential))


def arguments(directory, url, scope, home, *, binding="memory-import"):
    credential = protected(directory / "host.token", TOKENS["host"])
    return (
        "import",
        "--codex-home",
        str(home),
        "--namespace",
        "personal",
        "--server-url",
        url,
        "--scope-id",
        scope,
        "--binding-id",
        binding,
        "--credential-file",
        str(credential),
    )


async def journal(admin, scope):
    response = await admin.get(f"/v1/scopes/{scope}/sources")
    assert response.status_code == 200, response.text
    return response.json()["items"]


def test_cli_previews_imports_updates_and_retains_historical_evidence(tmp_path):
    asyncio.run(_versions(tmp_path))


async def _versions(directory):
    config = Config(codex_home=directory / "codex", namespace="personal")
    put(config, "MEMORY.md", "# Derived preferences\nUse focused tests.\n")
    put(config, "rollout_summaries/run.md", "# Derived run\nA prior build succeeded.\n")
    preview = await invoke(directory, "preview", "--codex-home", str(config.codex_home), "--namespace", "personal")
    assert {item["path"] for item in preview["files"]} == {"MEMORY.md", "rollout_summaries/run.md"}
    assert "Derived preferences" not in json.dumps(preview)
    async with running_server(directory) as (admin, url, _):
        scope = await create_scope(admin, "memory-import")
        await grant(admin, scope)
        await grant(admin, scope, "deployer")
        assert await journal(admin, scope) == []
        assert (await register(directory, url))["registered"] == "codex-memory-snapshot"
        args = arguments(directory, url, scope, config.codex_home)
        result = await invoke(directory, *args)
        assert result["status"] == "complete"
        assert len(result["items"]) == 2
        initial = await journal(admin, scope)
        assert len(initial) == 2
        captures = {item["content"]["payload"]["capture"]["relative_path"]: item for item in initial}
        original = captures["MEMORY.md"]
        assert original["source_type"] == "codex-memory-snapshot"
        capture = original["content"]["payload"]["capture"]
        assert capture["evidence_kind"] == "derived_memory"
        assert capture["content"] == "# Derived preferences\nUse focused tests.\n"
        assert str(config.codex_home) not in json.dumps(initial)
        # A second process resumes from the Server, without a local checkpoint.
        assert (await invoke(directory, *args))["items"] == []
        # Losing the binding checkpoint still reuses the immutable Source snapshots.
        await invoke(directory, *arguments(directory, url, scope, config.codex_home, binding="replay"))
        assert len(await journal(admin, scope)) == 2
        put(config, "MEMORY.md", "# Derived preferences\nKeep exact provenance.\n")
        updated = await invoke(directory, *args)
        assert len(updated["items"]) == 1
        assert len(await journal(admin, scope)) == 3
        old = await admin.get(f"/v1/scopes/{scope}/sources/{original['source_type']}/{original['source_id']}")
        assert old.status_code == 200
        assert old.json()["content"]["payload"]["capture"] == capture
        (config.codex_home / "memories/MEMORY.md").unlink()
        await invoke(directory, *args)
        assert len(await journal(admin, scope)) == 3
        other = await create_scope(admin, "other-memory-scope")
        await grant(admin, other)
        await grant(admin, other, "deployer")
        await invoke(directory, *arguments(directory, url, other, config.codex_home))
        assert len(await journal(admin, other)) == 1
        assert len(await journal(admin, scope)) == 3


def test_cli_partial_failure_retries_saved_items_without_duplicate_sources(tmp_path):
    asyncio.run(_retry(tmp_path))


async def _retry(directory):
    config = Config(codex_home=directory / "codex", namespace="personal")
    put(config, "a.md", "Derived summary one")
    broken = put(config, "b.md", b"\xff")
    async with running_server(directory) as (admin, url, _):
        scope = await create_scope(admin, "memory-retry")
        await grant(admin, scope)
        await grant(admin, scope, "deployer")
        await register(directory, url)
        args = arguments(directory, url, scope, config.codex_home)
        failed = await invoke(directory, *args, expected=1)
        assert failed["status"] == "incomplete"
        assert any(item["reason"] == "invalid_utf8" for item in failed["items"])
        assert len(await journal(admin, scope)) == 1
        checkpoint = await admin.post(
            "/v1/connector-checkpoints/get",
            json={
                "binding": {
                    "scope_id": scope,
                    "binding_id": "memory-import",
                    "connector_name": "codex-memory",
                    "connector_version": "1",
                }
            },
        )
        assert checkpoint.status_code == 200
        assert checkpoint.json()["checkpoint"] is None
        broken.write_text("Derived summary two")
        retried = await invoke(directory, *args)
        assert retried["status"] == "complete"
        assert len(await journal(admin, scope)) == 2
        assert (await invoke(directory, *args))["items"] == []


def test_import_requires_scope_permission_but_not_server_administration(tmp_path):
    asyncio.run(_permissions(tmp_path))


async def _permissions(directory):
    config = Config(codex_home=directory / "codex", namespace="personal")
    put(config, "MEMORY.md", "Derived summary")
    async with running_server(directory) as (admin, url, _):
        scope = await create_scope(admin, "permission")
        await grant(admin, scope, "deployer")
        await register(directory, url)
        args = arguments(directory, url, scope, config.codex_home)
        denied = await invoke(directory, *args, expected=1)
        assert denied == {"error": "server_rejected", "http_status": 403}
        assert await journal(admin, scope) == []
        await grant(admin, scope)
        result = await invoke(directory, *args)
        assert result["status"] == "complete"
        denied_registration = await invoke(
            directory, "register", "--server-url", url, "--credential-file", str(directory / "host.token"), expected=1
        )
        assert denied_registration == {"error": "server_rejected", "http_status": 403}


def test_process_exit_after_source_acceptance_can_resume_without_duplicate_evidence(tmp_path):
    asyncio.run(_interrupted(tmp_path))


async def _interrupted(directory):
    config = Config(codex_home=directory / "codex", namespace="personal")
    put(config, "MEMORY.md", "Keep the exact derived snapshot.")
    async with running_server(directory) as (admin, url, _):
        scope = await create_scope(admin, "interrupted-memory")
        await grant(admin, scope)
        await grant(admin, scope, "deployer")
        await register(directory, url)
        args = arguments(directory, url, scope, config.codex_home)
        # This subprocess exits after real Source receipts and before the public
        # Connector lifecycle can commit its checkpoint. No product hook is mocked.
        code = """
import asyncio
import os
import sys
from pathlib import Path
from integrations.codex.memory_source_plugin import CodexMemoryConnector
from integrations.codex.memory_source_plugin.files import Config
from powercontext.client import PowerContextClient, RemoteConnectorWorker
from powercontext.source_management.config import secret
from powercontext.source_management.models import SourceTarget
from powercontext.sources import ConnectorBinding, SourceDefinitionRegistry
from integrations.codex.memory_source_plugin.definition import MEMORY_DEFINITION

class InterruptedConnector(CodexMemoryConnector):
    async def run(self, session):
        await super().run(session)
        os._exit(23)

async def main():
    home, url, credential, scope = sys.argv[1:]
    config = Config(codex_home=Path(home), namespace="personal")
    target = SourceTarget(target_id="interrupted", scope_id=scope, instance_id="memory-import",
                          capability="codex.memory-history", capability_version="1", input={"path":"."})
    binding = ConnectorBinding(scope_id=scope, binding_id="memory-import", connector_name="codex-memory", connector_version="1")
    async with PowerContextClient(url, token=secret(Path(credential)), allow_insecure_http=True) as client:
        await RemoteConnectorWorker(client=client, registry=SourceDefinitionRegistry((MEMORY_DEFINITION,)),
                                    register_definitions=False).run(InterruptedConnector(config, (target,)), binding)
asyncio.run(main())
"""
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            code,
            str(config.codex_home),
            url,
            str(directory / "host.token"),
            scope,
            cwd=ROOT,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), 40)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert process.returncode == 23, (stdout + stderr).decode()
        saved = await journal(admin, scope)
        assert len(saved) == 1
        checkpoint = await admin.post(
            "/v1/connector-checkpoints/get",
            json={
                "binding": {
                    "scope_id": scope,
                    "binding_id": "memory-import",
                    "connector_name": "codex-memory",
                    "connector_version": "1",
                }
            },
        )
        assert checkpoint.json()["checkpoint"] is None
        assert (await invoke(directory, *args))["status"] == "complete"
        assert await journal(admin, scope) == saved


def test_concurrent_importers_use_checkpoint_cas_and_preserve_one_snapshot(tmp_path):
    asyncio.run(_concurrent(tmp_path))


async def _concurrent(directory):
    from integrations.codex.memory_source_plugin import CodexMemoryConnector
    from integrations.codex.memory_source_plugin.definition import MEMORY_DEFINITION
    from powercontext.client import PowerContextClient, RemoteConnectorWorker
    from powercontext.client.errors import ServerResponseError
    from powercontext.source_management.models import SourceTarget
    from powercontext.sources import ConnectorBinding, ConnectorRunResult, SourceDefinitionRegistry

    config = Config(codex_home=directory / "codex", namespace="personal")
    put(config, "MEMORY.md", "One immutable snapshot across concurrent importers.")
    async with running_server(directory) as (admin, url, _):
        scope = await create_scope(admin, "concurrent-memory")
        await grant(admin, scope)
        await grant(admin, scope, "deployer")
        await register(directory, url)
        barrier = asyncio.Event()
        finished = 0

        class ConcurrentConnector(CodexMemoryConnector):
            async def run(self, session):
                nonlocal finished
                result = await super().run(session)
                finished += 1
                if finished == 2:
                    barrier.set()
                await barrier.wait()
                return result

        target = SourceTarget(
            target_id="same-tree",
            scope_id=scope,
            instance_id="concurrent",
            capability="codex.memory-history",
            capability_version="1",
            input={"path": "."},
        )
        binding = ConnectorBinding(
            scope_id=scope, binding_id="concurrent", connector_name="codex-memory", connector_version="1"
        )
        async with PowerContextClient(url, token=TOKENS["host"], allow_insecure_http=True) as client:
            worker = RemoteConnectorWorker(
                client=client, registry=SourceDefinitionRegistry((MEMORY_DEFINITION,)), register_definitions=False
            )
            async with asyncio.timeout(30):
                outcomes = await asyncio.gather(
                    worker.run(ConcurrentConnector(config, (target,)), binding),
                    worker.run(ConcurrentConnector(config, (target,)), binding),
                    return_exceptions=True,
                )
            assert sum(isinstance(item, ConnectorRunResult) for item in outcomes) == 1
            [conflict] = [item for item in outcomes if isinstance(item, ServerResponseError)]
            assert conflict.status_code == 409 and conflict.code == "connector_checkpoint_conflict"
            resumed = await worker.run(CodexMemoryConnector(config, (target,)), binding)
            assert resumed.items == ()
        assert len(await journal(admin, scope)) == 1
