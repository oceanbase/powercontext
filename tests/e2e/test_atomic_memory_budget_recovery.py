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

"""Durable one-Source admission blocking and explicit, idempotent recovery."""

import asyncio
import os
from dataclasses import replace
from functools import partial
from hashlib import sha256

import pytest
from sqlalchemy import func, select

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryCandidate, AtomicMemoryExtractionOutput
from powercontext.builtin.artifacts.atomic_memory.processing import (
    AtomicMemoryBlockNotFoundError,
    AtomicMemoryBlockSourceIneligibleError,
    AtomicMemoryInputBudgetError,
)
from powercontext.builtin.inference import GenerationResult, TokenEstimator, character_token_estimator
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import MEMORY_WORK_BLOCKS_TABLE
from powercontext.builtin.processing_diagnostics import diagnostics_of
from powercontext.builtin.processing_status import ProcessingStatusApplication
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, RuntimeConfig, open_builtin_runtime
from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingBlockedError
from powercontext.builtin.scope import ScopeDraft
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME
from tests.e2e.dream_support import atomic_memory_pipeline


class _Extractor:
    def __init__(self):
        self.requests = []

    async def generate(self, value, /):
        self.requests.append(value)
        return GenerationResult(
            output=AtomicMemoryExtractionOutput(
                candidates=(
                    AtomicMemoryCandidate(
                        kind="fact",
                        text="The user requires exact evidence retention.",
                        description="Evidence retention preference",
                        evidence_ids=tuple(item.evidence_id for item in value.evidence),
                    ),
                )
            )
        )


def _config(path, budget=4000, *, database_kind="sqlite"):
    return BuiltinConfig(
        database=SeekDBConfig(path=path)
        if database_kind == "seekdb"
        else SQLiteConfig(url=f"sqlite+aiosqlite:///{path}"),
        runtime=RuntimeConfig(
            atomic_memory_input_tokens_limit=budget,
            atomic_memory_related_mode="fts",
        ),
    )


@pytest.fixture(
    params=[
        "sqlite",
        pytest.param(
            "seekdb",
            marks=pytest.mark.skipif(
                os.environ.get("POWERCONTEXT_TEST_MIGRATION_SEEKDB") != "1", reason="real seekdb probe not enabled"
            ),
        ),
    ]
)
def recovery_config(request, short_tmp_path):
    if request.param == "seekdb":
        pytest.importorskip("pylibseekdb")
    return partial(_config, short_tmp_path / "recovery.db", database_kind=request.param)


async def _scope(runtime, name):
    assert runtime.scopes is not None
    return (await runtime.scopes.create(ScopeDraft(title=name, summary=name, idempotency_key=name))).scope_id


async def _oversized(runtime, scope):
    return await runtime.sources.for_scope(scope).capture(
        CaptureSource(
            source_id="oversized",
            content="用户要求完整保留证据。" * 1000,
            metadata={},
        )
    )


