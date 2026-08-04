"""Run an isolated real-Codex Experience-to-managed-Skill acceptance journey."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Never

import uvicorn
from dotenv import load_dotenv
from sqlalchemy import bindparam, text

from powercontext.builtin.artifacts.experience import ExperienceCandidateInput, ExperienceContent
from powercontext.builtin.artifacts.skill import CodexSkillRoot
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.runtime import DatabaseConfig, ExternalSkillsConfig, RuntimeConfig
from powercontext.builtin.sources import ContentSource
from powercontext.client import PowerContextClient, ServerResponseError
from powercontext.http import (
    ApproveArtifactCandidateRequest,
    ArtifactCandidate,
    ArtifactCandidatePage,
    ArtifactReference,
    CandidateFamily,
    CandidateStatus,
    CaptureContentSourceRequest,
    ExperienceArtifact,
    ExperienceProposal,
    ExternalSkillImportMode,
    ExternalSkillResolutionStatus,
    GeneratedCandidateResponse,
    GeneratedCandidateStatus,
    GenerateExperienceRequest,
    GenerateSkillRequest,
    GetArtifactCandidateRequest,
    GetExperienceRequest,
    GetSkillRequest,
    ImportExternalSkillRequest,
    ListArtifactCandidatesRequest,
    ListExternalSkillsRequest,
    MemoryMatchedBy,
    MemorySearchMode,
    PrepareContextRequest,
    ProposeExperienceRequest,
    ProposeSkillRequest,
    RejectArtifactCandidateRequest,
    RememberMemoryRequest,
    ResolveExternalSkillRequest,
    ScanExternalSkillsRequest,
    SearchMemoryRequest,
    SkillArtifact,
    SkillGenerationOrigin,
    SkillProposal,
    SkillValidationItem,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings
from powercontext.sources import Source, SourceRef

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / ".powercontext-e2e"
CONFIGURED_EXPERIENCE_SCHEDULE_SECONDS = 75.0
_HARNESS_OUTPUT_PATTERN = re.compile(r"^\d{8}T\d{6}Z-(?:configured-)?experience-skill$")
_HARNESS_SCOPE_PREFIXES = (
    "configured-real-memory:",
    "configured-real-experience-skill:",
    "configured-real-foreign:",
)
_SCOPE_TABLES = (
    "pc_memory_vector_entries",
    "pc_memory_entry_heads",
    "pc_memory_entry_versions",
    "pc_artifact_candidate_heads",
    "pc_artifact_candidate_versions",
    "pc_artifact_heads",
    "pc_artifact_lineage_artifacts",
    "pc_artifact_lineage_sources",
    "pc_artifacts",
    "pc_source_cursors",
    "pc_external_skill_registrations",
    "pc_sources",
    "pc_source_journal_heads",
)
PRODUCER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["situation", "action", "outcome", "lesson"],
    "properties": {
        "situation": {"type": "string"},
        "action": {"type": "string"},
        "outcome": {"type": "string"},
        "lesson": {"type": "string"},
    },
}
SKILL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "description", "instructions", "validation"],
    "properties": {
        "name": {"type": "string"},
        "description": {"type": "string"},
        "instructions": {"type": "string"},
        "validation": {"type": "array", "minItems": 1, "items": {"type": "string"}},
    },
}
CONSUMER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["result", "validation"],
    "properties": {
        "result": {"type": "string"},
        "validation": {"type": "string"},
    },
}


class TaskOutcomeExperiencePipeline:
    """Deterministically map the harness's typed Task Outcome into Review."""

    async def incubate(self, sources: tuple[Source, ...], /) -> tuple[ExperienceCandidateInput, ...]:
        candidates: list[ExperienceCandidateInput] = []
        for source in sources:
            if not isinstance(source, ContentSource) or source.metadata.get("kind") != "task-outcome":
                continue
            payload = json.loads(source.content)
            if not isinstance(payload, dict):
                _fail("real Codex Task Outcome payload is not an object")
            proposal = ExperienceContent.model_validate(payload.get("codex_result"))
            candidates.append(
                ExperienceCandidateInput(
                    proposal=proposal,
                    sources=(SourceRef(source_type="content", source_id=source.name),),
                )
            )
        return tuple(candidates)


class RealCodexE2EError(RuntimeError):
    """Raised when an observable real-Codex acceptance condition fails."""


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    name: str
    status: str
    duration_seconds: float
    evidence: tuple[str, ...]
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ConfiguredScopes:
    memory: str
    artifacts: str
    foreign: str

    @property
    def all(self) -> tuple[str, ...]:
        return (self.memory, self.artifacts, self.foreign)


@dataclass(frozen=True, slots=True)
class ConfiguredJourneyState:
    scopes: ConfiguredScopes
    memory_query: str
    experience_revisions: tuple[ExperienceArtifact, ...]
    skill_revisions: tuple[SkillArtifact, ...]
    rejected_candidate_ids: tuple[str, ...]
    external_skill_id: str
    external_skill_fingerprint: str


