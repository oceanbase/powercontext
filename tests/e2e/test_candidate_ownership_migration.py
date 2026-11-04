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

"""Candidate recovery follows accepted identities, independently of Artifact ownership."""

import asyncio
import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    CreateDreamRunRequest,
    GetCandidateRequest,
    GetDreamRunRequest,
    InferenceConfig,
    RuntimeConfig,
)
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.authz.composition import open_builtin_access_control
from tests.e2e.dream_support import open_dream_runtime, process_pending
from tests.e2e.test_dream_skill_prompt import PromptGenerator
from tests.e2e.test_owner_recovery_http import ALICE, _server


@pytest.mark.parametrize("completed", [False, True])
@pytest.mark.parametrize("check_http", [False, True], ids=["cli", "http-and-cli"])
def test_prompt_candidate_recovery_blocks_competing_http_and_offline_migration(
    tmp_path: Path, completed: bool, check_http: bool
) -> None:
    async def scenario() -> None:
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{(tmp_path / 'domain.db').as_posix()}")
        # Seed through the supported complete Server schema before using the SDK Dream path.
        async with _server(tmp_path):
            pass
        async with open_dream_runtime(
            BuiltinConfig(
                database=database,
                runtime=RuntimeConfig(artifact_processing_families=()),
                inference=InferenceConfig(generation_model="test"),
            ),
            dream_generator=PromptGenerator(),
        ) as runtime:
            scope = await runtime.scopes.create(
                ScopeDraft(title="Prompt recovery", summary="Original attribution", idempotency_key="prompt-recovery")
            )
            scope_id = scope.scope_id
            prompt = await runtime.records.for_scope(scope_id).create_artifact(
                "prompt",
                ArtifactWrite(
                    prompt_key="atomic_memory.extract",
                    content={
                        "schema_version": "powercontext.prompt.v1",
                        "mode": "custom",
                        "instructions": "Always treat trips as moves.",
                        "demonstrations": [],
                    },
                ),
            )
            target = ArtifactRef(family="prompt", artifact_id=prompt.artifact_id, revision=prompt.revision)
            source = await runtime.sources.for_scope(scope_id).capture(
                CaptureSource(
                    source_id="correction", content="A two-day visit does not change the user's residence.", metadata={}
                )
            )
            dream = runtime.dream.for_scope(scope_id)
            run = await dream.create(
                CreateDreamRunRequest(
                    operation="revise_prompt",
                    target=target,
                    artifacts=(target,),
                    sources=(source.source_ref,),
                    idempotency_key="correct-trip",
                )
            )
            await process_pending(runtime)
            run = await dream.get(GetDreamRunRequest(run_id=run.run_id))
            assert run.candidate is not None, run
            candidate_id = run.candidate.candidate_id
            candidate_before = await runtime.review.for_scope(scope_id).get(
                GetCandidateRequest(candidate_id=candidate_id)
            )

        async with _server(tmp_path, interrupted=True) as (app, client, access):
            initial = await client.post(
                "/v1/access/ownership/apply", json={"scope_ids": [scope_id], "owner": {"type": "user", "id": "alice"}}
            )
            assert initial.status_code == 503, initial.text
            (intent,) = await app.state.owner_recovery.pending(scope_id=scope_id)
            assert intent.kind == "candidate" and intent.candidate_id == candidate_id and intent.principal == ALICE
            if completed:
                access.relationships.unavailable = False
                await app.state.owner_recovery.replay(intent.operation_id)
                # An external relation can be lost after delivery; the primary evidence remains authoritative.
                with closing(sqlite3.connect(tmp_path / "relationships.db")) as external:
                    external.execute(
                        "DELETE FROM pc_access_owners WHERE owner_kind = 'candidate' AND scope_id = ?", (scope_id,)
                    )
                    external.commit()
                assert await access.candidate_owner(scope_id, candidate_id) is None
            reason = "delivered_relation_missing" if completed else "pending"
            if check_http:
                for dry_run in (True, False):
                    result = await client.post(
                        "/v1/access/ownership/apply",
                        json={"scope_ids": [scope_id], "owner": {"type": "user", "id": "bob"}, "dry_run": dry_run},
                    )
                    assert result.status_code == 200, result.text
                    assert result.json()["applied"] == []
                    assert [(item["candidate_id"], item["conflict_code"]) for item in result.json()["conflicts"]] == [
                        (candidate_id, f"owner-recovery-{reason}")
                    ]

        environment = tmp_path / "offline.env"
        environment.write_text(
            "POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\n"
            f"POWERCONTEXT_SERVER_DATABASE_URL={database.url}\n"
            "POWERCONTEXT_SERVER_ACCESS_MODE=disabled\n"
            "POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID=external-test\n"
            "POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_FAMILIES=[]\n",
            encoding="utf-8",
        )
        process = await asyncio.create_subprocess_exec(
            str(Path(sys.executable).with_name("powercontext.exe" if os.name == "nt" else "powercontext")),
            "server",
            "access-ownership-migrate",
            "--env-file",
            str(environment),
            "--action",
            "apply",
            "--owner",
            "user:bob",
            "--scope-id",
            scope_id,
            "--maintenance-confirmed",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(30):
                stdout, stderr = await process.communicate()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
        assert process.returncode == 1, (stdout + stderr).decode()
        result = json.loads(stdout)
        assert result["applied"] == []
        assert [(item["gap"]["candidate_id"], item["detail"]) for item in result["conflicts"]] == [
            (candidate_id, f"owner-recovery-{reason}")
        ]
        async with open_builtin_access_control(database, deployment_id="external-test") as local:
            assert await local.candidate_owner(scope_id, candidate_id) is None
        async with _server(tmp_path) as (app, _client, access):
            if not completed:
                await app.state.owner_recovery.replay(intent.operation_id)
                attestation = await access.candidate_owner(scope_id, candidate_id)
                assert attestation is not None and attestation.proposed_owner == ALICE
            assert await app.state.owner_recovery.pending(scope_id=scope_id) == ()
            candidate_after = await app.state.application.review.for_scope(scope_id).get(
                GetCandidateRequest(candidate_id=candidate_id)
            )
            assert candidate_after.model_dump(mode="json") == candidate_before.model_dump(mode="json")

    asyncio.run(scenario())