def test_block_survives_flush_capture_restart_and_recovers_without_duplicate_publication(recovery_config):
    extractor = _Extractor()
    pipeline = atomic_memory_pipeline(extractor)

    async def scenario():
        async with open_builtin_runtime(recovery_config(), candidate_pipeline=pipeline) as runtime:
            assert runtime.atomic_memory is not None
            scope = await _scope(runtime, "blocked")
            memory = runtime.atomic_memory.for_scope(scope)
            await _oversized(runtime, scope)
            with pytest.raises(ArtifactProcessingBlockedError) as failed:
                await memory.flush(limit=1)
            block = (await memory.processing_status()).block
            assert block is not None
            diagnostic = diagnostics_of(failed.value)
            assert diagnostic is not None
            assert diagnostic.estimated_input_tokens == block.estimated_input_tokens
            assert block.estimated_input_tokens > block.effective_budget == 4000
            assert (block.source_after, block.source_through) == (0, 1)
            for _ in range(2):
                with pytest.raises(ArtifactProcessingBlockedError):
                    await memory.flush(limit=1)
            assert (await memory.processing_status()).block == block
            with pytest.raises(AtomicMemoryInputBudgetError):
                await memory.retry(block.block_id)
            await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="later", content="A later fact.", metadata={})
            )
            assert (await memory.processing_status()).source_head == 2
            assert (await memory.cursor()).sequence == 0
            assert (await memory.list(limit=10)).items == ()
            assert not extractor.requests
            projected = await ProcessingStatusApplication(runtime).for_scope(scope).status("memory")
            assert projected.block is not None and projected.block.diagnostics is not None
            assert projected.state == "blocked" and projected.block.block_id == block.block_id
            assert projected.block.diagnostics.effective_budget == 4000
            other_scope = await _scope(runtime, "independent")
            await runtime.sources.for_scope(other_scope).capture(
                CaptureSource(source_id="small", content="The user requires exact evidence retention.", metadata={})
            )
            assert (await runtime.atomic_memory.for_scope(other_scope).flush(limit=1)).current_cursor == 1
            with pytest.raises(AtomicMemoryBlockNotFoundError):
                await runtime.atomic_memory.for_scope(other_scope).get_block(block.block_id)

        async with open_builtin_runtime(recovery_config(), candidate_pipeline=pipeline) as runtime:
            assert runtime.atomic_memory is not None
            memory = runtime.atomic_memory.for_scope(scope)
            assert (await memory.processing_status()).block == block
            with pytest.raises(ArtifactProcessingBlockedError):
                await memory.flush(limit=1)
            with pytest.raises(AtomicMemoryInputBudgetError):
                await memory.retry(block.block_id)
            assert (await memory.cursor()).sequence == 0

        async with open_builtin_runtime(recovery_config(100000), candidate_pipeline=pipeline) as runtime:
            assert runtime.atomic_memory is not None
            memory = runtime.atomic_memory.for_scope(scope)
            # A configuration change alone does not clear the block.
            with pytest.raises(ArtifactProcessingBlockedError):
                await memory.flush(limit=1)
            accepted = await asyncio.gather(memory.retry(block.block_id), memory.retry(block.block_id))
            assert accepted[0] == accepted[1]
            assert accepted[0].block.retried_at is not None
            assert accepted[0].block.blocked_at == block.blocked_at
            assert (await memory.cursor()).sequence == 0
            assert (await memory.flush(limit=1)).current_cursor == 1
            before = (await memory.list(limit=10)).items
            assert len(before) == 1
            assert await memory.retry(block.block_id) == accepted[0]
            assert (await memory.list(limit=10)).items == before
            assert (await memory.cursor()).sequence == 1
            async with runtime.primary_database.transaction() as connection:
                assert await connection.scalar(select(func.count()).select_from(MEMORY_WORK_BLOCKS_TABLE)) == 1

        # Replaying a receipt does not need a configured generator.
        async with open_builtin_runtime(recovery_config()) as runtime:
            assert runtime.atomic_memory is not None
            memory = runtime.atomic_memory.for_scope(scope)
            assert await memory.retry(block.block_id) == accepted[0]
            assert await memory.get_block(block.block_id) == accepted[0].block

    asyncio.run(scenario())


