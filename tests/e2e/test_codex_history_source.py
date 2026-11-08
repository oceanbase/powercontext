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

"""Codex history uses managed submissions, persisted checkpoints and ordinary Content reads."""

from __future__ import annotations

import asyncio
import json
from hashlib import sha256

import pytest

from integrations.codex.source_plugin import CodexHistoryConnector, Config, identify_target
from powercontext.builtin.sources import CONTENT_SOURCE_DEFINITION, ContentCapture
from powercontext.client import PowerContextClient
from powercontext.client.errors import ServerResponseError
from powercontext.client.ingestion import register_source_definitions
from powercontext.http import CaptureContentSourceRequest, SubmitSourceObservationRequest
from powercontext.http import SourceObservation as HttpSourceObservation
from powercontext.source_management.context import PluginContext
from powercontext.source_management.models import SourceTarget, TargetIdentityRequest
from powercontext.source_management.targets import ManagedTargets
from powercontext.sources import ConnectorRunStatus, SourceDefinitionRegistry, project_source_for_transport
from tests.source_helpers import ROOT, TOKENS, create_scope, grant, running_server


def write_session(home, *, content="keep this decision", other=False):
    path = home / "sessions" / ("other.jsonl" if other else "rollout.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"type": "session_meta", "payload": {"id": "session-a", "cwd": "/project"}},
        {"type": "turn_context", "payload": {"turn_id": "turn-a"}},
        {"type": "response_item", "payload": {"type": "message", "role": "user", "content": content}},
        {"type": "response_item", "payload": {"type": "message", "role": "assistant", "content": "ignored"}},
    ]
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    return path


def target(scope):
    return SourceTarget(
        target_id="session-target",
        scope_id=scope,
        instance_id="codex-history",
        capability="codex.session-history",
        capability_version="1",
        input={"session_file": "rollout.jsonl"},
    )


async def context(client, home, selected):
    await register_source_definitions(client=client, registry=SourceDefinitionRegistry((CONTENT_SOURCE_DEFINITION,)))

    async def report(value):
        pass

    targets = ManagedTargets(instance_id="codex-history", report=report)
    targets.synchronize([selected.model_dump(mode="json")], [])
    ctx = PluginContext(
        plugin_id="codex-history",
        connector_name="codex-history",
        connector_version="1",
        plugin_dir=ROOT / "integrations/codex/source_plugin",
        secrets={},
        client=client,
        registry=SourceDefinitionRegistry((CONTENT_SOURCE_DEFINITION,)),
        targets=targets,
    )
    connector = CodexHistoryConnector(ctx, Config(codex_home=home))
    connector.targets = (selected,)
    ctx.connect(connector)
    return ctx


@pytest.mark.parametrize("existing_live", [False, True])
def test_history_persists_content_and_reuses_live_identity(tmp_path, existing_live):
    asyncio.run(_persist(tmp_path, existing_live))


