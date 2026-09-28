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

"""Opt-in real-provider acceptance for the four Scope processors.

Run each database in its own disposable location. The JSON report contains
counts and control state only; provider settings, inputs, and outputs stay out
of the artifact. This module is not collected by ordinary pytest.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import tempfile
import time
from pathlib import Path
from typing import Any, Literal, cast
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import func, select, text

from powercontext.builtin.artifacts.profile.models import PROFILE_SOURCE_WINDOW_BINDING
from powercontext.builtin.artifacts.prompt import PromptKey
from powercontext.builtin.artifacts.prompt.builtin import builtin_prompt_definitions
from powercontext.builtin.artifacts.topic_memory import TOPIC_MEMORY_SOURCE_WINDOW_BINDING
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.processing_migration import bootstrap_processing_schema
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import (
    ARTIFACT_CANDIDATE_HEADS_TABLE,
    ARTIFACT_CANDIDATE_VERSIONS_TABLE,
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_PROCESSING_BINDING_STATES_TABLE,
    BUILTIN_TABLES,
    MEMORY_ENTRY_VERSIONS_TABLE,
    MODEL_USAGE_DAILY_TABLE,
    SCOPES_TABLE,
    SOURCE_JOURNAL_HEADS_TABLE,
)
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.runtime.config import BuiltinConfig, RuntimeConfig
from powercontext.builtin.runtime.family_processing import FAMILY_BINDINGS
from powercontext.builtin.runtime.models import SearchTopicMemoryRequest
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.authz import PrincipalRef
from powercontext.server.authz.repository import ACCESS_OWNERS_TABLE, ACCESS_TABLES
from powercontext.server.configuration import server_settings_context
from powercontext.server.processing_security import WorkerSecuritySpec
from powercontext.server.settings import ServerSettings

# This acceptance covers Source-driven processors; Dream has its own explicit-request acceptance.
BINDINGS = {
    **{family: FAMILY_BINDINGS[family] for family in ("memory", "experience", "profile")},
    "topic-memory": TOPIC_MEMORY_SOURCE_WINDOW_BINDING,
}
PROFILE_CONTROL_TITLES = {
    "absent": "Supervisor Profile absent-policy control",
    "disabled": "Supervisor Profile disabled-policy control",
}
CUSTOM_PROMPT_MARKERS: dict[PromptKey, str] = {
    "memory.extract": "ScopePromptMemoryAccepted",
    "experience.incubate": "ScopePromptExperienceAccepted",
}


class AcceptanceFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.details: dict[str, Any] = {}


class FailureCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.failures: list[dict[str, str]] = []

    def emit(self, record):
        if record.levelno >= logging.ERROR and "artifact_processing" in record.name:
            self.failures.append({
                "event": str(getattr(record, "event", "worker-error")),
                "error_code": str(getattr(record, "error_code", "unknown")),
                "exception_type": str(getattr(record, "exception_type", "unknown")),
                "traceback": str(getattr(record, "traceback", "")),
                "stage": str(getattr(record, "stage", "unknown")),
                "family": str(getattr(record, "family", "unknown")),
            })


def _database(config):
    if isinstance(config, SQLiteConfig):
        return SQLiteProfile.open(config, tables=(*BUILTIN_TABLES, *ACCESS_TABLES))
    if isinstance(config, SeekDBConfig):
        return SeekDBProfile.open(config, tables=(*BUILTIN_TABLES, *ACCESS_TABLES))
    return OceanBaseProfile.open(config, tables=(*BUILTIN_TABLES, *ACCESS_TABLES))


def _seekdb_path(directory: Path) -> Path:
    # Embedded SeekDB uses a Unix socket beneath its data path (108-byte limit).
    link = directory / "seekdb"
    if link.is_symlink() and not link.exists():
        raise AcceptanceFailure("seekdb-acceptance-database-no-longer-exists")
    if not link.exists():
        # Runtime-owned TMPDIR can disappear between invocations. Preserve the
        # dedicated data directory so a later --resume actually reopens it.
        link.symlink_to(tempfile.mkdtemp(prefix="pc-rfc1515-", dir="/tmp"), target_is_directory=True)
    return link.resolve()


async def _case_state(connection, database, scope: str) -> dict[str, Any]:
    version_sql = "SELECT sqlite_version()" if isinstance(database, SQLiteConfig) else "SELECT version()"
    version = str(await connection.scalar(text(version_sql)))
    rows = (
        await connection.execute(
            select(
                MODEL_USAGE_DAILY_TABLE.c.purpose,
                MODEL_USAGE_DAILY_TABLE.c.operation,
                func.sum(MODEL_USAGE_DAILY_TABLE.c.requests),
            )
            .where(MODEL_USAGE_DAILY_TABLE.c.scope_id == scope)
            .group_by(MODEL_USAGE_DAILY_TABLE.c.purpose, MODEL_USAGE_DAILY_TABLE.c.operation)
        )
    ).all()
    intents = [
        await ArtifactProcessingIntentRepository().load(connection, scope, binding) for binding in BINDINGS.values()
    ]
    return {
        "backend_version": version,
        "profile_scan_generation": int(
            await connection.scalar(
                select(ARTIFACT_PROCESSING_BINDING_STATES_TABLE.c.scan_generation).where(
                    ARTIFACT_PROCESSING_BINDING_STATES_TABLE.c.binding_name == PROFILE_SOURCE_WINDOW_BINDING
                )
            )
            or 0
        ),
        "model_requests": {f"{row[0]}/{row[1]}": int(row[2]) for row in rows},
        "source_journal_position": int(
            await connection.scalar(
                select(SOURCE_JOURNAL_HEADS_TABLE.c.position).where(SOURCE_JOURNAL_HEADS_TABLE.c.scope_id == scope)
            )
            or 0
        ),
        "all_requests_handled": all(
            row is not None and row.requested_generation > 0 and row.handled_generation >= row.requested_generation
            for row in intents
        ),
    }


async def _prepare_scope(
    database, config: BuiltinConfig, *, resume: bool, custom_prompts: bool, automatic_profile: bool
) -> tuple[str, dict[str, Any]]:
    async with _database(database) as profile, profile.database.transaction() as connection:
        count = await connection.scalar(select(func.count()).select_from(SCOPES_TABLE))
        if count and not resume:
            raise AcceptanceFailure("acceptance-requires-empty-database")
        if resume:
            scopes = (
                (
                    await connection.execute(
                        select(SCOPES_TABLE.c.scope_id).where(
                            SCOPES_TABLE.c.title == "Supervisor acceptance",
                            SCOPES_TABLE.c.summary == "Synthetic acceptance work",
                        )
                    )
                )
                .scalars()
                .all()
            )
            if len(scopes) != 1:
                raise AcceptanceFailure("resume-requires-one-acceptance-scope")
            return scopes[0], await _case_state(connection, database, scopes[0])
        await bootstrap_processing_schema(connection, canonical_processing_manifest(config))
    # Compose the public Runtime so Prompt capabilities follow the same real
    # adapters as production. Automatic admission is disabled in this fixture;
    # accepted work is published only after this setup Runtime has closed.
    setup_config = config.model_copy(
        update={"runtime": config.runtime.model_copy(update={"profile_schedule_enabled": False})}
    )
    async with open_builtin_runtime(setup_config) as setup_runtime:
        contexts = cast(RelationalContexts, setup_runtime._provider)
        scope = (
            await contexts.scopes.create(
                ScopeDraft(
                    title="Supervisor acceptance",
                    summary="Synthetic acceptance work",
                    idempotency_key=uuid4().hex,
                )
            )
        ).scope_id
        if custom_prompts:
            definitions = {
                item.key: item for item in builtin_prompt_definitions(config.runtime.memory_extraction_profile)
            }
            for key, marker in CUSTOM_PROMPT_MARKERS.items():
                field = "text" if key == "memory.extract" else "lesson"
                await contexts.records.create_artifact(
                    scope,
                    "prompt",
                    ArtifactWrite(
                        prompt_key=key,
                        content={
                            "schema_version": "powercontext.prompt.v1",
                            "mode": "custom",
                            "instructions": definitions[key].default_instructions
                            + f"\nFor this Scope, prefix every candidate {field} with [{marker}] "
                            "as a formatting label, then include the supported factual content.",
                            "demonstrations": [],
                        },
                    ),
                )
        await contexts.profiles.put_policy(
            scope, generation_enabled=True, activation_mode="review_required", expected_version=0
        )
        await contexts.records.capture_source(
            scope,
            "content",
            "supervisor-real-task-outcome",
            "Completed engineering task: a release failed because configuration changes bypassed validation. "
            "I added a strict configuration check before deployment; the check then passed and the release succeeded. "
            "Reusable lesson: always run the strict configuration test after changing configuration.",
            {"kind": "task-outcome", "synthetic": True},
        )
        await contexts.records.capture_source(
            scope,
            "content",
            "supervisor-real-user-profile",
            "I am the single user of this Scope. I have worked as a Python backend engineer since 2020. "
            "Simplified Chinese has been my preferred language for engineering discussions since 2024. "
            "I use pytest for Python regression tests and require executable test evidence before accepting changes. "
            "I prefer narrowly scoped code changes. These are my enduring personal work preferences, "
            "consistently confirmed across multiple years and projects, independent of the current release task.",
            {"kind": "profile-evidence", "role": "user", "synthetic": True},
        )
        if automatic_profile:
            await _prepare_profile_controls(contexts)
    async with (
        _database(database) as profile,
        profile.database.transaction() as connection,
    ):
        for binding in BINDINGS.values():
            if not automatic_profile or binding != PROFILE_SOURCE_WINDOW_BINDING:
                await ArtifactProcessingIntentRepository().request(connection, scope, binding)
        return scope, await _case_state(connection, database, scope)


async def _prepare_profile_controls(contexts: RelationalContexts) -> None:
    for policy, title in PROFILE_CONTROL_TITLES.items():
        control = (
            await contexts.scopes.create(
                ScopeDraft(title=title, summary="Synthetic eligibility control", idempotency_key=uuid4().hex)
            )
        ).scope_id
        if policy == "disabled":
            await contexts.profiles.put_policy(
                control, generation_enabled=False, activation_mode="review_required", expected_version=0
            )
        await contexts.records.capture_source(
            control,
            "content",
            f"supervisor-profile-{policy}-policy",
            "I prefer executable Python regression evidence before accepting engineering changes.",
            {"kind": "profile-evidence", "role": "user", "synthetic": True},
        )


async def _automatic_profile_state(contexts: RelationalContexts, supervisor) -> dict[str, Any]:
    """Observe durable cron progress and reject any admission of ineligible Scopes."""

    controls = {}
    async with contexts.database.transaction() as connection:
        for policy, title in PROFILE_CONTROL_TITLES.items():
            scope = await connection.scalar(select(SCOPES_TABLE.c.scope_id).where(SCOPES_TABLE.c.title == title))
            if scope is None:
                raise AcceptanceFailure("automatic-profile-control-scope-missing")
            intent = await ArtifactProcessingIntentRepository().load(connection, scope, PROFILE_SOURCE_WINDOW_BINDING)
            if intent is None or intent.dirty_generation <= intent.clean_generation:
                raise AcceptanceFailure("automatic-profile-control-dirty-input-lost")
            if intent.requested_generation or intent.handled_generation or intent.last_auto_scan_generation:
                raise AcceptanceFailure("automatic-profile-ineligible-scope-admitted")
            controllers = getattr(supervisor, "supervisors", (supervisor,))
            for controller in controllers:
                state = controller._families.get(PROFILE_SOURCE_WINDOW_BINDING)
                if state is not None and any(scope in items for items in (state.running, state.queued, state.retries)):
                    raise AcceptanceFailure("automatic-profile-ineligible-worker-or-retry")
            controls[policy] = {
                "requested_generation": intent.requested_generation,
                "handled_generation": intent.handled_generation,
                "last_auto_scan_generation": intent.last_auto_scan_generation,
                "dirty_generation": intent.dirty_generation,
                "clean_generation": intent.clean_generation,
                "observed_running_queued_or_retry": False,
            }
        table = ARTIFACT_PROCESSING_BINDING_STATES_TABLE
        scan = (
            (await connection.execute(select(table).where(table.c.binding_name == PROFILE_SOURCE_WINDOW_BINDING)))
            .mappings()
            .one_or_none()
        )
    return {
        "controls": controls,
        "scan_generation": 0 if scan is None else int(scan["scan_generation"]),
        "scan_in_progress": False if scan is None else bool(scan["scan_in_progress"]),
        "checkpoint": None if scan is None else str(scan["last_schedule_checkpoint_at"]),
    }


async def _custom_prompt_evidence(contexts: RelationalContexts, scope: str) -> dict[str, Any]:
    async with contexts.database.transaction() as connection:
        memory_texts = (
            (
                await connection.execute(
                    select(MEMORY_ENTRY_VERSIONS_TABLE.c.text).where(MEMORY_ENTRY_VERSIONS_TABLE.c.scope_id == scope)
                )
            )
            .scalars()
            .all()
        )
        experience_proposals = (
            (
                await connection.execute(
                    select(ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.proposal).where(
                        ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.scope_id == scope,
                        ARTIFACT_CANDIDATE_VERSIONS_TABLE.c.family == "experience",
                    )
                )
            )
            .scalars()
            .all()
        )
    observed = {
        "memory.extract": any(CUSTOM_PROMPT_MARKERS["memory.extract"] in str(value) for value in memory_texts),
        "experience.incubate": any(
            CUSTOM_PROMPT_MARKERS["experience.incubate"] in str(value) for value in experience_proposals
        ),
    }
    evidence = {}
    for key in CUSTOM_PROMPT_MARKERS:
        selected = await contexts.prompts.resolve(scope, key)
        if selected is None or selected.selection != "artifact" or not observed[key]:
            raise AcceptanceFailure("custom-prompt-not-executed-by-worker")
        evidence[key] = {
            "selection": selected.selection,
            "revision": selected.selected_version,
            "compiled_digest": selected.compiled_digest,
            "persisted_output_contains_custom_formatting_label": observed[key],
        }
    return evidence


async def run_acceptance(  # noqa: C901 - one bounded end-to-end acceptance lifecycle
    settings: ServerSettings,
    *,
    backend: str,
    directory: Path,
    mode: Literal["global", "dedicated"],
    resume: bool = False,
    custom_prompts: bool = False,
    automatic_profile: bool = False,
) -> dict[str, Any]:
    """Recover accepted work after reopening, then acknowledge a fresh NOOP."""

    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    inference = settings.inference
    if inference.generation_model is None or inference.generation_model == "test":
        raise AcceptanceFailure("real-generation-required")
    if inference.embedding_model is None or inference.embedding_model == "test":
        raise AcceptanceFailure("real-embedding-required")
    if backend == "sqlite":
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{(directory / 'runtime.db').resolve()}")
    elif backend == "seekdb":
        database = SeekDBConfig(path=await asyncio.to_thread(_seekdb_path, directory))
    else:
        if not isinstance(settings.database, OceanBaseConfig):
            raise AcceptanceFailure("dedicated-oceanbase-configuration-required")
        database = settings.database
    runtime_config = RuntimeConfig(
        artifact_processing_supervisor_mode=mode,
        artifact_processing_families=tuple(BINDINGS),
        source_window_limit=4,
        topic_memory_source_window_limit=4,
        memory_max_workers=1,
        experience_max_workers=1,
        profile_max_workers=1,
        profile_schedule_enabled=automatic_profile,
        profile_cron="* * * * *",
        topic_memory_max_workers=1,
    )
    config = BuiltinConfig(database=database, runtime=runtime_config, inference=inference)
    security = WorkerSecuritySpec(
        principal=PrincipalRef(type="service", id="supervisor-real-acceptance"),
        deployment_id="supervisor-real-acceptance",
        static_preset=True,
    ).model_dump(mode="json")
    scope, initial_state = await _prepare_scope(
        database, config, resume=resume, custom_prompts=custom_prompts, automatic_profile=automatic_profile
    )
    captured = FailureCapture()
    logger = logging.getLogger("powercontext.builtin.runtime.artifact_processing")
    logger.addHandler(captured)
    started = time.monotonic()
    intent_states: dict[str, dict[str, int]] = {}
    automatic_evidence: dict[str, Any] = {}
    completed_scans: dict[int, str] = {}
    required_scan_generation = initial_state["profile_scan_generation"] + (2 if resume else 3)

    def failed(code: str) -> AcceptanceFailure:
        error = AcceptanceFailure(code)
        error.details = {
            "worker_failures": captured.failures,
            "intent_generations": intent_states,
            "automatic_profile": automatic_evidence,
            "elapsed_seconds": round(time.monotonic() - started, 2),
        }
        return error

    try:
        async with open_builtin_runtime(config, worker_security=security) as runtime:
            contexts = cast(RelationalContexts, runtime._provider)
            supervisor = runtime.artifact_processing_supervisor
            if supervisor is None:
                raise failed("supervisor-not-registered")
            deadline = asyncio.get_running_loop().time() + 660
            while True:
                async with contexts.database.transaction() as connection:
                    rows = [
                        await ArtifactProcessingIntentRepository().load(connection, scope, binding)
                        for binding in BINDINGS.values()
                    ]
                intent_states = {
                    family: {
                        "requested": row.requested_generation,
                        "handled": row.handled_generation,
                        "dirty": row.dirty_generation,
                        "clean": row.clean_generation,
                    }
                    for family, row in zip(BINDINGS, rows, strict=True)
                    if row is not None
                }
                scans_complete = True
                if automatic_profile:
                    automatic_evidence = await _automatic_profile_state(contexts, supervisor)
                    if not automatic_evidence["scan_in_progress"] and automatic_evidence["scan_generation"]:
                        completed_scans[automatic_evidence["scan_generation"]] = automatic_evidence["checkpoint"]
                    scans_complete = (
                        automatic_evidence["scan_generation"] >= required_scan_generation
                        and not automatic_evidence["scan_in_progress"]
                    )
                if all(row is not None and row.handled_generation >= 1 for row in rows) and scans_complete:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise failed("accepted-work-timeout")
                await asyncio.sleep(0.25)
            async with contexts.database.transaction() as connection:
                artifact_rows = (
                    await connection.execute(
                        select(ARTIFACT_HEADS_TABLE.c.family, func.count())
                        .where(ARTIFACT_HEADS_TABLE.c.scope_id == scope)
                        .group_by(ARTIFACT_HEADS_TABLE.c.family)
                    )
                ).all()
                candidate_rows = (
                    await connection.execute(
                        select(ARTIFACT_CANDIDATE_HEADS_TABLE.c.family, func.count())
                        .where(ARTIFACT_CANDIDATE_HEADS_TABLE.c.scope_id == scope)
                        .group_by(ARTIFACT_CANDIDATE_HEADS_TABLE.c.family)
                    )
                ).all()
                ownership_rows = (
                    await connection.execute(
                        select(ACCESS_OWNERS_TABLE.c.family, func.count())
                        .where(ACCESS_OWNERS_TABLE.c.scope_id == scope)
                        .group_by(ACCESS_OWNERS_TABLE.c.family)
                    )
                ).all()
                ownership = {str(row[0]): int(row[1]) for row in ownership_rows}
                artifacts = {str(row[0]): int(row[1]) for row in artifact_rows}
                candidates = {str(row[0]): int(row[1]) for row in candidate_rows}
                cursors = {}
                for family, binding in BINDINGS.items():
                    cursor = await SourceCursorRepository().load(connection, scope, binding)
                    cursors[family] = 0 if cursor is None else cursor.cursor.sequence
            if artifacts.get("memory", 0) < 1 or artifacts.get("topic-memory", 0) < 1:
                raise failed("real-generation-produced-no-published-memory")
            if candidates.get("experience", 0) < 1 or candidates.get("profile", 0) != 1:
                raise failed("real-generation-did-not-preserve-review-candidates")
            if any(ownership.get(family, 0) < 1 for family in BINDINGS):
                raise failed("atomic-owner-attestation-contract-failed")
            if cursors["profile"] != 0 or any(
                cursors[family] != initial_state["source_journal_position"]
                for family in ("memory", "experience", "topic-memory")
            ):
                raise failed("domain-cursor-contract-failed")
            # Already pending Profile review is a successful invocation without
            # generating another Candidate or consuming its Source window.
            async with contexts.database.transaction() as connection:
                request = await ArtifactProcessingIntentRepository().request(
                    connection, scope, PROFILE_SOURCE_WINDOW_BINDING
                )
            supervisor.wake(PROFILE_SOURCE_WINDOW_BINDING)
            while True:
                async with contexts.database.transaction() as connection:
                    pending = await ArtifactProcessingIntentRepository().load(
                        connection, scope, PROFILE_SOURCE_WINDOW_BINDING
                    )
                if pending is not None and pending.handled_generation >= request.requested_generation:
                    break
                if asyncio.get_running_loop().time() >= deadline:
                    raise failed("pending-review-noop-failed")
                await asyncio.sleep(0.25)
            async with contexts.database.transaction() as connection:
                review_count = await connection.scalar(
                    select(func.count())
                    .select_from(ARTIFACT_CANDIDATE_HEADS_TABLE)
                    .where(
                        ARTIFACT_CANDIDATE_HEADS_TABLE.c.scope_id == scope,
                        ARTIFACT_CANDIDATE_HEADS_TABLE.c.family == "profile",
                    )
                )
                review_cursor = await SourceCursorRepository().load(connection, scope, PROFILE_SOURCE_WINDOW_BINDING)
                final_state = await _case_state(connection, database, scope)
                expected_ids = set(
                    (
                        await connection.execute(
                            select(ARTIFACT_HEADS_TABLE.c.artifact_id).where(
                                ARTIFACT_HEADS_TABLE.c.scope_id == scope,
                                ARTIFACT_HEADS_TABLE.c.family == "topic-memory",
                            )
                        )
                    ).scalars()
                )
            if review_count != 1 or (review_cursor is not None and review_cursor.cursor.sequence != 0):
                raise failed("pending-review-noop-consumed-domain-input")
            if automatic_profile:
                automatic_evidence = await _automatic_profile_state(contexts, supervisor)
                automatic_evidence.update({
                    "initial_scan_generation": initial_state["profile_scan_generation"],
                    "required_scan_generation": required_scan_generation,
                    "observed_completed_scans": completed_scans,
                    "cron": "* * * * *",
                    "two_cron_fires_completed": True,
                    "initial_profile_explicit_request": False,
                })
            replay_without_model_calls = resume and initial_state["all_requests_handled"]
            if replay_without_model_calls and initial_state["model_requests"] != final_state["model_requests"]:
                raise failed("completed-request-replay-made-model-calls")
            for usage_key in (
                "memory_extraction/generation",
                "memory_indexing/embedding",
                "experience_generation/generation",
                "topic_memory_generation/generation",
                "topic_memory_indexing/embedding",
            ):
                if final_state["model_requests"].get(usage_key, 0) < 1:
                    raise failed("real-model-usage-evidence-missing")
            prompt_evidence = await _custom_prompt_evidence(contexts, scope) if custom_prompts else {}
            search = await runtime.topic_memory.for_scope(scope).search(
                SearchTopicMemoryRequest(query="strict configuration validation release engineering", limit=10)
            )
            vector_hits = sum(any("vector" in channel for channel in hit.matched_by) for hit in search.hits)
            if (
                search.mode != "hybrid"
                or not vector_hits
                or any(hit.artifact_ref.artifact_id not in expected_ids for hit in search.hits)
            ):
                raise failed("real-hybrid-vector-retrieval-failed")
            return {
                "status": "PASS",
                "backend": backend,
                "mode": mode,
                "backend_version": final_state["backend_version"],
                "generation_model": config.inference.generation_model,
                "embedding_model": config.inference.embedding_model,
                "model_requests": final_state["model_requests"],
                "custom_prompts": prompt_evidence,
                "automatic_profile": automatic_evidence,
                "source_journal_position": initial_state["source_journal_position"],
                "all_requests_handled": final_state["all_requests_handled"],
                "resume_without_processing_model_calls": replay_without_model_calls,
                "retrieval": {
                    "mode": search.mode,
                    "hit_count": len(search.hits),
                    "vector_hit_count": vector_hits,
                    "all_hits_belong_to_expected_scope": True,
                },
                "real_generation": True,
                "real_embedding": True,
                "generation_timeout_seconds": config.inference.generation_timeout_seconds,
                "scope_timeout_seconds": 600,
                "accepted_work_recovered_after_reopen": True,
                "profile_pending_review_noop": True,
                "artifact_counts": artifacts,
                "candidate_counts": candidates,
                "cursors": cursors,
                "ownership_counts": ownership,
                "family_status": supervisor.family_status,
                "worker_failures": captured.failures,
                "recovered_worker_failures": len(captured.failures),
                "elapsed_seconds": round(time.monotonic() - started, 2),
            }
    finally:
        logger.removeHandler(captured)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", choices=("sqlite", "seekdb", "oceanbase"), required=True)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--report-file", type=Path, help="Write evidence separately while reusing an existing database."
    )
    parser.add_argument("--mode", choices=("global", "dedicated"), default="global")
    parser.add_argument("--resume", action="store_true", help="Recover the existing synthetic acceptance database.")
    parser.add_argument(
        "--custom-prompts", action="store_true", help="Verify Scope-owned Memory and Experience custom Prompts."
    )
    parser.add_argument(
        "--automatic-profile", action="store_true", help="Verify Profile Policy filtering across two real cron fires."
    )
    parser.add_argument("--generation-timeout-seconds", type=float)
    parser.add_argument("--oceanbase-url-env", default="POWERCONTEXT_TEST_OCEANBASE_URL")
    args = parser.parse_args()
    report: dict[str, Any]
    try:
        with server_settings_context(env_file=args.env_file) as settings:
            if args.generation_timeout_seconds is not None:
                inference = type(settings.inference).model_validate({
                    **settings.inference.model_dump(),
                    "generation_timeout_seconds": args.generation_timeout_seconds,
                })
                settings = settings.model_copy(update={"inference": inference})
            if args.backend == "oceanbase":
                import os

                url = os.environ.get(args.oceanbase_url_env)
                if not url:
                    raise AcceptanceFailure("dedicated-oceanbase-url-required")  # noqa: TRY301
                settings = settings.model_copy(update={"database": OceanBaseConfig(url=SecretStr(url))})
            report = asyncio.run(
                run_acceptance(
                    settings,
                    backend=args.backend,
                    directory=args.output,
                    mode=args.mode,
                    resume=args.resume,
                    custom_prompts=args.custom_prompts,
                    automatic_profile=args.automatic_profile,
                )
            )
    except Exception as error:
        report = {"status": "FAIL", "backend": args.backend, "error_type": type(error).__name__}
        if isinstance(error, AcceptanceFailure):
            report["error_code"] = str(error)
            report.update(error.details)
    if report["status"] == "PASS":
        report["runtime_closed"] = True
    args.output.mkdir(parents=True, exist_ok=True)
    report_file = args.report_file or args.output / "report.json"
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