def test_rejection_fingerprint_covers_rendered_input_and_allows_corrected_input_on_retry(tmp_path):
    extractor = _Extractor()
    estimated_inputs = []
    rendered_payloads = []
    prefix = "custom rendering " * 2000
    estimator = character_token_estimator()

    def render(value):
        payload = prefix + "\n" + value.model_dump_json()
        rendered_payloads.append(payload)
        return payload

    def estimate(text):
        estimated_inputs.append(text)
        return estimator.estimate(text)

    pipeline = replace(
        atomic_memory_pipeline(extractor),
        input_renderer=render,
        estimator=TokenEstimator(estimator.profile, estimate),
    )

    async def scenario():
        nonlocal prefix
        async with open_builtin_runtime(_config(tmp_path / "fingerprint.db"), candidate_pipeline=pipeline) as runtime:
            assert runtime.atomic_memory is not None
            scope = await _scope(runtime, "fingerprint")
            memory = runtime.atomic_memory.for_scope(scope)
            await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="small", content="Preserve complete evidence.", metadata={})
            )
            with pytest.raises(ArtifactProcessingBlockedError):
                await memory.flush(limit=1)
            block = (await memory.processing_status()).block
            assert block is not None
            rejected_input = estimated_inputs[-1]
            assert rejected_input.endswith("\n" + rendered_payloads[-1])
            assert rejected_input != rendered_payloads[-1]
            assert block.input_fingerprint == sha256(rejected_input.encode("utf-8")).hexdigest()
            assert not extractor.requests

            prefix = "corrected rendering"
            receipt = await memory.retry(block.block_id)
            assert sha256(estimated_inputs[-1].encode("utf-8")).hexdigest() != block.input_fingerprint
            assert receipt.block.input_fingerprint == block.input_fingerprint
            assert not extractor.requests
            assert (await memory.flush(limit=1)).current_cursor == 1
            assert len(extractor.requests) == 1
            assert await memory.get_block(block.block_id) == receipt.block

    asyncio.run(scenario())


def test_recovery_rolls_back_intent_and_receipt_when_interrupted(recovery_config, monkeypatch):
    import powercontext.builtin.runtime.atomic_memory_processing as module

    async def scenario():
        async with open_builtin_runtime(
            recovery_config(), candidate_pipeline=atomic_memory_pipeline(_Extractor())
        ) as runtime:
            assert runtime.atomic_memory is not None
            scope = await _scope(runtime, "rollback")
            memory = runtime.atomic_memory.for_scope(scope)
            await _oversized(runtime, scope)
            with pytest.raises(ArtifactProcessingBlockedError):
                await memory.flush(limit=1)
            block = (await memory.processing_status()).block
            assert runtime._atomic_memory_recovery is not None
            processor = runtime._atomic_memory_recovery.processor
            processor.config = replace(processor.config, input_tokens_limit=100000)
            async with runtime.primary_database.transaction() as connection:
                before = await ArtifactProcessingIntentRepository().load(connection, scope, SOURCE_WINDOW_TRIGGER_NAME)
            original = module.accept_memory_retry

            async def interrupted(*args):
                await original(*args)
                raise asyncio.CancelledError

            with monkeypatch.context() as patch:
                patch.setattr(module, "accept_memory_retry", interrupted)
                with pytest.raises(asyncio.CancelledError):
                    await memory.retry(block.block_id)
            assert (await memory.processing_status()).block == block
            async with runtime.primary_database.transaction() as connection:
                assert (
                    await ArtifactProcessingIntentRepository().load(connection, scope, SOURCE_WINDOW_TRIGGER_NAME)
                    == before
                )
            accepted = await memory.retry(block.block_id)
            assert accepted.block.retry_request_generation is not None

    asyncio.run(scenario())


def test_retry_rejects_an_ineligible_source_and_leaves_the_block_untouched(recovery_config, monkeypatch):
    import powercontext.builtin.runtime.atomic_memory_processing as module

    async def scenario():
        extractor = _Extractor()
        async with open_builtin_runtime(
            recovery_config(), candidate_pipeline=atomic_memory_pipeline(extractor)
        ) as runtime:
            assert runtime.atomic_memory is not None and runtime._atomic_memory_recovery is not None
            scope = await _scope(runtime, "ineligible")
            memory = runtime.atomic_memory.for_scope(scope)
            await _oversized(runtime, scope)
            with pytest.raises(ArtifactProcessingBlockedError):
                await memory.flush(limit=1)
            block = (await memory.processing_status()).block
            assert block is not None
            async with runtime.primary_database.transaction() as connection:
                before = await ArtifactProcessingIntentRepository().load(connection, scope, SOURCE_WINDOW_TRIGGER_NAME)
            with monkeypatch.context() as patch:
                patch.setattr(module, "is_generation_eligible", lambda source: False)
                with pytest.raises(AtomicMemoryBlockSourceIneligibleError) as raised:
                    await memory.retry(block.block_id)
            assert raised.value.block_id == block.block_id
            assert raised.value.source_ref.source_id == "oversized"
            assert raised.value.source_ref.source_type == "content"
            # The rejection leaves the block, cursor, intent, and receipt unchanged
            # and performs no model work.
            assert (await memory.processing_status()).block == block
            assert (await memory.cursor()).sequence == 0
            async with runtime.primary_database.transaction() as connection:
                assert (
                    await ArtifactProcessingIntentRepository().load(connection, scope, SOURCE_WINDOW_TRIGGER_NAME)
                    == before
                )
            assert not extractor.requests

    asyncio.run(scenario())