async def _persist(directory, existing_live):
    home = directory / "codex"
    write_session(home)
    write_session(home, content="must not import another session", other=True)
    async with running_server(directory) as (admin, url, _):
        scope = await create_scope(admin, "history")
        await grant(admin, scope, "deployer")
        identity = "\0".join((scope, "session-a", "turn-a", "keep this decision"))
        source_id = "codex-user-prompt:" + sha256(identity.encode()).hexdigest()
        async with PowerContextClient(url, token=TOKENS["deployer"], allow_insecure_http=True) as client:
            if existing_live:
                await client.capture_content_source(
                    CaptureContentSourceRequest(
                        scope_id=scope,
                        source_id=source_id,
                        content="keep this decision",
                        metadata={
                            "origin": "codex",
                            "event": "user_prompt_submit",
                            "cwd": "/project",
                            "session_id": "session-a",
                            "turn_id": "turn-a",
                        },
                    )
                )
            selected = target(scope)
            ctx = await context(client, home, selected)
            result = await ctx.run_batch(scope_id=scope, targets=(selected,))
            assert result.status == ConnectorRunStatus.COMPLETE
            assert result.items[0].source_ref.source_id == source_id
            registry = SourceDefinitionRegistry((CONTENT_SOURCE_DEFINITION,))
            native = await registry.resolve(ContentCapture(source_id="forged", content="ordinary text"))
            observation = project_source_for_transport(registry, native)
            for field, value in (("handoff_receipt", True), ("working_note", True), ("wire_content_present", True)):
                forged = observation.model_copy(update={"payload": {**observation.payload, field: value}})
                with pytest.raises(ServerResponseError) as denied:
                    await client.submit_source_observation(
                        SubmitSourceObservationRequest(
                            scope_id=scope,
                            observation=HttpSourceObservation.model_validate(forged.model_dump(mode="json")),
                        )
                    )
                assert denied.value.status_code == 422
            assert isinstance(observation.projections[0].value, dict)
            forged_text = observation.model_copy(
                update={
                    "projections": (
                        observation.projections[0].model_copy(
                            update={"value": {**observation.projections[0].value, "content": "forged evidence"}}
                        ),
                    )
                }
            )
            with pytest.raises(ServerResponseError) as denied:
                await client.submit_source_observation(
                    SubmitSourceObservationRequest(
                        scope_id=scope,
                        observation=HttpSourceObservation.model_validate(forged_text.model_dump(mode="json")),
                    )
                )
            assert denied.value.status_code == 422
            source = await client.get_source(scope, "content", source_id)
            assert source.content == "keep this decision"
            await ctx.close()
            # A fresh Context resumes from the Server checkpoint, without any local import file.
            ctx = await context(client, home, selected)
            resumed = await ctx.run_batch(scope_id=scope, targets=(selected,))
            assert resumed.items == ()
            page = await admin.get(f"/v1/scopes/{scope}/sources")
            assert len(page.json()["items"]) == 1
            # Another explicitly selected Scope has its own identity and checkpoint.
            other_scope = await create_scope(admin, "other-history")
            await grant(admin, other_scope, "deployer")
            other_target = target(other_scope)
            other_ctx = await context(client, home, other_target)
            other = await other_ctx.run_batch(scope_id=other_scope, targets=(other_target,))
            assert len(other.items) == 1
            assert other.items[0].source_ref.source_id != source_id
            await other_ctx.close()
            await ctx.close()


def test_history_malformed_file_remains_retryable(tmp_path):
    asyncio.run(_retry(tmp_path))


async def _retry(directory):
    home = directory / "codex"
    path = write_session(home)
    good = path.read_text()
    path.write_text(good + "{broken\n")
    async with running_server(directory) as (admin, url, _):
        scope = await create_scope(admin, "retry")
        await grant(admin, scope, "deployer")
        async with PowerContextClient(url, token=TOKENS["deployer"], allow_insecure_http=True) as client:
            selected = target(scope)
            ctx = await context(client, home, selected)
            failed = await ctx.run_batch(scope_id=scope, targets=(selected,))
            assert failed.status == ConnectorRunStatus.INCOMPLETE
            assert failed.committed_checkpoint is None
            assert failed.items[0].detail == "session_unreadable"
            assert (await admin.get(f"/v1/scopes/{scope}/sources")).json()["items"] == []
            path.write_text(good)
            result = await ctx.run_batch(scope_id=scope, targets=(selected,))
            assert result.status == ConnectorRunStatus.COMPLETE
            await ctx.close()


@pytest.mark.parametrize("filename", ["../outside.jsonl", "/absolute.jsonl", "rollout.txt"])
def test_history_target_cannot_escape_configured_directory(tmp_path, filename):
    request = TargetIdentityRequest(
        capability="codex.session-history", capability_version="1", input={"session_file": filename}
    )
    with pytest.raises(ValueError, match="inside the configured"):
        asyncio.run(identify_target(request, Config(codex_home=tmp_path)))


def test_history_redacts_selected_codex_credentials(tmp_path):
    asyncio.run(_redact(tmp_path))


async def _redact(directory):
    home = directory / "codex"
    secret = "test-secret-for-history-redaction"  # noqa: S105 - synthetic credential fixture.
    write_session(home, content=f"credential {secret}")
    (home / "auth.json").write_text(json.dumps({"OPENAI_API_KEY": secret}))
    async with running_server(directory) as (admin, url, _):
        scope = await create_scope(admin, "redaction")
        await grant(admin, scope, "deployer")
        async with PowerContextClient(url, token=TOKENS["deployer"], allow_insecure_http=True) as client:
            selected = target(scope)
            ctx = await context(client, home, selected)
            result = await ctx.run_batch(scope_id=scope, targets=(selected,))
            source_id = result.items[0].source_ref.source_id
            source = await client.get_source(scope, "content", source_id)
            assert secret not in source.model_dump_json()
            assert "[REDACTED]" in source.model_dump_json()
            await ctx.close()