class Recorder:
    """Persist bounded synthetic evidence and a machine-readable report."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.directory.mkdir(parents=True)
        self.environment: dict[str, object] = {}
        self.cleanup: dict[str, object] = {}
        self.scenarios: list[ScenarioResult] = []

    def write_text(self, relative: str, value: str) -> Path:
        path = self.directory / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value, encoding="utf-8")
        return path

    def write_json(self, relative: str, value: object) -> Path:
        return self.write_text(relative, f"{json.dumps(value, ensure_ascii=False, indent=2)}\n")

    @contextmanager
    def scenario(self, name: str, *evidence: str) -> Iterator[None]:
        started = time.monotonic()
        print(f"[START] {name}", flush=True)
        try:
            yield
        except Exception as error:
            self.scenarios.append(
                ScenarioResult(
                    name=name,
                    status="failed",
                    duration_seconds=round(time.monotonic() - started, 3),
                    evidence=tuple(evidence),
                    detail=f"{type(error).__name__}: {error}",
                )
            )
            self.write_report()
            print(f"[FAIL] {name}: {error}", flush=True)
            raise
        duration = time.monotonic() - started
        self.scenarios.append(
            ScenarioResult(
                name=name,
                status="passed",
                duration_seconds=round(duration, 3),
                evidence=tuple(evidence),
            )
        )
        self.write_report()
        print(f"[PASS] {name} ({duration:.1f}s)", flush=True)

    def write_report(self) -> None:
        self.write_json(
            "report.json",
            {
                "schema": "powercontext.experience-skill-real-codex-e2e.v1",
                "environment": self.environment,
                "scenarios": [asdict(scenario) for scenario in self.scenarios],
                "cleanup": self.cleanup,
            },
        )


@dataclass(slots=True)
class RunningServer:
    server: uvicorn.Server
    thread: threading.Thread
    listener: socket.socket
    base_url: str

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=15)
        self.listener.close()
        if self.thread.is_alive():
            _fail("PowerContext Server did not stop")


def main(argv: Sequence[str] | None = None) -> int:  # noqa: C901 - one exception-safe harness lifecycle
    arguments = _arguments(argv)
    configured_settings: ServerSettings | None = None
    configured_scopes: ConfiguredScopes | None = None
    if arguments.configured:
        load_dotenv(arguments.env_file, override=False)
        configured_settings = ServerSettings()
        _validate_configured_settings(configured_settings)
        configured_scopes = _new_configured_scopes()
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    mode = "configured-experience-skill" if arguments.configured else "experience-skill"
    output_root = arguments.output_root.resolve()
    removed_existing_outputs = _remove_existing_harness_outputs(output_root) if arguments.purge_existing else 0
    recorder = Recorder(output_root / f"{run_id}-{mode}")
    codex = _required_executable("codex")
    git = _required_executable("git")
    real_codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).resolve()
    auth_file = real_codex_home / "auth.json"
    if not auth_file.is_file():
        _fail(f"real Codex auth is unavailable at {auth_file}")
    user_state_before = _user_state(real_codex_home)
    codex_home = tempfile.TemporaryDirectory(prefix="powercontext-experience-skill-codex-")
    isolated_home = Path(codex_home.name)
    isolated_auth = isolated_home / "auth.json"
    shutil.copyfile(auth_file, isolated_auth)
    isolated_auth.chmod(0o600)
    codex_environment = {**os.environ, "CODEX_HOME": str(isolated_home), "NO_COLOR": "1"}
    server: RunningServer | None = None
    configured_server_settings: ServerSettings | None = None

    recorder.environment.update({
        "codex_version": _run([codex, "--version"], cwd=PROJECT_ROOT).stdout.strip(),
        "branch": _run([git, "branch", "--show-current"], cwd=PROJECT_ROOT).stdout.strip(),
        "mode": "configured-real-services" if arguments.configured else "deterministic-runtime",
        "codex_home": "isolated temporary directory with a mode-0600 auth copy only",
        "scheduler": "isolated APScheduler SQLite sidecar",
        "preflight_output_directories_removed": removed_existing_outputs,
    })
    if configured_settings is None:
        recorder.environment.update({
            "runtime_generation_model": "not configured",
            "experience_incubation": "APScheduler with a deterministic typed Task Outcome adapter",
            "database": "isolated SQLite",
        })
    else:
        recorder.environment.update({
            "python": sys.version.split()[0],
            "sqlite": sqlite3.sqlite_version,
            "generation_model": configured_settings.inference.generation_model,
            "embedding_model": configured_settings.inference.embedding_model,
            "embedding_profile_id": configured_settings.inference.embedding_profile_id,
            "embedding_dimension": configured_settings.inference.embedding_dimension,
            "database": configured_settings.database.kind,
            "database_isolated": isinstance(configured_settings.database, SQLiteConfig),
            "experience_incubation": "APScheduler with the configured real generation model",
            "experience_schedule_seconds": CONFIGURED_EXPERIENCE_SCHEDULE_SECONDS,
        })
    recorder.write_report()
    try:
        if configured_settings is not None and arguments.purge_existing:
            recorder.environment["preflight_database_cleanup"] = asyncio.run(
                _purge_existing_harness_scopes(configured_settings.database)
            )
            recorder.write_report()
        repositories = _prepare_repositories(
            recorder.directory / "work",
            git,
            require_marker=configured_settings is None,
            validation_command="python test_config.py" if configured_settings is None else "python3 test_config.py",
        )
        if configured_settings is None:
            server = _start_server(
                recorder.directory / "runtime.db",
                recorder.directory / "scheduler.db",
            )
        else:
            external_skill = _prepare_external_skill(recorder.directory / "work" / "external-skills")
            configured_server_settings = _configured_server_settings(
                configured_settings,
                recorder.directory,
                external_skill.parent,
            )
            server = _start_configured_server(
                configured_server_settings,
                recorder.directory / "configured-scheduler.db",
            )
        recorder.environment["server_url"] = server.base_url
        recorder.write_report()
        if configured_settings is None:
            asyncio.run(
                _run_journey(
                    recorder=recorder,
                    codex=codex,
                    codex_environment=codex_environment,
                    repositories=repositories,
                    server_url=server.base_url,
                    timeout=arguments.codex_timeout,
                )
            )
        else:
            if configured_scopes is None or configured_server_settings is None:
                _fail("configured E2E state was not initialized")
            journey = asyncio.run(
                _run_configured_journey(
                    recorder=recorder,
                    codex=codex,
                    codex_environment=codex_environment,
                    repositories=repositories,
                    external_skill=external_skill,
                    scopes=configured_scopes,
                    server_url=server.base_url,
                    timeout=arguments.codex_timeout,
                    generation_timeout=configured_settings.inference.generation_timeout_seconds,
                )
            )
            server.stop()
            server = None
            server = _start_configured_server(
                _without_scheduled_processing(configured_server_settings),
                recorder.directory / "configured-scheduler.db",
            )
            asyncio.run(
                _verify_configured_restart(
                    database=configured_settings.database,
                    recorder=recorder,
                    state=journey,
                    server_url=server.base_url,
                    generation_timeout=configured_settings.inference.generation_timeout_seconds,
                )
            )
    finally:
        cleanup_errors: list[str] = []
        if server is not None:
            try:
                server.stop()
            except Exception as error:
                cleanup_errors.append(f"server: {error}")
        database_cleanup: dict[str, object] | None = None
        if configured_settings is not None and configured_scopes is not None and arguments.cleanup:
            try:
                database_cleanup = asyncio.run(
                    _purge_database_scopes(configured_settings.database, configured_scopes.all)
                )
            except Exception as error:
                cleanup_errors.append(f"database: {type(error).__name__}: {error}")
        codex_home_path = isolated_home
        codex_home.cleanup()
        user_state_after = _user_state(real_codex_home)
        restored = user_state_before == user_state_after and not codex_home_path.exists() and not cleanup_errors
        recorder.cleanup.update({
            "restored": restored,
            "user_codex_state_unchanged": user_state_before == user_state_after,
            "isolated_codex_home_removed": not codex_home_path.exists(),
            "server_stopped": server is None or not server.thread.is_alive(),
            "database": database_cleanup,
            "output_directory_removed": False,
            "errors": cleanup_errors,
        })
        recorder.write_report()
        if arguments.cleanup:
            try:
                shutil.rmtree(recorder.directory)
            except Exception as error:
                cleanup_errors.append(f"output: {type(error).__name__}: {error}")
            output_removed = not recorder.directory.exists()
            if not output_removed and not any(error.startswith("output:") for error in cleanup_errors):
                cleanup_errors.append("output: directory still exists after cleanup")
            recorder.cleanup["output_directory_removed"] = output_removed
        restored = restored and (not arguments.cleanup or bool(recorder.cleanup["output_directory_removed"]))
        recorder.cleanup["restored"] = restored and not cleanup_errors
        recorder.cleanup["errors"] = cleanup_errors
        if recorder.directory.exists():
            recorder.write_report()
        print(f"Cleanup audit: {json.dumps(recorder.cleanup, ensure_ascii=False)}", flush=True)
        if cleanup_errors or not restored:
            _fail(f"cleanup audit failed: {recorder.cleanup}")

    passed = sum(result.status == "passed" for result in recorder.scenarios)
    print(f"Real Codex Experience/Skill E2E passed {passed}/{len(recorder.scenarios)} scenarios")
    if arguments.cleanup:
        print("Evidence: removed after successful cleanup audit")
    else:
        print(f"Evidence: {recorder.directory}")
    return 0


async def _run_journey(
    *,
    recorder: Recorder,
    codex: Path,
    codex_environment: Mapping[str, str],
    repositories: Mapping[str, Path],
    server_url: str,
    timeout: int,
) -> None:
    scope_id = f"real-codex-experience-skill:{int(time.time())}"
    producer_schema = recorder.write_json("schemas/experience.json", PRODUCER_SCHEMA)
    skill_schema = recorder.write_json("schemas/skill.json", SKILL_SCHEMA)
    consumer_schema = recorder.write_json("schemas/consumer.json", CONSUMER_SCHEMA)
    async with PowerContextClient(server_url) as client:
        with recorder.scenario(
            "real Codex completes a task and emits typed Experience content",
            "sessions/01-producer.jsonl",
            "sessions/01-producer.last.json",
            "producer/check.json",
        ):
            producer = _run_codex(
                recorder=recorder,
                codex=codex,
                environment=codex_environment,
                name="01-producer",
                repository=repositories["producer"],
                prompt=(
                    "Fix the strict configuration fixture without changing test_config.py. Run python test_config.py. "
                    "Your final response must be JSON with situation, action, outcome, and lesson. Describe only "
                    "what you actually changed and validated; do not claim a check passed unless you ran it. After "
                    "the check succeeds, the outcome field must include the exact phrase python test_config.py passed."
                ),
                output_schema=producer_schema,
                sandbox="workspace-write",
                timeout=timeout,
            )
            check = _run([Path(shutil.which("python") or "python"), "test_config.py"], cwd=repositories["producer"])
            recorder.write_json("producer/check.json", {"returncode": check.returncode, "stdout": check.stdout})
            proposal = ExperienceProposal.model_validate(producer)
            _require("passed" in proposal.outcome.lower(), "Codex Experience outcome did not report the verified pass")

        with recorder.scenario(
            "APScheduler incubates an Experience Candidate that stays gated until Review approval",
            "api/experience-candidate.json",
            "api/experience-approved.json",
        ):
            evidence = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope_id,
                    source_id="task-outcome-producer",
                    content=json.dumps(
                        {
                            "codex_result": producer,
                            "check": {"command": "python test_config.py", "status": "passed"},
                            "diff": _git_stdout(repositories["producer"], "diff", "--"),
                        },
                        ensure_ascii=False,
                    ),
                    metadata={"kind": "task-outcome", "agent": "codex"},
                )
            )
            inbox = await _wait_for_experience_candidate(client, scope_id)
            experience_candidate = inbox.candidates[0]
            prepared = await client.prepare_context(PrepareContextRequest(scope_id=scope_id, query=proposal.lesson))
            _require(experience_candidate.result_artifact is None, "pending Experience allocated an Artifact")
            _require(inbox.candidates == [experience_candidate], "pending Experience is absent from Review Inbox")
            _require(
                experience_candidate.proposal == proposal, "scheduled Experience changed typed Task Outcome content"
            )
            _require(prepared.status == "empty", "pending Experience entered PreparedContext")
            approved_experience_candidate = await client.approve_artifact_candidate(
                ApproveArtifactCandidateRequest(
                    scope_id=scope_id,
                    candidate_id=experience_candidate.candidate_id,
                    expected_version=1,
                )
            )
            approved_experience_ref = _result_artifact(
                approved_experience_candidate,
                "Experience approval wrote no Artifact",
            )
            experience_v1 = await client.get_experience(
                GetExperienceRequest(
                    scope_id=scope_id,
                    artifact=approved_experience_ref,
                )
            )
            recorder.write_json("api/experience-candidate.json", experience_candidate.model_dump(mode="json"))
            recorder.write_json("api/experience-approved.json", experience_v1.model_dump(mode="json"))
            _require(experience_v1.source_refs == [evidence.source], "Experience lost exact task evidence")

        with recorder.scenario(
            "real Codex incubates a managed Skill from approved Experience",
            "sessions/02-skill-author.jsonl",
            "api/skill-candidate.json",
        ):
            skill_draft = _run_codex(
                recorder=recorder,
                codex=codex,
                environment=codex_environment,
                name="02-skill-author",
                repository=repositories["producer"],
                prompt=(
                    "Draft a portable Codex Skill from the approved Experience below. Return JSON only. The name must "
                    "be exactly strict-config-repair. Its instructions must tell the receiving agent to set config.json "
                    "mode to strict, create managed-skill-v1.txt containing exactly POWERCONTEXT_SKILL_V1 followed by a "
                    "newline, never edit test_config.py, and run python test_config.py. Include observable validation.\n\n"
                    f"Approved Experience:\n{experience_v1.model_dump_json(indent=2)}"
                ),
                output_schema=skill_schema,
                sandbox="read-only",
                timeout=timeout,
            )
            skill_proposal = _skill_proposal(skill_draft)
            _require(skill_proposal.name == "strict-config-repair", "Codex changed the required managed Skill name")
            skill_candidate = await client.propose_skill(
                ProposeSkillRequest(
                    scope_id=scope_id,
                    proposal=skill_proposal,
                    source_refs=[],
                    artifact_refs=[experience_v1.artifact],
                    reason="Incubated from one exact approved Experience Revision.",
                )
            )
            skill_inbox = await client.list_artifact_candidates(
                ListArtifactCandidatesRequest(scope_id=scope_id, family=CandidateFamily.SKILL)
            )
            prepared = await client.prepare_context(PrepareContextRequest(scope_id=scope_id, query=proposal.lesson))
            _require(skill_candidate.result_artifact is None, "pending managed Skill allocated an Artifact")
            _require(skill_inbox.candidates == [skill_candidate], "pending managed Skill is absent from Review Inbox")
            _require(prepared.status == "ready", "approved Experience did not enter PreparedContext")
            prepared_content = prepared.content
            if prepared_content is None:
                _fail("ready PreparedContext omitted content")
            _require('"kind":"experience"' in prepared_content, "PreparedContext omitted Experience kind")
            _require('"family":"skill"' not in prepared_content, "pending managed Skill entered PreparedContext")
            recorder.write_json("api/skill-candidate.json", skill_candidate.model_dump(mode="json"))

        with recorder.scenario(
            "Reviewer approves managed Skill with exact Experience lineage",
            "api/skill-approved.json",
        ):
            approved_skill_candidate = await client.approve_artifact_candidate(
                ApproveArtifactCandidateRequest(
                    scope_id=scope_id,
                    candidate_id=skill_candidate.candidate_id,
                    expected_version=1,
                )
            )
            approved_skill_ref = _result_artifact(approved_skill_candidate, "Skill approval wrote no Artifact")
            skill_v1 = await client.get_skill(GetSkillRequest(scope_id=scope_id, artifact=approved_skill_ref))
            recorder.write_json("api/skill-approved.json", skill_v1.model_dump(mode="json"))
            _require(skill_v1.artifact_refs == [experience_v1.artifact], "managed Skill lost Experience lineage")

        with recorder.scenario(
            "exact managed Skill projects into an isolated Codex repository",
            "projection/SKILL.md",
            "projection/powercontext.json",
        ):
            projection = repositories["consumer"] / ".agents" / "skills" / skill_v1.content.name
            _project_via_cli(
                server_url=server_url,
                scope_id=scope_id,
                skill=skill_v1,
                destination=projection,
                recorder=recorder,
            )
            recorder.write_text("projection/SKILL.md", (projection / "SKILL.md").read_text(encoding="utf-8"))
            recorder.write_text(
                "projection/powercontext.json",
                (projection / "powercontext.json").read_text(encoding="utf-8"),
            )

        with recorder.scenario(
            "next real Codex task explicitly uses the projected managed Skill",
            "sessions/03-consumer.jsonl",
            "sessions/03-consumer.last.json",
            "consumer/check.json",
        ):
            consumer = _run_codex(
                recorder=recorder,
                codex=codex,
                environment=codex_environment,
                name="03-consumer",
                repository=repositories["consumer"],
                prompt=(
                    "Use $strict-config-repair to repair this repository's strict configuration fixture. "
                    "Follow the Skill exactly, run its validation, then immediately return the required short JSON. "
                    "Set result to passed only if the validation command actually passed."
                ),
                output_schema=consumer_schema,
                sandbox="workspace-write",
                timeout=timeout,
            )
            _require("passed" in str(consumer.get("result", "")).lower(), "Codex did not report a successful result")
            check = _run([Path(shutil.which("python") or "python"), "test_config.py"], cwd=repositories["consumer"])
            recorder.write_json("consumer/check.json", {"returncode": check.returncode, "stdout": check.stdout})
            _require(
                (repositories["consumer"] / "managed-skill-v1.txt").read_text(encoding="utf-8")
                == "POWERCONTEXT_SKILL_V1\n",
                "real Codex did not apply the projected Skill's exact marker instruction",
            )

        with recorder.scenario(
            "later usage evidence evolves Experience and Skill through replacement Candidates",
            "api/experience-v2.json",
            "api/skill-v2.json",
        ):
            usage = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope_id,
                    source_id="skill-usage-consumer",
                    content=json.dumps(
                        {
                            "skill": skill_v1.artifact.model_dump(mode="json"),
                            "result": "passed",
                            "check": "python test_config.py",
                            "diff": _git_stdout(repositories["consumer"], "diff", "--"),
                        },
                        ensure_ascii=False,
                    ),
                    metadata={"kind": "skill-usage", "agent": "codex"},
                )
            )
            experience_v2 = await _replace_experience(client, scope_id, experience_v1, usage.source)
            skill_v2 = await _replace_skill(client, scope_id, skill_v1, usage.source)
            historical_experience = await client.get_experience(
                GetExperienceRequest(scope_id=scope_id, artifact=experience_v1.artifact)
            )
            historical_skill = await client.get_skill(GetSkillRequest(scope_id=scope_id, artifact=skill_v1.artifact))
            recorder.write_json("api/experience-v2.json", experience_v2.model_dump(mode="json"))
            recorder.write_json("api/skill-v2.json", skill_v2.model_dump(mode="json"))
            _require(experience_v2.artifact.revision == 2, "Experience replacement did not create Revision 2")
            _require(skill_v2.artifact.revision == 2, "Skill replacement did not create Revision 2")
            _require(historical_experience == experience_v1, "old Experience Revision is no longer readable")
            _require(historical_skill == skill_v1, "old Skill Revision is no longer readable")

        with recorder.scenario(
            "rejected managed Skill remains terminal and produces no Artifact",
            "api/rejected-skill.json",
        ):
            rejected_candidate = await client.propose_skill(
                ProposeSkillRequest(
                    scope_id=scope_id,
                    proposal=SkillProposal(
                        name="unsafe-skill",
                        description="Use for an intentionally rejected proposal.",
                        instructions="Delete unrelated files.",
                        validation=[SkillValidationItem("Unrelated files are absent.")],
                    ),
                    source_refs=[usage.source],
                    artifact_refs=[],
                )
            )
            rejected = await client.reject_artifact_candidate(
                RejectArtifactCandidateRequest(
                    scope_id=scope_id,
                    candidate_id=rejected_candidate.candidate_id,
                    expected_version=1,
                    reason="The proposal exceeds the task authority boundary.",
                )
            )
            exact = await client.get_artifact_candidate(
                GetArtifactCandidateRequest(scope_id=scope_id, candidate_id=rejected.candidate_id)
            )
            rejected_page = await client.list_artifact_candidates(
                ListArtifactCandidatesRequest(
                    scope_id=scope_id,
                    family=CandidateFamily.SKILL,
                    status=CandidateStatus.REJECTED,
                )
            )
            recorder.write_json("api/rejected-skill.json", rejected.model_dump(mode="json"))
            _require(rejected.result_artifact is None, "rejected managed Skill produced an Artifact")
            _require(exact == rejected and rejected in rejected_page.candidates, "rejected Candidate is not auditable")


async def _run_configured_journey(
    *,
    recorder: Recorder,
    codex: Path,
    codex_environment: Mapping[str, str],
    repositories: Mapping[str, Path],
    external_skill: Path,
    scopes: ConfiguredScopes,
    server_url: str,
    timeout: int,
    generation_timeout: float,
) -> ConfiguredJourneyState:
    memory_scope = scopes.memory
    artifact_scope = scopes.artifacts
    foreign_scope = scopes.foreign
    memory_query = "How should an overly lenient configuration be corrected and verified?"
    producer_schema = recorder.write_json("schemas/experience.json", PRODUCER_SCHEMA)
    consumer_schema = recorder.write_json("schemas/consumer.json", CONSUMER_SCHEMA)
    generation_wait = max(
        120.0,
        generation_timeout + CONFIGURED_EXPERIENCE_SCHEDULE_SECONDS + 30.0,
    )

    async with PowerContextClient(server_url, timeout=max(30.0, generation_wait)) as client:
        with recorder.scenario(
            "configured embedding model and database support vector and hybrid Memory retrieval",
            "api/capabilities.json",
            "api/memory-remembered.json",
            "api/memory-vector-search.json",
            "api/memory-hybrid-search.json",
        ):
            capabilities = await client.get_capabilities()
            _require(capabilities.memory_extraction, "configured generation model did not enable Memory extraction")
            _require(capabilities.experience_generation, "configured Experience generation is unavailable")
            _require(capabilities.managed_skill_generation, "configured managed Skill generation is unavailable")
            _require(capabilities.external_skill_registry, "configured external Skill Registry is unavailable")
            _require(MemorySearchMode.VECTOR in capabilities.search_modes, "configured vector search is unavailable")
            _require(MemorySearchMode.HYBRID in capabilities.search_modes, "configured hybrid search is unavailable")
            remembered = await client.remember_memory(
                RememberMemoryRequest(
                    scope_id=memory_scope,
                    kind="validated-procedure",
                    text=(
                        "For the strict configuration fixture, change config.json mode from permissive to strict, "
                        "leave test_config.py unchanged, and run python3 test_config.py."
                    ),
                    reason="Seed one exact semantic-retrieval fact for configured E2E validation.",
                )
            )
            _require(remembered.entry is not None, "remember_memory did not persist a Memory entry")
            vector = await client.search_memory(
                SearchMemoryRequest(
                    scope_id=memory_scope,
                    query=memory_query,
                    limit=5,
                    mode=MemorySearchMode.VECTOR,
                )
            )
            hybrid = await client.search_memory(
                SearchMemoryRequest(
                    scope_id=memory_scope,
                    query="strict configuration validation",
                    limit=5,
                    mode=MemorySearchMode.HYBRID,
                )
            )
            _require(vector.mode is not None and vector.mode.value == "vector", "vector request used another mode")
            _require(bool(vector.hits), "configured embedding/vector database returned no semantic hit")
            _require(MemoryMatchedBy.VECTOR in vector.hits[0].matched_by, "top semantic hit was not vector-matched")
            _require(hybrid.mode is not None and hybrid.mode.value == "hybrid", "hybrid request used another mode")
            _require(bool(hybrid.hits), "configured hybrid database returned no hit")
            recorder.write_json("api/capabilities.json", capabilities.model_dump(mode="json"))
            recorder.write_json("api/memory-remembered.json", remembered.model_dump(mode="json"))
            recorder.write_json("api/memory-vector-search.json", vector.model_dump(mode="json"))
            recorder.write_json("api/memory-hybrid-search.json", hybrid.model_dump(mode="json"))

        with recorder.scenario(
            "real Codex completes and independently validates the producer task",
            "sessions/01-configured-producer.jsonl",
            "sessions/01-configured-producer.last.json",
            "producer/check.json",
        ):
            producer = _run_codex(
                recorder=recorder,
                codex=codex,
                environment=codex_environment,
                name="01-configured-producer",
                repository=repositories["producer"],
                prompt=(
                    "Fix this repository's strict configuration fixture without changing test_config.py. "
                    "Run python3 test_config.py. Return the required JSON from observed facts only. The outcome must "
                    "include the exact phrase python3 test_config.py passed only after that command passes."
                ),
                output_schema=producer_schema,
                sandbox="workspace-write",
                timeout=timeout,
            )
            producer_check = _run(
                [Path(sys.executable), "test_config.py"],
                cwd=repositories["producer"],
            )
            proposal = ExperienceProposal.model_validate(producer)
            _require("python3 test_config.py passed" in proposal.outcome, "producer omitted verified command outcome")
            _require(
                json.loads((repositories["producer"] / "config.json").read_text(encoding="utf-8"))
                == {"mode": "strict"},
                "real Codex did not make the required producer change",
            )
            _require(
                not _git_stdout(repositories["producer"], "diff", "--", "test_config.py"), "Codex changed the test"
            )
            recorder.write_json(
                "producer/check.json",
                {"returncode": producer_check.returncode, "stdout": producer_check.stdout},
            )

        with recorder.scenario(
            "configured LLM and APScheduler incubate one gated Experience Candidate",
            "api/experience-source.json",
            "api/experience-candidate.json",
            "api/experience-approved.json",
        ):
            task_outcome = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=artifact_scope,
                    source_id="configured-producer-task-outcome",
                    content=json.dumps(
                        {
                            "task": "Repair a repository whose config.json is permissive but must be strict.",
                            "agent_result": producer,
                            "observed_change": {"file": "config.json", "before": "permissive", "after": "strict"},
                            "constraints": ["test_config.py was not changed"],
                            "validation": {
                                "command": "python3 test_config.py",
                                "status": "passed",
                                "stdout": producer_check.stdout.strip(),
                            },
                            "diff": _git_stdout(repositories["producer"], "diff", "--"),
                        },
                        ensure_ascii=False,
                    ),
                    metadata={"kind": "task-outcome", "agent": "codex", "validation_status": "passed"},
                )
            )
            inbox = await _wait_for_experience_candidate(
                client,
                artifact_scope,
                timeout_seconds=generation_wait,
            )
            experience_candidate = inbox.candidates[0]
            prepared = await client.prepare_context(
                PrepareContextRequest(scope_id=artifact_scope, query="repair permissive configuration")
            )
            _require(experience_candidate.result_artifact is None, "pending Experience allocated an Artifact")
            _require(experience_candidate.source_refs == [task_outcome.source], "Experience lost exact task lineage")
            _require(prepared.status.value == "empty", "pending Experience entered Memory-only PreparedContext")
            approved = await client.approve_artifact_candidate(
                ApproveArtifactCandidateRequest(
                    scope_id=artifact_scope,
                    candidate_id=experience_candidate.candidate_id,
                    expected_version=experience_candidate.version,
                )
            )
            experience_v1 = await client.get_experience(
                GetExperienceRequest(
                    scope_id=artifact_scope,
                    artifact=_result_artifact(approved, "Experience approval wrote no Artifact"),
                )
            )
            _require(experience_v1.source_refs == [task_outcome.source], "approved Experience lost exact SourceRef")
            recorder.write_json("api/experience-source.json", task_outcome.model_dump(mode="json"))
            recorder.write_json("api/experience-candidate.json", experience_candidate.model_dump(mode="json"))
            recorder.write_json("api/experience-approved.json", experience_v1.model_dump(mode="json"))

        with recorder.scenario(
            "exact evidence lookup rejects cross-scope Experience generation before inference",
            "api/foreign-scope-source.json",
            "api/cross-scope-generation-error.json",
        ):
            foreign_source = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=foreign_scope,
                    source_id="foreign-task-outcome",
                    content="A Source that must remain inaccessible from the primary artifact scope.",
                    metadata={"kind": "scope-isolation-probe"},
                )
            )
            try:
                await client.generate_experience(
                    GenerateExperienceRequest(
                        scope_id=artifact_scope,
                        source_refs=[foreign_source.source],
                        artifact_refs=[],
                        reason="This request must fail before calling the configured model.",
                    )
                )
            except ServerResponseError as error:
                _require(error.status_code == 422, "cross-scope evidence returned an unexpected HTTP status")
                _require(error.code == "invalid_request", "cross-scope evidence returned an unexpected error code")
                recorder.write_json(
                    "api/cross-scope-generation-error.json",
                    {"status_code": error.status_code, "code": error.code},
                )
            else:
                _fail("Experience generation resolved evidence from another scope")
            pending = await client.list_artifact_candidates(
                ListArtifactCandidatesRequest(scope_id=artifact_scope, family=CandidateFamily.EXPERIENCE)
            )
            _require(not pending.candidates, "cross-scope generation persisted a Candidate")
            recorder.write_json("api/foreign-scope-source.json", foreign_source.model_dump(mode="json"))

        with recorder.scenario(
            "configured LLM generates and Review approves an Experience-backed managed Skill",
            "api/skill-candidate.json",
            "api/skill-approved.json",
        ):
            generated_skill = await client.generate_skill(
                GenerateSkillRequest(
                    scope_id=artifact_scope,
                    origin=SkillGenerationOrigin.EXPERIENCE,
                    source_refs=[task_outcome.source],
                    artifact_refs=[experience_v1.artifact],
                    reason=(
                        "Convert one approved, validated Experience and its exact Task Outcome into a portable "
                        "managed Skill."
                    ),
                )
            )
            skill_candidate = _required_generated_candidate(
                generated_skill,
                "configured model returned no reusable managed Skill",
            )
            recorder.write_json("api/skill-candidate.json", skill_candidate.model_dump(mode="json"))
            _require(skill_candidate.result_artifact is None, "pending managed Skill allocated an Artifact")
            _require(
                skill_candidate.artifact_refs == [experience_v1.artifact],
                "generated managed Skill lost exact Experience lineage",
            )
            _require(
                skill_candidate.source_refs == [task_outcome.source],
                "generated managed Skill lost exact Task Outcome lineage",
            )
            skill_text = skill_candidate.proposal.model_dump_json().lower()
            for required_text in ("config.json", "strict", "test_config.py"):
                _require(required_text in skill_text, f"generated managed Skill omitted {required_text}")
            approved = await client.approve_artifact_candidate(
                ApproveArtifactCandidateRequest(
                    scope_id=artifact_scope,
                    candidate_id=skill_candidate.candidate_id,
                    expected_version=skill_candidate.version,
                )
            )
            skill_v1 = await client.get_skill(
                GetSkillRequest(
                    scope_id=artifact_scope,
                    artifact=_result_artifact(approved, "managed Skill approval wrote no Artifact"),
                )
            )
            _require(skill_v1.artifact_refs == [experience_v1.artifact], "approved Skill lost Experience lineage")
            _require(skill_v1.source_refs == [task_outcome.source], "approved Skill lost Task Outcome lineage")
            recorder.write_json("api/skill-approved.json", skill_v1.model_dump(mode="json"))

        with recorder.scenario(
            "approval-time Skill lineage validator rejects invalid create and replacement lineage",
            "api/invalid-skill-lineage-candidate.json",
            "api/invalid-skill-lineage-rejected.json",
            "api/invalid-skill-replacement-candidate.json",
            "api/invalid-skill-replacement-rejected.json",
        ):
            invalid_lineage = await client.propose_skill(
                ProposeSkillRequest(
                    scope_id=artifact_scope,
                    proposal=SkillProposal(
                        name="invalid-skill-derived-copy",
                        description="A deliberately invalid new managed Skill lineage fixture.",
                        instructions="Copy another managed Skill without approved Experience evidence.",
                        validation=[SkillValidationItem("Approval must reject this lineage.")],
                    ),
                    source_refs=[],
                    artifact_refs=[skill_v1.artifact],
                    reason="Exercise the approval-time managed Skill lineage validator.",
                )
            )
            rejected_lineage = await _reject_after_invalid_skill_approval(
                client,
                scope_id=artifact_scope,
                candidate=invalid_lineage,
                rejection_reason="New managed Skills may reference only approved Experience Artifacts.",
                accepted_message="approval accepted a new managed Skill derived from another managed Skill",
            )
            invalid_replacement = await client.propose_skill(
                ProposeSkillRequest(
                    scope_id=artifact_scope,
                    proposal=skill_v1.content,
                    source_refs=[],
                    artifact_refs=[skill_v1.artifact],
                    target=skill_v1.artifact,
                    reason="Exercise replacement lineage validation without bounded usage evidence.",
                )
            )
            rejected_replacement = await _reject_after_invalid_skill_approval(
                client,
                scope_id=artifact_scope,
                candidate=invalid_replacement,
                rejection_reason="A managed Skill replacement requires direct bounded Source evidence.",
                accepted_message="approval accepted a managed Skill replacement without bounded Source evidence",
            )
            prepared = await client.prepare_context(
                PrepareContextRequest(scope_id=artifact_scope, query="invalid skill derived copy")
            )
            rejected_page = await client.list_artifact_candidates(
                ListArtifactCandidatesRequest(
                    scope_id=artifact_scope,
                    family=CandidateFamily.SKILL,
                    status=CandidateStatus.REJECTED,
                )
            )
            _require(prepared.status.value == "empty", "rejected Skill content entered PreparedContext")
            _require(
                rejected_lineage in rejected_page.candidates and rejected_replacement in rejected_page.candidates,
                "rejected lineage Candidates are not auditable",
            )
            recorder.write_json("api/invalid-skill-lineage-candidate.json", invalid_lineage.model_dump(mode="json"))
            recorder.write_json(
                "api/invalid-skill-lineage-rejected.json",
                rejected_lineage.model_dump(mode="json"),
            )
            recorder.write_json(
                "api/invalid-skill-replacement-candidate.json",
                invalid_replacement.model_dump(mode="json"),
            )
            recorder.write_json(
                "api/invalid-skill-replacement-rejected.json",
                rejected_replacement.model_dump(mode="json"),
            )

        with recorder.scenario(
            "exact managed Skill projects into an isolated Codex repository",
            "projection/SKILL.md",
            "projection/powercontext.json",
        ):
            projection = repositories["consumer"] / ".agents" / "skills" / skill_v1.content.name
            _project_via_cli(
                server_url=server_url,
                scope_id=artifact_scope,
                skill=skill_v1,
                destination=projection,
                recorder=recorder,
            )
            recorder.write_text("projection/SKILL.md", (projection / "SKILL.md").read_text(encoding="utf-8"))
            recorder.write_text(
                "projection/powercontext.json",
                (projection / "powercontext.json").read_text(encoding="utf-8"),
            )

        with recorder.scenario(
            "a second real Codex task explicitly reuses the projected managed Skill",
            "sessions/02-configured-consumer.jsonl",
            "sessions/02-configured-consumer.last.json",
            "consumer/check.json",
        ):
            consumer = _run_codex(
                recorder=recorder,
                codex=codex,
                environment=codex_environment,
                name="02-configured-consumer",
                repository=repositories["consumer"],
                prompt=(
                    f"Use ${skill_v1.content.name} to complete this repository's fixture. Follow the projected Skill "
                    "rather than inventing a separate procedure, run its validation, and return the required JSON. "
                    "Set result to passed only after the validation command succeeds."
                ),
                output_schema=consumer_schema,
                sandbox="workspace-write",
                timeout=timeout,
            )
            _require("passed" in str(consumer.get("result", "")).lower(), "consumer did not report a passed task")
            consumer_check = _run([Path(sys.executable), "test_config.py"], cwd=repositories["consumer"])
            _require(
                json.loads((repositories["consumer"] / "config.json").read_text(encoding="utf-8"))
                == {"mode": "strict"},
                "consumer did not apply the managed Skill procedure",
            )
            _require(not _git_stdout(repositories["consumer"], "diff", "--", "test_config.py"), "consumer changed test")
            recorder.write_json(
                "consumer/check.json",
                {"returncode": consumer_check.returncode, "stdout": consumer_check.stdout},
            )

        with recorder.scenario(
            "two real Codex task boundaries produce one multi-source Experience Candidate",
            "api/usage-source.json",
            "api/cross-task-experience-candidate.json",
            "api/cross-task-experience-approved.json",
        ):
            usage = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=artifact_scope,
                    source_id="configured-managed-skill-usage",
                    content=json.dumps(
                        {
                            "managed_skill": skill_v1.artifact.model_dump(mode="json"),
                            "task_boundary": "A second isolated repository and a separate real Codex session.",
                            "result": consumer,
                            "observed_change": {"file": "config.json", "after": "strict"},
                            "validation": {
                                "command": "python3 test_config.py",
                                "status": "passed",
                                "stdout": consumer_check.stdout.strip(),
                            },
                            "reusable_improvement": (
                                "A replacement should explicitly require preserving the validation command and its "
                                "observed result in the task result for later audit."
                            ),
                            "diff": _git_stdout(repositories["consumer"], "diff", "--"),
                        },
                        ensure_ascii=False,
                    ),
                    metadata={"kind": "skill-usage", "agent": "codex", "validation_status": "passed"},
                )
            )
            generated_cross_task = await client.generate_experience(
                GenerateExperienceRequest(
                    scope_id=artifact_scope,
                    source_refs=[task_outcome.source, usage.source],
                    artifact_refs=[],
                    reason="Synthesize the procedure observed in two independent real Codex task boundaries.",
                )
            )
            cross_task_candidate = _required_generated_candidate(
                generated_cross_task,
                "configured model returned no cross-task Experience",
            )
            _require(cross_task_candidate.target is None, "cross-task create unexpectedly targeted an Artifact")
            _require(
                cross_task_candidate.source_refs == [task_outcome.source, usage.source],
                "cross-task Experience lost one or reordered exact SourceRefs",
            )
            _require(
                cross_task_candidate.result_artifact is None, "pending cross-task Experience allocated an Artifact"
            )
            approved = await client.approve_artifact_candidate(
                ApproveArtifactCandidateRequest(
                    scope_id=artifact_scope,
                    candidate_id=cross_task_candidate.candidate_id,
                    expected_version=cross_task_candidate.version,
                )
            )
            cross_task_experience = await client.get_experience(
                GetExperienceRequest(
                    scope_id=artifact_scope,
                    artifact=_result_artifact(approved, "cross-task Experience approval wrote no Artifact"),
                )
            )
            _require(
                cross_task_experience.artifact.artifact_id != experience_v1.artifact.artifact_id,
                "cross-task create reused an existing Artifact identity without an explicit target",
            )
            _require(
                all(
                    source.source_id not in cross_task_experience.artifact.artifact_id
                    for source in cross_task_experience.source_refs
                ),
                "Experience Artifact identity was derived from a task Source identity",
            )
            recorder.write_json("api/usage-source.json", usage.model_dump(mode="json"))
            recorder.write_json(
                "api/cross-task-experience-candidate.json",
                cross_task_candidate.model_dump(mode="json"),
            )
            recorder.write_json(
                "api/cross-task-experience-approved.json",
                cross_task_experience.model_dump(mode="json"),
            )

        with recorder.scenario(
            "configured LLM evolves Experience and Skill from exact cross-task usage evidence",
            "api/experience-v2.json",
            "api/skill-v2.json",
        ):
            generated_experience_v2 = await client.generate_experience(
                GenerateExperienceRequest(
                    scope_id=artifact_scope,
                    source_refs=[usage.source],
                    artifact_refs=[experience_v1.artifact],
                    target=experience_v1.artifact,
                    reason="Cross-task use adds observed reuse evidence to the original Experience.",
                )
            )
            experience_v2_candidate = _required_generated_candidate(
                generated_experience_v2,
                "configured model returned no Experience replacement",
            )
            approved = await client.approve_artifact_candidate(
                ApproveArtifactCandidateRequest(
                    scope_id=artifact_scope,
                    candidate_id=experience_v2_candidate.candidate_id,
                    expected_version=experience_v2_candidate.version,
                )
            )
            experience_v2 = await client.get_experience(
                GetExperienceRequest(
                    scope_id=artifact_scope,
                    artifact=_result_artifact(approved, "Experience replacement approval wrote no Artifact"),
                )
            )
            generated_skill_v2 = await client.generate_skill(
                GenerateSkillRequest(
                    scope_id=artifact_scope,
                    origin=SkillGenerationOrigin.USAGE,
                    source_refs=[usage.source],
                    artifact_refs=[skill_v1.artifact],
                    target=skill_v1.artifact,
                    reason="Preserve the observed validation evidence requirement from real reuse.",
                )
            )
            skill_v2_candidate = _required_generated_candidate(
                generated_skill_v2,
                "configured model returned no managed Skill replacement",
            )
            approved = await client.approve_artifact_candidate(
                ApproveArtifactCandidateRequest(
                    scope_id=artifact_scope,
                    candidate_id=skill_v2_candidate.candidate_id,
                    expected_version=skill_v2_candidate.version,
                )
            )
            skill_v2 = await client.get_skill(
                GetSkillRequest(
                    scope_id=artifact_scope,
                    artifact=_result_artifact(approved, "managed Skill replacement approval wrote no Artifact"),
                )
            )
            historical_experience = await client.get_experience(
                GetExperienceRequest(scope_id=artifact_scope, artifact=experience_v1.artifact)
            )
            historical_skill = await client.get_skill(
                GetSkillRequest(scope_id=artifact_scope, artifact=skill_v1.artifact)
            )
            _require(experience_v2.artifact.revision == 2, "Experience replacement did not create Revision 2")
            _require(skill_v2.artifact.revision == 2, "managed Skill replacement did not create Revision 2")
            _require(experience_v2.source_refs == [usage.source], "Experience replacement lost usage SourceRef")
            _require(experience_v2.artifact_refs == [experience_v1.artifact], "Experience lost predecessor lineage")
            _require(skill_v2.source_refs == [usage.source], "Skill replacement lost usage SourceRef")
            _require(skill_v2.artifact_refs == [skill_v1.artifact], "Skill replacement lost predecessor lineage")
            _require(historical_experience == experience_v1, "old Experience Revision is no longer exactly readable")
            _require(historical_skill == skill_v1, "old Skill Revision is no longer exactly readable")
            recorder.write_json("api/experience-v2.json", experience_v2.model_dump(mode="json"))
            recorder.write_json("api/skill-v2.json", skill_v2.model_dump(mode="json"))

        with recorder.scenario(
            "configured LLM preserves contradictory outcome evidence in an explicit Experience replacement",
            "api/conflicting-outcome-source.json",
            "api/conflicting-experience-candidate.json",
            "api/experience-v3-conflict.json",
        ):
            conflicting_outcome = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=artifact_scope,
                    source_id="configured-read-only-conflict",
                    content=json.dumps(
                        {
                            "task": "Apply the same strict configuration repair in a read-only checkout.",
                            "observed_environment": "The repository filesystem was mounted read-only.",
                            "attempted_action": "Write mode=strict to config.json.",
                            "outcome": "The write failed with a permission error; config.json remained permissive.",
                            "validation": {
                                "command": "python3 test_config.py",
                                "status": "not_run",
                                "reason": "The required configuration change could not be written.",
                            },
                        },
                        ensure_ascii=False,
                    ),
                    metadata={"kind": "experience-conflict-evidence", "validation_status": "not_run"},
                )
            )
            generated_conflict = await client.generate_experience(
                GenerateExperienceRequest(
                    scope_id=artifact_scope,
                    source_refs=[conflicting_outcome.source],
                    artifact_refs=[experience_v2.artifact],
                    target=experience_v2.artifact,
                    reason=(
                        "The same procedure encountered a contradictory outcome under a read-only applicability "
                        "boundary; preserve or narrow that conflict explicitly."
                    ),
                )
            )
            conflict_candidate = _required_generated_candidate(
                generated_conflict,
                "configured model silently discarded contradictory Experience evidence",
            )
            conflict_proposal = ExperienceProposal.model_validate(conflict_candidate.proposal)
            conflict_text = conflict_proposal.model_dump_json().lower()
            conflict_markers = ("read-only", "read only", "writable", "permission", "fail", "cannot", "unable")
            _require(
                any(marker in conflict_text for marker in conflict_markers),
                "generated replacement silently converted contradictory evidence into an unqualified success",
            )
            _require(conflict_candidate.target == experience_v2.artifact, "conflict replacement lost exact target")
            _require(
                conflict_candidate.source_refs == [conflicting_outcome.source]
                and conflict_candidate.artifact_refs == [experience_v2.artifact],
                "conflict replacement lost direct Source or predecessor lineage",
            )
            approved = await client.approve_artifact_candidate(
                ApproveArtifactCandidateRequest(
                    scope_id=artifact_scope,
                    candidate_id=conflict_candidate.candidate_id,
                    expected_version=conflict_candidate.version,
                )
            )
            experience_v3 = await client.get_experience(
                GetExperienceRequest(
                    scope_id=artifact_scope,
                    artifact=_result_artifact(approved, "conflict Experience approval wrote no Artifact"),
                )
            )
            historical_experience_v2 = await client.get_experience(
                GetExperienceRequest(scope_id=artifact_scope, artifact=experience_v2.artifact)
            )
            _require(experience_v3.artifact.revision == 3, "conflict replacement did not create Revision 3")
            _require(historical_experience_v2 == experience_v2, "Experience Revision 2 is no longer exactly readable")
            recorder.write_json(
                "api/conflicting-outcome-source.json",
                conflicting_outcome.model_dump(mode="json"),
            )
            recorder.write_json(
                "api/conflicting-experience-candidate.json",
                conflict_candidate.model_dump(mode="json"),
            )
            recorder.write_json("api/experience-v3-conflict.json", experience_v3.model_dump(mode="json"))

        with recorder.scenario(
            "external Skill Registry isolates, forks, imports, and rescans exact package snapshots",
            "api/external-skill-scan.json",
            "api/external-skill-fork-candidate.json",
            "api/external-skill-fork-rejected.json",
            "api/external-skill-import-candidate.json",
            "api/external-skill-import-approved.json",
            "api/external-skill-stale-resolution.json",
            "api/external-skill-rescan.json",
            "api/external-skill-current-resolution.json",
        ):
            scan = await client.scan_external_skills(ScanExternalSkillsRequest(scope_id=artifact_scope))
            _require(len(scan.registrations) == 1, "external Skill scan did not discover exactly one fixture")
            registration = scan.registrations[0]
            listed = await client.list_external_skills(ListExternalSkillsRequest(scope_id=artifact_scope))
            _require(len(listed.skills) == 1, "available external Skill was not listed")
            foreign_list = await client.list_external_skills(ListExternalSkillsRequest(scope_id=foreign_scope))
            _require(not foreign_list.skills, "external Skill registration leaked into another scope")
            resolved = await client.resolve_external_skill(
                ResolveExternalSkillRequest(
                    scope_id=artifact_scope,
                    external_skill_id=registration.external_skill_id,
                    fingerprint=registration.fingerprint,
                )
            )
            _require(resolved.status is ExternalSkillResolutionStatus.AVAILABLE, "exact external Skill was unavailable")
            _require(
                resolved.entrypoint == str(external_skill / "SKILL.md"), "external Skill resolved wrong entrypoint"
            )
            forked = await client.import_external_skill(
                ImportExternalSkillRequest(
                    scope_id=artifact_scope,
                    external_skill_id=registration.external_skill_id,
                    fingerprint=registration.fingerprint,
                    mode=ExternalSkillImportMode.FORK,
                    reason="Exercise explicit fork Review gating for one exact external package snapshot.",
                )
            )
            fork_candidate = _required_generated_candidate(
                forked,
                "configured model returned no managed Skill for the external fork snapshot",
            )
            _require(
                len(fork_candidate.source_refs) == 1
                and fork_candidate.source_refs[0].name == "external-skill-snapshot",
                "external fork lost exact snapshot Source lineage",
            )
            rejected_fork = await client.reject_artifact_candidate(
                RejectArtifactCandidateRequest(
                    scope_id=artifact_scope,
                    candidate_id=fork_candidate.candidate_id,
                    expected_version=fork_candidate.version,
                    reason="The fork path is validated here without creating a second managed Artifact.",
                )
            )
            _require(rejected_fork.result_artifact is None, "rejected external fork produced an Artifact")
            imported = await client.import_external_skill(
                ImportExternalSkillRequest(
                    scope_id=artifact_scope,
                    external_skill_id=registration.external_skill_id,
                    fingerprint=registration.fingerprint,
                    mode=ExternalSkillImportMode.IMPORT,
                    reason="Govern one caller-selected exact external package snapshot.",
                )
            )
            imported_candidate = _required_generated_candidate(
                imported,
                "configured model returned no managed Skill for the external snapshot",
            )
            _require(
                len(imported_candidate.source_refs) == 1
                and imported_candidate.source_refs[0].name == "external-skill-snapshot",
                "external import lost exact snapshot Source lineage",
            )
            approved = await client.approve_artifact_candidate(
                ApproveArtifactCandidateRequest(
                    scope_id=artifact_scope,
                    candidate_id=imported_candidate.candidate_id,
                    expected_version=imported_candidate.version,
                )
            )
            imported_skill = await client.get_skill(
                GetSkillRequest(
                    scope_id=artifact_scope,
                    artifact=_result_artifact(approved, "external Skill import approval wrote no Artifact"),
                )
            )
            manifest = external_skill / "SKILL.md"
            manifest.write_text(
                manifest.read_text(encoding="utf-8") + "\nPackage changed after import.\n", encoding="utf-8"
            )
            stale = await client.resolve_external_skill(
                ResolveExternalSkillRequest(
                    scope_id=artifact_scope,
                    external_skill_id=registration.external_skill_id,
                    fingerprint=registration.fingerprint,
                )
            )
            _require(
                stale.status is ExternalSkillResolutionStatus.UNAVAILABLE, "changed external snapshot stayed available"
            )
            _require(stale.entrypoint is None, "unavailable external snapshot exposed an entrypoint")
            rescanned = await client.scan_external_skills(ScanExternalSkillsRequest(scope_id=artifact_scope))
            _require(len(rescanned.registrations) == 1, "external Skill rescan lost the changed package")
            current_registration = rescanned.registrations[0]
            _require(
                current_registration.external_skill_id == registration.external_skill_id,
                "external Skill rescan changed stable registration identity",
            )
            _require(
                current_registration.fingerprint != registration.fingerprint,
                "external Skill content change did not produce a new fingerprint",
            )
            old_after_rescan = await client.resolve_external_skill(
                ResolveExternalSkillRequest(
                    scope_id=artifact_scope,
                    external_skill_id=registration.external_skill_id,
                    fingerprint=registration.fingerprint,
                )
            )
            current = await client.resolve_external_skill(
                ResolveExternalSkillRequest(
                    scope_id=artifact_scope,
                    external_skill_id=current_registration.external_skill_id,
                    fingerprint=current_registration.fingerprint,
                )
            )
            _require(
                old_after_rescan.status is ExternalSkillResolutionStatus.UNAVAILABLE,
                "rescan made the stale external fingerprint available again",
            )
            _require(current.status is ExternalSkillResolutionStatus.AVAILABLE, "rescan did not expose new fingerprint")
            _require(current.entrypoint == str(external_skill / "SKILL.md"), "rescan resolved wrong local binding")
            recorder.write_json("api/external-skill-scan.json", scan.model_dump(mode="json"))
            recorder.write_json(
                "api/external-skill-fork-candidate.json",
                fork_candidate.model_dump(mode="json"),
            )
            recorder.write_json(
                "api/external-skill-fork-rejected.json",
                rejected_fork.model_dump(mode="json"),
            )
            recorder.write_json(
                "api/external-skill-import-candidate.json",
                imported_candidate.model_dump(mode="json"),
            )
            recorder.write_json("api/external-skill-import-approved.json", imported_skill.model_dump(mode="json"))
            recorder.write_json("api/external-skill-stale-resolution.json", stale.model_dump(mode="json"))
            recorder.write_json("api/external-skill-rescan.json", rescanned.model_dump(mode="json"))
            recorder.write_json("api/external-skill-current-resolution.json", current.model_dump(mode="json"))

    return ConfiguredJourneyState(
        scopes=scopes,
        memory_query=memory_query,
        experience_revisions=(experience_v1, experience_v2, experience_v3),
        skill_revisions=(skill_v1, skill_v2, imported_skill),
        rejected_candidate_ids=(
            rejected_lineage.candidate_id,
            rejected_replacement.candidate_id,
            rejected_fork.candidate_id,
        ),
        external_skill_id=current_registration.external_skill_id,
        external_skill_fingerprint=current_registration.fingerprint,
    )


async def _verify_configured_restart(
    *,
    database: DatabaseConfig,
    recorder: Recorder,
    state: ConfiguredJourneyState,
    server_url: str,
    generation_timeout: float,
) -> None:
    with recorder.scenario(
        "configured state remains exact and searchable after a clean Server restart",
        "api/restart-persistence.json",
    ):
        timeout = max(30.0, generation_timeout + 30.0)
        async with PowerContextClient(server_url, timeout=timeout) as client:
            persisted_experience_values: list[ExperienceArtifact] = []
            for expected in state.experience_revisions:
                persisted_experience_values.append(
                    await client.get_experience(
                        GetExperienceRequest(scope_id=state.scopes.artifacts, artifact=expected.artifact)
                    )
                )
            persisted_experiences = tuple(persisted_experience_values)
            persisted_skill_values: list[SkillArtifact] = []
            for expected in state.skill_revisions:
                persisted_skill_values.append(
                    await client.get_skill(GetSkillRequest(scope_id=state.scopes.artifacts, artifact=expected.artifact))
                )
            persisted_skills = tuple(persisted_skill_values)
            rejected_values: list[ArtifactCandidate] = []
            for candidate_id in state.rejected_candidate_ids:
                rejected_values.append(
                    await client.get_artifact_candidate(
                        GetArtifactCandidateRequest(scope_id=state.scopes.artifacts, candidate_id=candidate_id)
                    )
                )
            rejected = tuple(rejected_values)
            resolved = await client.resolve_external_skill(
                ResolveExternalSkillRequest(
                    scope_id=state.scopes.artifacts,
                    external_skill_id=state.external_skill_id,
                    fingerprint=state.external_skill_fingerprint,
                )
            )
            vector = await client.search_memory(
                SearchMemoryRequest(
                    scope_id=state.scopes.memory,
                    query=state.memory_query,
                    limit=5,
                    mode=MemorySearchMode.VECTOR,
                )
            )
            current_experience = state.experience_revisions[-1]
            prepared = await client.prepare_context(
                PrepareContextRequest(
                    scope_id=state.scopes.artifacts,
                    query=current_experience.content.lesson,
                )
            )
        database_counts = await _database_scope_counts(database, state.scopes.all)

        _require(
            persisted_experiences == state.experience_revisions,
            "Server restart changed an exact Experience Revision",
        )
        _require(persisted_skills == state.skill_revisions, "Server restart changed an exact Skill Revision")
        _require(
            all(candidate.status is CandidateStatus.REJECTED for candidate in rejected),
            "Server restart lost a rejected Candidate decision",
        )
        _require(
            resolved.status is ExternalSkillResolutionStatus.AVAILABLE,
            "Server restart lost the current external Skill binding",
        )
        _require(bool(vector.hits), "Server restart lost the real vector projection")
        _require(
            MemoryMatchedBy.VECTOR in vector.hits[0].matched_by,
            "Server restart returned a non-vector result for vector-only retrieval",
        )
        _require(prepared.status.value == "ready", "Server restart lost approved Experience recall")
        prepared_content = prepared.content
        if prepared_content is None:
            _fail("ready PreparedContext omitted content after Server restart")
        _require('"kind":"experience"' in prepared_content, "restart PreparedContext omitted Experience kind")
        _require(
            current_experience.artifact.artifact_id in prepared_content,
            "restart PreparedContext cited the wrong Experience head",
        )
        _require(
            database_counts[state.scopes.memory]["pc_memory_vector_entries"] > 0,
            "independent database audit found no persisted vector projection",
        )
        _require(
            database_counts[state.scopes.artifacts]["pc_source_cursors"] > 0,
            "independent database audit found no persisted Experience incubation cursor",
        )
        _require(
            database_counts[state.scopes.artifacts]["pc_artifact_lineage_artifacts"] > 0,
            "independent database audit found no exact Artifact lineage",
        )
        _require(
            database_counts[state.scopes.artifacts]["pc_artifact_heads"] > 0,
            "independent database audit found no approved Artifact heads",
        )
        recorder.write_json(
            "api/restart-persistence.json",
            {
                "experience_revisions": [value.artifact.model_dump(mode="json") for value in persisted_experiences],
                "skill_revisions": [value.artifact.model_dump(mode="json") for value in persisted_skills],
                "rejected_candidate_count": len(rejected),
                "external_skill_status": resolved.status,
                "vector_hit_count": len(vector.hits),
                "prepared_context_bytes": prepared.content_bytes,
                "database_counts": database_counts,
            },
        )


async def _wait_for_experience_candidate(
    client: PowerContextClient,
    scope_id: str,
    *,
    timeout_seconds: float = 10.0,
) -> ArtifactCandidatePage:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        inbox = await client.list_artifact_candidates(
            ListArtifactCandidatesRequest(
                scope_id=scope_id,
                family=CandidateFamily.EXPERIENCE,
            )
        )
        if len(inbox.candidates) == 1:
            return inbox
        if len(inbox.candidates) > 1:
            _fail("scheduled Experience incubation produced duplicate Candidates")
        await asyncio.sleep(0.05)
    _fail("scheduled Experience Candidate did not reach the Review Inbox")


def _required_generated_candidate(response: GeneratedCandidateResponse, message: str) -> ArtifactCandidate:
    _require(response.status is GeneratedCandidateStatus.PENDING, message)
    candidate = response.candidate
    if candidate is None:
        _fail(message)
    return candidate


async def _reject_after_invalid_skill_approval(
    client: PowerContextClient,
    *,
    scope_id: str,
    candidate: ArtifactCandidate,
    rejection_reason: str,
    accepted_message: str,
) -> ArtifactCandidate:
    try:
        await client.approve_artifact_candidate(
            ApproveArtifactCandidateRequest(
                scope_id=scope_id,
                candidate_id=candidate.candidate_id,
                expected_version=candidate.version,
            )
        )
    except ServerResponseError as error:
        _require(error.status_code == 422, "invalid Skill lineage returned an unexpected HTTP status")
        _require(error.code == "invalid_request", "invalid Skill lineage returned an unexpected error code")
    else:
        _fail(accepted_message)
    still_pending = await client.get_artifact_candidate(
        GetArtifactCandidateRequest(scope_id=scope_id, candidate_id=candidate.candidate_id)
    )
    _require(still_pending.status is CandidateStatus.PENDING, "failed approval mutated Candidate status")
    return await client.reject_artifact_candidate(
        RejectArtifactCandidateRequest(
            scope_id=scope_id,
            candidate_id=candidate.candidate_id,
            expected_version=still_pending.version,
            reason=rejection_reason,
        )
    )


async def _replace_experience(client, scope_id, current, source):
    candidate = await client.propose_experience(
        ProposeExperienceRequest(
            scope_id=scope_id,
            proposal=ExperienceProposal(
                situation=current.content.situation,
                action=current.content.action,
                outcome="The same procedure also passed when a later Codex task used the projected managed Skill.",
                lesson=f"{current.content.lesson} The approved Skill projection reproduced the result in a later task.",
            ),
            source_refs=[source],
            artifact_refs=[current.artifact],
            target=current.artifact,
            reason="Later real-Codex usage strengthened the original judgment.",
        )
    )
    approved = await client.approve_artifact_candidate(
        ApproveArtifactCandidateRequest(
            scope_id=scope_id,
            candidate_id=candidate.candidate_id,
            expected_version=1,
        )
    )
    _require(approved.result_artifact is not None, "Experience replacement approval wrote no Artifact")
    return await client.get_experience(GetExperienceRequest(scope_id=scope_id, artifact=approved.result_artifact))


async def _replace_skill(client, scope_id, current, source):
    candidate = await client.propose_skill(
        ProposeSkillRequest(
            scope_id=scope_id,
            proposal=SkillProposal(
                name=current.content.name,
                description=current.content.description,
                instructions=f"{current.content.instructions.rstrip()}\nPreserve the validation output in the task result.",
                validation=current.content.validation,
            ),
            source_refs=[source],
            artifact_refs=[current.artifact],
            target=current.artifact,
            reason="Real usage showed that validation evidence should be preserved explicitly.",
        )
    )
    approved = await client.approve_artifact_candidate(
        ApproveArtifactCandidateRequest(
            scope_id=scope_id,
            candidate_id=candidate.candidate_id,
            expected_version=1,
        )
    )
    _require(approved.result_artifact is not None, "Skill replacement approval wrote no Artifact")
    return await client.get_skill(GetSkillRequest(scope_id=scope_id, artifact=approved.result_artifact))


def _project_via_cli(
    *,
    server_url: str,
    scope_id: str,
    skill: SkillArtifact,
    destination: Path,
    recorder: Recorder,
) -> None:
    uv = _required_executable("uv")
    completed = _run(
        [
            uv,
            "run",
            "powercontext",
            "client",
            "--server-url",
            server_url,
            "skill",
            "export",
            "--target",
            "codex",
            "--scope-id",
            scope_id,
            "--revision",
            str(skill.artifact.revision),
            "--destination",
            destination,
            skill.artifact.artifact_id,
        ],
        cwd=PROJECT_ROOT,
    )
    recorder.write_text("projection/cli.stdout", completed.stdout)


def _run_codex(
    *,
    recorder: Recorder,
    codex: Path,
    environment: Mapping[str, str],
    name: str,
    repository: Path,
    prompt: str,
    output_schema: Path | None,
    sandbox: str,
    timeout: int,
) -> dict[str, Any]:
    session_directory = recorder.directory / "sessions"
    session_directory.mkdir(parents=True, exist_ok=True)
    last_message = session_directory / f"{name}.last.json"
    command: list[str | Path] = [
        codex,
        "-a",
        "never",
        "--disable",
        "memories",
        "--disable",
        "plugins",
        "--disable",
        "remote_plugin",
        "--disable",
        "shell_snapshot",
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--json",
        "-s",
        sandbox,
        "-C",
        repository,
        "-o",
        last_message,
    ]
    if output_schema is not None:
        command.extend(("--output-schema", output_schema))
    command.append(prompt)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            [str(value) for value in command],
            cwd=repository,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        recorder.write_text(f"sessions/{name}.jsonl", _process_output(error.stdout))
        recorder.write_text(f"sessions/{name}.stderr", _process_output(error.stderr))
        recorder.write_text(f"sessions/{name}.timeout.txt", str(error))
        recorder.write_json(
            f"sessions/{name}.meta.json",
            {
                "status": "timed-out",
                "duration_seconds": round(time.monotonic() - started, 3),
                "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            },
        )
        raise RealCodexE2EError(f"Codex session {name} timed out after {timeout}s") from error  # noqa: TRY003
    recorder.write_text(f"sessions/{name}.jsonl", completed.stdout)
    recorder.write_text(f"sessions/{name}.stderr", completed.stderr)
    recorder.write_json(
        f"sessions/{name}.meta.json",
        {
            "returncode": completed.returncode,
            "duration_seconds": round(time.monotonic() - started, 3),
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        },
    )
    if completed.returncode != 0:
        _fail(f"Codex session {name} exited with {completed.returncode}: {completed.stderr.strip()[:500]}")
    if output_schema is None:
        return {}
    try:
        return json.loads(last_message.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _fail(f"Codex session {name} returned invalid structured output", cause=error)


def _process_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    return value.decode(errors="replace") if isinstance(value, bytes) else value


def _skill_proposal(value: Mapping[str, Any]) -> SkillProposal:
    validation = value.get("validation")
    if not isinstance(validation, list) or not all(isinstance(item, str) for item in validation):
        _fail("Codex managed Skill validation is not a string list")
    return SkillProposal(
        name=str(value.get("name", "")),
        description=str(value.get("description", "")),
        instructions=str(value.get("instructions", "")),
        validation=[SkillValidationItem(item) for item in validation],
    )


def _prepare_repositories(
    root: Path,
    git: Path,
    *,
    require_marker: bool = True,
    validation_command: str = "python test_config.py",
) -> dict[str, Path]:
    repositories: dict[str, Path] = {}
    for name in ("producer", "consumer"):
        repository = root / name
        repository.mkdir(parents=True)
        (repository / "AGENTS.md").write_text(
            (
                "# Synthetic acceptance repository\n\n"
                f"Follow the user task. Never edit test_config.py. Run {validation_command}.\n"
            ),
            encoding="utf-8",
        )
        (repository / "config.json").write_text('{"mode": "permissive"}\n', encoding="utf-8")
        marker_check = (
            ""
            if name == "producer" or not require_marker
            else (
                "marker = Path('managed-skill-v1.txt')\n"
                "assert marker.read_text(encoding='utf-8') == 'POWERCONTEXT_SKILL_V1\\n'\n"
            )
        )
        (repository / "test_config.py").write_text(
            "import json\n"
            "from pathlib import Path\n\n"
            "config = json.loads(Path('config.json').read_text(encoding='utf-8'))\n"
            "assert config == {'mode': 'strict'}\n"
            f"{marker_check}"
            "print('strict configuration validation passed')\n",
            encoding="utf-8",
        )
        _run([git, "init", "-q"], cwd=repository)
        _run([git, "config", "user.email", "codex-e2e@example.invalid"], cwd=repository)
        _run([git, "config", "user.name", "PowerContext Codex E2E"], cwd=repository)
        _run([git, "add", "AGENTS.md", "config.json", "test_config.py"], cwd=repository)
        _run([git, "commit", "-qm", "init synthetic fixture"], cwd=repository)
        repositories[name] = repository
    return repositories


def _start_server(database: Path, scheduler: Path) -> RunningServer:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
            runtime=RuntimeConfig(experience_schedule_seconds=0.05),
            mcp=McpConfig(enabled=False),
        ),
        scheduler_path=scheduler,
        experience_pipeline=TaskOutcomeExperiencePipeline(),
    )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        listener.close()
        _fail("PowerContext Server did not start")
    return RunningServer(server=server, thread=thread, listener=listener, base_url=f"http://127.0.0.1:{port}")


def _start_configured_server(settings: ServerSettings, scheduler_path: Path) -> RunningServer:
    app = create_server_app(settings=settings, scheduler_path=scheduler_path)
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    port = int(listener.getsockname()[1])
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 60
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.05)
    if not server.started:
        listener.close()
        _fail("configured PowerContext Server did not start")
    return RunningServer(server=server, thread=thread, listener=listener, base_url=f"http://127.0.0.1:{port}")


def _validate_configured_settings(settings: ServerSettings) -> None:
    inference = settings.inference
    if inference.generation_model is None:
        _fail("configured E2E requires POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL")
    if inference.embedding_model is None:
        _fail("configured E2E requires POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL")
    if isinstance(settings.database, SQLiteConfig):
        extension = settings.database.vec1_extension
        if extension is None or not extension.is_file():
            _fail("configured SQLite E2E requires an installed Vec1 extension file")
        if not hasattr(sqlite3.Connection, "enable_load_extension"):
            _fail("configured SQLite E2E requires a Python build with SQLite extension loading enabled")


def _new_configured_scopes() -> ConfiguredScopes:
    suffix = f"{time.time_ns()}-{os.getpid()}"
    return ConfiguredScopes(
        memory=f"configured-real-memory:{suffix}",
        artifacts=f"configured-real-experience-skill:{suffix}",
        foreign=f"configured-real-foreign:{suffix}",
    )


def _without_scheduled_processing(settings: ServerSettings) -> ServerSettings:
    runtime = settings.runtime.model_copy(
        update={
            "schedule_seconds": None,
            "experience_schedule_seconds": None,
        }
    )
    return settings.model_copy(update={"runtime": runtime})


def _remove_existing_harness_outputs(output_root: Path) -> int:
    if not output_root.is_dir():
        return 0
    removed = 0
    for child in output_root.iterdir():
        if not _HARNESS_OUTPUT_PATTERN.fullmatch(child.name):
            continue
        if child.is_symlink() or child.is_file():
            child.unlink()
        else:
            shutil.rmtree(child)
        removed += 1
    return removed


async def _purge_existing_harness_scopes(database: DatabaseConfig) -> dict[str, object]:
    scopes = await _discover_harness_scopes(database)
    cleanup = await _purge_database_scopes(database, scopes)
    remaining = await _discover_harness_scopes(database)
    if remaining:
        _fail(f"preflight database cleanup left {len(remaining)} harness scopes")
    cleanup["remaining_harness_scope_count"] = 0
    return cleanup


async def _discover_harness_scopes(database: DatabaseConfig) -> tuple[str, ...]:
    async def discover(profile: OceanBaseProfile | SQLiteProfile) -> tuple[str, ...]:
        scopes: set[str] = set()
        async with profile.database.transaction() as connection:
            for table_name in _SCOPE_TABLES:
                statement = text(
                    f"SELECT DISTINCT scope_id FROM {table_name} WHERE scope_id LIKE :prefix"  # noqa: S608
                )
                for prefix in _HARNESS_SCOPE_PREFIXES:
                    scopes.update(
                        str(value)
                        for value in (await connection.execute(statement, {"prefix": f"{prefix}%"})).scalars()
                    )
        return tuple(sorted(scopes))

    if isinstance(database, OceanBaseConfig):
        async with OceanBaseProfile.open(database, tables=()) as profile:
            return await discover(profile)
    async with SQLiteProfile.open(database, tables=()) as profile:
        return await discover(profile)


async def _database_scope_counts(
    database: DatabaseConfig,
    scopes: tuple[str, ...],
) -> dict[str, dict[str, int]]:
    async def count(profile: OceanBaseProfile | SQLiteProfile) -> dict[str, dict[str, int]]:
        async with profile.database.transaction() as connection:
            return await _scope_counts(connection, scopes)

    if isinstance(database, OceanBaseConfig):
        async with OceanBaseProfile.open(database, tables=()) as profile:
            return await count(profile)
    async with SQLiteProfile.open(database, tables=()) as profile:
        return await count(profile)


async def _purge_database_scopes(
    database: DatabaseConfig,
    scopes: tuple[str, ...],
) -> dict[str, object]:
    async def purge(profile: OceanBaseProfile | SQLiteProfile) -> dict[str, object]:
        async with profile.database.transaction() as connection:
            before = await _scope_counts(connection, scopes)
            if scopes:
                for table_name in _SCOPE_TABLES:
                    statement = text(
                        f"DELETE FROM {table_name} WHERE scope_id IN :scope_ids"  # noqa: S608
                    ).bindparams(bindparam("scope_ids", expanding=True))
                    await connection.execute(statement, {"scope_ids": scopes})
        async with profile.database.transaction() as connection:
            after = await _scope_counts(connection, scopes)
        rows_before = {
            table_name: sum(scope_counts[table_name] for scope_counts in before.values())
            for table_name in _SCOPE_TABLES
        }
        rows_after = {
            table_name: sum(scope_counts[table_name] for scope_counts in after.values()) for table_name in _SCOPE_TABLES
        }
        remaining = sum(rows_after.values())
        if remaining:
            _fail(f"database cleanup left {remaining} rows in {len(scopes)} exact harness scopes")
        return {
            "scope_count": len(scopes),
            "rows_before": {name: count for name, count in rows_before.items() if count},
            "rows_after": {name: count for name, count in rows_after.items() if count},
            "remaining_row_count": remaining,
        }

    if isinstance(database, OceanBaseConfig):
        async with OceanBaseProfile.open(database, tables=()) as profile:
            return await purge(profile)
    async with SQLiteProfile.open(database, tables=()) as profile:
        return await purge(profile)


async def _scope_counts(connection: Any, scopes: tuple[str, ...]) -> dict[str, dict[str, int]]:
    counts = {scope: dict.fromkeys(_SCOPE_TABLES, 0) for scope in scopes}
    if not scopes:
        return counts
    for table_name in _SCOPE_TABLES:
        statement = text(
            f"SELECT scope_id, COUNT(*) FROM {table_name} "  # noqa: S608
            "WHERE scope_id IN :scope_ids GROUP BY scope_id"
        ).bindparams(bindparam("scope_ids", expanding=True))
        rows = await connection.execute(statement, {"scope_ids": scopes})
        for scope_id, row_count in rows:
            counts[str(scope_id)][table_name] = int(row_count)
    return counts


def _configured_server_settings(
    settings: ServerSettings,
    output_directory: Path,
    external_skill_root: Path,
) -> ServerSettings:
    database = settings.database
    if isinstance(database, SQLiteConfig):
        database = database.model_copy(
            update={"url": f"sqlite+aiosqlite:///{output_directory / 'configured-runtime.db'}"}
        )
    runtime = settings.runtime.model_copy(
        update={
            "experience_schedule_seconds": CONFIGURED_EXPERIENCE_SCHEDULE_SECONDS,
        }
    )
    external_skills = ExternalSkillsConfig(
        host_id=f"configured-e2e-{output_directory.name}",
        codex_roots=(
            CodexSkillRoot(
                root_id="configured-e2e",
                installation_scope="project",
                path=external_skill_root,
            ),
        ),
    )
    return settings.model_copy(
        update={
            "database": database,
            "runtime": runtime,
            "external_skills": external_skills,
            "mcp": McpConfig(enabled=False),
        }
    )


def _prepare_external_skill(root: Path) -> Path:
    package = root / "external-release-audit"
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\n"
        "name: external-release-audit\n"
        "description: Use before a production rollout to verify canary evidence.\n"
        "---\n\n"
        "# Instructions\n\n"
        "Confirm that a canary health check passed before expanding deployment to every region.\n\n"
        "## Validation\n\n"
        "- The task result identifies the observed canary health-check outcome.\n",
        encoding="utf-8",
    )
    return package


def _user_state(codex_home: Path) -> dict[str, str | None]:
    return {
        name: _digest(path) if path.is_file() else None
        for name in ("auth.json", "config.toml")
        if (path := codex_home / name)
    }


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_stdout(repository: Path, *arguments: str) -> str:
    git = _required_executable("git")
    return _run([git, *arguments], cwd=repository).stdout


def _run(
    command: Sequence[str | Path],
    *,
    cwd: Path,
    env: Mapping[str, str] | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [str(value) for value in command],
        cwd=cwd,
        env=None if env is None else dict(env),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        rendered = " ".join(str(value) for value in command[:5])
        _fail(f"command failed ({completed.returncode}): {rendered}; stderr={completed.stderr.strip()[:500]}")
    return completed


def _required_executable(name: str) -> Path:
    value = shutil.which(name)
    if value is None:
        _fail(f"required executable is unavailable: {name}")
    return Path(value).resolve()


def _result_artifact(candidate: ArtifactCandidate, message: str) -> ArtifactReference:
    result = candidate.result_artifact
    if result is None:
        _fail(message)
    return result


def _require(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


def _fail(message: str, *, cause: BaseException | None = None) -> Never:
    if cause is None:
        raise RealCodexE2EError(message)
    raise RealCodexE2EError(message) from cause


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="Directory for bounded synthetic evidence.",
    )
    parser.add_argument(
        "--codex-timeout",
        type=int,
        default=360,
        help="Per-session real Codex timeout in seconds.",
    )
    parser.add_argument(
        "--configured",
        action="store_true",
        help="Use real generation, embedding, database, Vec1, and External Skill settings from the environment.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=PROJECT_ROOT / ".env",
        help="Environment file loaded only by --configured mode.",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete this run's exact configured database scopes and evidence directory after auditing them.",
    )
    parser.add_argument(
        "--purge-existing",
        action="store_true",
        help="Before the run, remove prior Experience/Skill harness outputs and configured test scopes.",
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(main())