def test_retry_rejects_an_eligibility_change_at_the_commit_boundary(recovery_config, monkeypatch):
    import powercontext.builtin.runtime.atomic_memory_processing as module

    class _FlipEligibility:
        def __init__(self):
            self.eligible = True

        def __call__(self, source):
            result = self.eligible
            self.eligible = False
            return result

    async def scenario():
        extractor = _Extractor()
        async with open_builtin_runtime(
            recovery_config(), candidate_pipeline=atomic_memory_pipeline(extractor)
        ) as runtime:
            assert runtime.atomic_memory is not None and runtime._atomic_memory_recovery is not None
            scope = await _scope(runtime, "eligibility-flip")
            memory = runtime.atomic_memory.for_scope(scope)
            await _oversized(runtime, scope)
            with pytest.raises(ArtifactProcessingBlockedError):
                await memory.flush(limit=1)
            block = (await memory.processing_status()).block
            assert block is not None
            processor = runtime._atomic_memory_recovery.processor
            processor.config = replace(processor.config, input_tokens_limit=100000)
            async with runtime.primary_database.transaction() as connection:
                before = await ArtifactProcessingIntentRepository().load(connection, scope, SOURCE_WINDOW_TRIGGER_NAME)
            # The preflight sees an eligible Source, but eligibility flips before the
            # commit-boundary re-check; the commit must be rejected without partial state.
            with monkeypatch.context() as patch:
                patch.setattr(module, "is_generation_eligible", _FlipEligibility())
                with pytest.raises(AtomicMemoryBlockSourceIneligibleError) as raised:
                    await memory.retry(block.block_id)
            assert raised.value.block_id == block.block_id
            assert (await memory.processing_status()).block == block
            assert (await memory.cursor()).sequence == 0
            async with runtime.primary_database.transaction() as connection:
                assert (
                    await ArtifactProcessingIntentRepository().load(connection, scope, SOURCE_WINDOW_TRIGGER_NAME)
                    == before
                )
            assert not extractor.requests

    asyncio.run(scenario())


