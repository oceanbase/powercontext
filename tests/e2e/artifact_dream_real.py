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

"""Opt-in real LLM/HTTP acceptance in disposable SQLite or OceanBase databases."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import multiprocessing
import socket
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import uvicorn
from dotenv import load_dotenv
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.oceanbase.profile import _register_official_dialect
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, RetireMemoryEntryRequest, RuntimeConfig, open_builtin_runtime
from powercontext.builtin.runtime.config import InferenceConfig
from powercontext.builtin.sources import ContentSource
from powercontext.client import PowerContextClient
from powercontext.client.errors import ServerResponseError
from powercontext.http import (
    ApproveArtifactCandidateRequest,
    CaptureContentSourceRequest,
    CreateDreamRunRequest,
    CreateScopeRequest,
    DreamOperation,
    DreamStatus,
    GetArtifactCandidateRequest,
    GetExperienceRequest,
    GetSkillRequest,
    ListDreamRunsRequest,
    MemoryCitation,
    PrepareContextRequest,
)
from powercontext.server.app import ServerApplication, create_app
from powercontext.server.settings import ServerSettings

ENV_FILE = Path(".env")
CASE = "sqlite-global"
OUTPUT = Path("dream-acceptance.json")
phase = "settings"
report: dict[str, dict[str, Any]] = {}


def progress(value: str) -> None:
    global phase
    phase = value
    print(value, flush=True)


def historical_task_evidence() -> tuple[str, ...]:
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE TABLE writes (record_id INTEGER PRIMARY KEY, request_key TEXT UNIQUE)")
        read_attempts = 0
        result = None
        before_changes = connection.total_changes
        for attempt in range(3):
            read_attempts += 1
            try:
                if attempt < 2:
                    raise TimeoutError("injected_read_timeout")  # noqa: TRY301 -- inject a failed read attempt.
                result = connection.execute("SELECT count(*) FROM writes").fetchone()[0]
            except TimeoutError:
                continue
        assert result == 0 and connection.total_changes == before_changes

        # A missing acknowledgement is injected after the first write is committed.
        for attempt in range(2):
            try:
                connection.execute("INSERT INTO writes(request_key) VALUES (NULL)")
                connection.commit()
                if attempt == 0:
                    raise TimeoutError("injected_lost_write_acknowledgement")  # noqa: TRY301 -- inject after commit.
            except TimeoutError:
                continue
        unkeyed_rows = connection.execute("SELECT count(*) FROM writes").fetchone()[0]
        assert unkeyed_rows == 2
        connection.execute("DELETE FROM writes")
        connection.commit()

        ids = []
        for attempt in range(3):
            try:
                connection.execute("INSERT OR IGNORE INTO writes(request_key) VALUES (?)", ("original-key",))
                connection.commit()
                ids.append(
                    connection.execute(
                        "SELECT record_id FROM writes WHERE request_key = ?", ("original-key",)
                    ).fetchone()[0]
                )
                if attempt == 0:
                    raise TimeoutError("injected_lost_write_acknowledgement")  # noqa: TRY301 -- inject after commit.
            except TimeoutError:
                continue
        keyed_rows = connection.execute("SELECT count(*) FROM writes").fetchone()[0]
        assert keyed_rows == 1 and len(set(ids)) == 1

    return (
        f"Local SQLite read task with fault injection: the first two read attempts raised an injected timeout; "
        f"attempt {read_attempts} returned count {result}. Observed database changes: 0. "
        "This validates bounded retries of this read-only operation, not arbitrary writes.",
        f"Local SQLite unkeyed-write task with fault injection: acknowledgement was lost after a committed insert. "
        f"The same operation was retried without an idempotency key. Measured row count: {unkeyed_rows}; "
        "expected row count: 1. The blind replay duplicated the write.",
        f"Local SQLite keyed-write task with fault injection: acknowledgement was lost after a committed insert. "
        f"Three attempts retained the original key, protected by a unique constraint and insert-or-ignore. "
        f"Measured row count: {keyed_rows}; distinct returned record identities: {len(set(ids))}. "
        "This supports retrying writes only with a verified deduplication contract.",
    )


class TaskMemory:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(kind="task_record", text=rendering, sources=(item,))
            for item in request.sources
            if isinstance(item, ContentSource)
            for rendering in (
                (item.content, "Another rendering of the same check: " + item.content)
                if item.name == "task-2"
                else (item.content,)
            )
        )


async def terminal(client: PowerContextClient, scope: str, run_id: str):
    async with asyncio.timeout(165):
        while True:
            run = await client.get_dream_run(scope, run_id)
            if run.status in {DreamStatus.SUCCEEDED, DreamStatus.FAILED}:
                return run
            await asyncio.sleep(0.5)


def background_entry(config, ready, stop, errors):
    logging.disable(logging.CRITICAL)

    async def run():
        async with open_builtin_runtime(config):
            ready.set()
            while not stop.is_set():  # noqa: ASYNC110 -- stop is a multiprocessing.Event shared with the API process.
                await asyncio.sleep(0.05)

    try:
        asyncio.run(run())
    except BaseException as error:
        errors.put(type(error).__name__)
        ready.set()
        raise SystemExit(1) from None


async def validate_backend(
    label: str, database, inference: InferenceConfig, *, mode="global", split=False, dream_generator=None
) -> None:
    progress(label + ": opening runtime")
    report.setdefault(label, {})
    configuration = BuiltinConfig(
        database=database,
        inference=inference,
        runtime=RuntimeConfig(
            artifact_processing_families=("experience", "skill"), artifact_processing_supervisor_mode=mode
        ),
    )
    process = None
    stop = None
    if split:
        context = multiprocessing.get_context("spawn")
        ready, stop, errors = context.Event(), context.Event(), context.Queue()
        background = configuration.model_copy(
            update={"runtime": configuration.runtime.model_copy(update={"artifact_processing_role": "background"})}
        )
        process = context.Process(target=background_entry, args=(background, ready, stop, errors))
        process.start()
        try:
            started = await asyncio.to_thread(ready.wait, 90)
            assert started and process.is_alive() and errors.empty(), "background_start_failed"
        except BaseException:
            stop.set()
            await asyncio.to_thread(process.join, 10)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 10)
            raise
        configuration = configuration.model_copy(
            update={
                "runtime": configuration.runtime.model_copy(update={"artifact_processing_role": "api"}),
                "inference": InferenceConfig(),
            }
        )
    try:
        await validate_runtime(label, configuration, dream_generator=dream_generator)
        report[label]["mode"] = mode
        report[label]["split_processes"] = split
    finally:
        if process is not None:
            assert stop is not None
            stop.set()
            await asyncio.to_thread(process.join, 30)
            if process.is_alive():
                process.terminate()
                await asyncio.to_thread(process.join, 10)
            report[label]["background_exit_code"] = process.exitcode
            assert process.exitcode == 0, "background_exit_failed"


async def validate_runtime(label, configuration, *, dream_generator=None):
    async with open_builtin_runtime(
        configuration, candidate_pipeline=TaskMemory(), dream_generator=dream_generator
    ) as runtime:
        app = create_app(application=cast(ServerApplication, runtime))
        original_handler = app.exception_handlers[Exception]

        async def diagnose(request, error):
            import traceback

            chain = []
            current = error
            while current is not None and len(chain) < 5:
                item = {"type": type(current).__name__}
                original = getattr(current, "orig", None)
                args = () if original is None else getattr(original, "args", ())
                if args and isinstance(args[0], int):
                    item["database_error_code"] = args[0]
                item["frames"] = [
                    {"file": frame.filename.rsplit("/", 1)[-1], "line": frame.lineno, "function": frame.name}
                    for frame in traceback.extract_tb(current.__traceback__)
                    if "/src/powercontext/" in frame.filename
                ]
                chain.append(item)
                current = current.__cause__
            print(json.dumps({"unexpected_error": chain}), flush=True)
            response = original_handler(request, error)
            return await response if inspect.isawaitable(response) else response

        app.add_exception_handler(Exception, diagnose)
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(128)
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False, lifespan="off"))
        serving = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if serving.done():
                        await serving
                    await asyncio.sleep(0.01)
            async with PowerContextClient(f"http://127.0.0.1:{port}", timeout=60) as client:
                scope = await client.create_scope(
                    CreateScopeRequest(
                        title="Dream validation",
                        summary="Local retry task execution with explicit fault injection",
                        idempotency_key="real-dream",
                    )
                )
                texts = historical_task_evidence()
                for index, content in enumerate(texts):
                    await client.capture_content_source(
                        CaptureContentSourceRequest(scope_id=scope.scope_id, source_id=f"task-{index}", content=content)
                    )
                await runtime.memory.for_scope(scope.scope_id).flush()
                entries = await runtime.memory.for_scope(scope.scope_id).list()
                citations = [
                    MemoryCitation.model_validate_json(entry.citation.model_dump_json()) for entry in entries.entries
                ]
                assert len(citations) == 4
                request = CreateDreamRunRequest(
                    operation=DreamOperation.REFINE_EXPERIENCE,
                    memory_citations=citations,
                    idempotency_key="memory-to-experience",
                )
                progress(
                    label
                    + ": generating Experience ("
                    + ("configured LLM" if dream_generator is None else "controlled generator")
                    + ")"
                )
                accepted = await client.create_dream_run(scope.scope_id, request)
                run = await terminal(client, scope.scope_id, accepted.run_id)
                report[label] = {"experience_run": run.model_dump(mode="json")}
                assert run.status == DreamStatus.SUCCEEDED and run.candidate is not None, (
                    f"run_result:{run.status}:{run.error}"
                )
                candidate = await client.get_artifact_candidate(
                    GetArtifactCandidateRequest(scope_id=scope.scope_id, candidate_id=run.candidate.candidate_id)
                )
                report[label]["experience_candidate"] = candidate.model_dump(mode="json")
                assert candidate.memory_citations and candidate.source_refs
                assert run.input_manifest is not None and len(run.input_manifest.root_groups) == 3
                approved = await client.approve_artifact_candidate(
                    ApproveArtifactCandidateRequest(
                        scope_id=scope.scope_id, candidate_id=candidate.candidate_id, expected_version=candidate.version
                    )
                )
                assert approved.result_artifact is not None
                experience = await client.get_experience(
                    GetExperienceRequest(scope_id=scope.scope_id, artifact=approved.result_artifact)
                )
                assert experience.memory_citations == candidate.memory_citations
                assert await client.create_dream_run(scope.scope_id, request) == run
                report[label]["experience"] = experience.model_dump(mode="json")
                followup = await client.prepare_context(
                    PrepareContextRequest(
                        scope_id=scope.scope_id, query="Retry a write whose commit acknowledgement was lost."
                    )
                )
                assert followup.content is not None and experience.artifact.artifact_id in followup.content
                report[label]["followup_context"] = followup.model_dump(mode="json")
                progress(
                    label
                    + ": deriving Skill ("
                    + ("configured LLM" if dream_generator is None else "controlled generator")
                    + ")"
                )
                skill_request = CreateDreamRunRequest(
                    operation=DreamOperation.DERIVE_SKILL,
                    artifacts=[experience.artifact],
                    idempotency_key="experience-to-skill",
                )
                skill_accepted = await client.create_dream_run(scope.scope_id, skill_request)
                skill_run = await terminal(client, scope.scope_id, skill_accepted.run_id)
                report[label]["skill_run"] = skill_run.model_dump(mode="json")
                assert skill_run.status == DreamStatus.SUCCEEDED and skill_run.candidate is not None, (
                    f"run_result:{skill_run.status}:{skill_run.error}"
                )
                skill_candidate = await client.get_artifact_candidate(
                    GetArtifactCandidateRequest(scope_id=scope.scope_id, candidate_id=skill_run.candidate.candidate_id)
                )
                report[label]["skill_candidate"] = skill_candidate.model_dump(mode="json")
                assert not skill_candidate.memory_citations and skill_candidate.artifact_refs == [experience.artifact]
                skill_approved = await client.approve_artifact_candidate(
                    ApproveArtifactCandidateRequest(
                        scope_id=scope.scope_id,
                        candidate_id=skill_candidate.candidate_id,
                        expected_version=skill_candidate.version,
                    )
                )
                assert skill_approved.result_artifact is not None
                skill = await client.get_skill(
                    GetSkillRequest(scope_id=scope.scope_id, artifact=skill_approved.result_artifact)
                )
                assert skill.content.package is not None
                assert len((await client.list_dream_runs(scope.scope_id, ListDreamRunsRequest())).runs) == 2
                report[label]["skill"] = skill.model_dump(mode="json")
                retired = next(
                    entry.citation
                    for entry in entries.entries
                    if entry.citation.entry_id == candidate.memory_citations[0].entry_id
                )
                await runtime.memory.for_scope(scope.scope_id).retire(RetireMemoryEntryRequest(citation=retired))
                rejected = CreateDreamRunRequest(
                    operation=DreamOperation.REFINE_EXPERIENCE,
                    memory_citations=[MemoryCitation.model_validate_json(retired.model_dump_json())],
                    idempotency_key="retired-entry",
                )
                try:
                    await client.create_dream_run(scope.scope_id, rejected)
                except ServerResponseError as error:
                    assert error.status_code == 422
                else:
                    raise AssertionError("retired_entry_was_accepted")
                report[label]["retirement_blocks_new_dream"] = True
                report[label]["passed"] = True
                progress(label + ": real HTTP workflow passed")
        finally:
            server.should_exit = True
            await asyncio.wait_for(serving, timeout=15)
            listener.close()


async def main() -> None:
    logging.disable(logging.CRITICAL)
    load_dotenv(ENV_FILE, override=True)
    settings = ServerSettings()
    values = settings.inference.model_dump()
    values.update(embedding_model=None, embedding_profile_id=None, embedding_dimension=None)
    inference = InferenceConfig.model_validate(values)
    assert inference.generation_model is not None
    label = CASE
    mode = "dedicated" if "dedicated" in label else "global"
    split = "split" in label
    if label.startswith("sqlite"):
        with tempfile.TemporaryDirectory(prefix="powercontext-dream-real-") as temporary:
            await validate_backend(
                label, SQLiteConfig(url=f"sqlite+aiosqlite:///{temporary}/dream.db"), inference, mode=mode
            )
        return
    assert isinstance(settings.database, OceanBaseConfig)
    configured_url = make_url(settings.database.url.get_secret_value())
    database_name = "pc_dream_e2e_" + uuid4().hex[:16]
    _register_official_dialect()
    engine = create_async_engine(configured_url.set(database=None), hide_parameters=True, echo=False)
    created = False
    try:
        async with engine.begin() as connection:
            await connection.execute(
                text(f"CREATE DATABASE `{database_name}` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_bin")
            )
        created = True
        isolated = OceanBaseConfig(
            url=SecretStr(configured_url.set(database=database_name).render_as_string(hide_password=False))
        )
        await validate_backend(label, isolated, inference, mode=mode, split=split)
    except BaseException as error:
        report.setdefault("failure", {"phase": phase, "type": type(error).__name__})
        raise
    finally:
        if created:
            async with engine.begin() as connection:
                await connection.execute(text(f"DROP DATABASE `{database_name}`"))
        await engine.dispose()
        report["cleanup"] = {"database_removed": created}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--case",
        required=True,
        choices=(
            "sqlite-global",
            "sqlite-dedicated",
            "oceanbase-global",
            "oceanbase-dedicated",
            "oceanbase-split-global",
            "oceanbase-split-dedicated",
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    ENV_FILE, CASE, OUTPUT = args.env_file, args.case, args.output
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    try:
        asyncio.run(main())
    except BaseException as error:
        report.setdefault("failure", {"phase": phase, "type": type(error).__name__})
        print(json.dumps(report["failure"]), flush=True)
        sys.exit(1)
    finally:
        OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