def test_spawned_supervisor_blocks_before_provider_calls_and_admits_other_scopes_after_restart(tmp_path, monkeypatch):
    import threading
    from collections import Counter

    from pydantic import AnyHttpUrl

    from powercontext.builtin.runtime import InferenceConfig
    from powercontext.builtin.runtime.artifact_processing import SpawnArtifactProcessingWorkerLauncher
    from tests.builtin.runtime.test_processing_scheduler import _wait
    from tests.e2e.test_atomic_memory_supervisor_generation import _GenerationHandler, _GenerationServer

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-local-provider")
    server = _GenerationServer(("127.0.0.1", 0), _GenerationHandler)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    starts = Counter()
    original = SpawnArtifactProcessingWorkerLauncher.start

    async def start(launcher, assignment):
        starts[assignment.scope_id] += 1
        return await original(launcher, assignment)

    monkeypatch.setattr(SpawnArtifactProcessingWorkerLauncher, "start", start)
    config = BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'spawned.db'}"),
        inference=InferenceConfig(
            generation_model="openai-chat:gpt-4o-mini",
            generation_base_url=AnyHttpUrl(f"http://127.0.0.1:{server.server_port}/v1"),
        ),
        runtime=RuntimeConfig(
            artifact_processing_families=("memory",),
            memory_schedule_seconds=0.02,
            memory_worker_timeout_seconds=45,
            atomic_memory_related_mode="fts",
            atomic_memory_input_tokens_limit=4000,
        ),
    )

    async def scenario():
        async with open_builtin_runtime(config) as runtime:
            assert runtime.atomic_memory is not None
            scope = await _scope(runtime, "oversized")
            await _oversized(runtime, scope)
            supervisor = runtime.artifact_processing_supervisor
            assert supervisor is not None
            await _wait(
                lambda observed=supervisor: observed.family_status["memory"]["blocked"] == 1, timeout_seconds=45
            )
            block = (await runtime.atomic_memory.for_scope(scope).processing_status()).block
            assert starts[scope] == 1 and not server.requests
            assert supervisor.family_status["memory"]["failed"] == 0
            assert (await runtime.atomic_memory.for_scope(scope).cursor()).sequence == 0

        async with open_builtin_runtime(config) as runtime:
            assert runtime.atomic_memory is not None
            supervisor = runtime.artifact_processing_supervisor
            assert supervisor is not None
            await _wait(
                lambda observed=supervisor: observed.family_status["memory"]["blocked"] == 1, timeout_seconds=15
            )
            assert (await runtime.atomic_memory.for_scope(scope).processing_status()).block == block
            await runtime.sources.for_scope(scope).capture(
                CaptureSource(source_id="later", content="Later evidence.", metadata={})
            )
            other = await _scope(runtime, "other")
            await runtime.sources.for_scope(other).capture(
                CaptureSource(
                    source_id="normal",
                    content="The user prefers acceptance tests that exercise spawned workers.",
                    metadata={},
                )
            )
            async with asyncio.timeout(45):
                while (await runtime.atomic_memory.for_scope(other).cursor()).sequence == 0:  # noqa: ASYNC110 - observe another process committing its cursor
                    await asyncio.sleep(0.02)
            assert starts[scope] == 1
            assert (await runtime.atomic_memory.for_scope(scope).cursor()).sequence == 0
            assert (await runtime.atomic_memory.for_scope(scope).processing_status()).block == block
            assert server.requests

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_http_recovery_requires_scope_admin_and_reports_the_exact_block(tmp_path):
    import httpx

    from powercontext.builtin.authz.composition import open_builtin_access_control
    from powercontext.server.factory import create_server_app
    from powercontext.server.settings import AccessControlConfig, McpConfig, MetricsConfig, ServerSettings
    from tests.e2e.server_storage import prepare_server_database
    from tests.e2e.test_access_control_regressions import ADMIN, _Authentication, _grant
    from tests.e2e.test_access_control_regressions import _scope as create_scope

    async def scenario():
        settings = ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'http.db'}"),
            runtime=RuntimeConfig(
                artifact_processing_families=(), atomic_memory_related_mode="fts", atomic_memory_input_tokens_limit=4000
            ),
            access=AccessControlConfig(mode="enforced", deployment_id="budget-recovery"),
            mcp=McpConfig(enabled=False),
            metrics=MetricsConfig(enabled=False),
        )
        await asyncio.to_thread(prepare_server_database, settings)
        async with open_builtin_access_control(
            settings.database, bootstrap_administrators=(ADMIN,), deployment_id="budget-recovery"
        ) as access:
            app = create_server_app(
                settings=settings,
                authentication_provider=_Authentication(),
                access_control=access,
                candidate_pipeline=atomic_memory_pipeline(_Extractor()),
            )
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
            ):
                scope = await create_scope(client)
                captured = await client.post(
                    "/v1/sources/content",
                    json={"scope_id": scope, "source_id": "oversized", "content": "用户要求完整保留证据。" * 1000},
                )
                assert captured.status_code == 202, captured.text
                failed = await client.post("/v1/memory/flush", json={"scope_id": scope})
                assert failed.status_code == 409, failed.text
                observed = await client.post("/v1/atomic-memory/processing/status", json={"scope_id": scope})
                assert observed.status_code == 200, observed.text
                block = observed.json()["block"]
                assert block["estimated_input_tokens"] > block["effective_budget"] == 4000
                payload = {"scope_id": scope, "block_id": block["block_id"]}
                for name, role in [
                    ("reader", "scope.viewer"),
                    ("writer", "scope.contributor"),
                    ("manager", "scope.admin"),
                ]:
                    await _grant(client, scope, name, role)
                    if name == "manager":
                        # Administration does not grant access to Source evidence.
                        await _grant(client, scope, name, "scope.contributor")
                    headers = {"Authorization": "Bearer " + name}
                    assert (
                        await client.post(
                            "/v1/atomic-memory/processing/status", json={"scope_id": scope}, headers=headers
                        )
                    ).status_code == 200
                    exact = await client.post("/v1/atomic-memory/processing/blocks/get", json=payload, headers=headers)
                    assert exact.status_code == 200 and exact.json() == block
                    result = await client.post("/v1/atomic-memory/processing/retry", json=payload, headers=headers)
                    assert result.status_code == (409 if name == "manager" else 403), result.text
                denied = await client.post(
                    "/v1/atomic-memory/processing/status",
                    json={"scope_id": scope},
                    headers={"Authorization": "Bearer stranger"},
                )
                assert denied.status_code == 403
                assert (await client.post("/v1/atomic-memory/processing/status", json={"scope_id": scope})).json()[
                    "block"
                ] == block
                recovery = app.state.application._atomic_memory_recovery
                recovery.processor.config = replace(recovery.processor.config, input_tokens_limit=100000)
                manager = {"Authorization": "Bearer manager"}
                accepted = await client.post("/v1/atomic-memory/processing/retry", json=payload, headers=manager)
                assert accepted.status_code == 200, accepted.text
                duplicate = await client.post("/v1/atomic-memory/processing/retry", json=payload, headers=manager)
                assert duplicate.status_code == 200 and duplicate.json() == accepted.json()
                # No Source content is returned by operational diagnostics.
                assert "用户要求完整保留证据" not in observed.text
                assert (await client.post("/v1/atomic-memory/processing/status", json={"scope_id": scope})).json()[
                    "block"
                ] is None

    asyncio.run(scenario())


def test_old_receipt_cannot_clear_a_new_block_or_bypass_the_next_worker_budget(recovery_config):
    async def scenario():
        extractor = _Extractor()
        async with open_builtin_runtime(
            recovery_config(), candidate_pipeline=atomic_memory_pipeline(extractor)
        ) as runtime:
            assert runtime.atomic_memory is not None and runtime._atomic_memory_recovery is not None
            scope = await _scope(runtime, "repeat-block")
            memory = runtime.atomic_memory.for_scope(scope)
            processor = runtime._atomic_memory_recovery.processor
            await _oversized(runtime, scope)
            with pytest.raises(ArtifactProcessingBlockedError):
                await memory.flush(limit=1)
            first = (await memory.processing_status()).block
            processor.config = replace(processor.config, input_tokens_limit=100000)
            accepted = await memory.retry(first.block_id)
            processor.config = replace(processor.config, input_tokens_limit=4000)
            with pytest.raises(ArtifactProcessingBlockedError):
                await memory.flush(limit=1)
            second = (await memory.processing_status()).block
            assert second.block_id != first.block_id and second.input_fingerprint == first.input_fingerprint
            assert not extractor.requests
            assert await memory.retry(first.block_id) == accepted
            assert (await memory.processing_status()).block == second
            processor.config = replace(processor.config, input_tokens_limit=100000)
            await memory.retry(second.block_id)
            assert (await memory.flush(limit=1)).current_cursor == 1
            assert len((await memory.list(limit=10)).items) == 1

    asyncio.run(scenario())
