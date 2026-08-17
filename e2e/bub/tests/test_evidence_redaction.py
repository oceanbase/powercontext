from __future__ import annotations

import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from powercontext_e2e.artifacts import write_artifacts
from powercontext_e2e.catalog import load_tasks
from powercontext_e2e.evaluation import MemoryEvaluator
from powercontext_e2e.evidence import load_resolved_instructions
from powercontext_e2e.models import (
    HarborTrialObservation,
    MemoryEntrySnapshot,
    MemorySnapshot,
    ResolvedInstruction,
    RunEnvironment,
    TaskObservation,
)
from powercontext_e2e.settings import HarnessSettings


def test_resolved_instruction_evidence_matches_harbor_acp_summaries(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sensitive_value = "instruction-secret-sentinel"
    monkeypatch.setenv("BUB_API_KEY", sensitive_value)
    summary_path = tmp_path / "steps" / "capture" / "agent" / "acp-summary.json"
    summary_path.parent.mkdir(parents=True)
    instruction = f"Inspect the database with {sensitive_value}."
    summary_path.write_text(json.dumps({"instruction": instruction}), encoding="utf-8")

    resolved = load_resolved_instructions(tmp_path, HarnessSettings())

    assert len(resolved) == 1
    assert resolved[0].step == "capture"
    assert resolved[0].artifact == "steps/capture/agent/acp-summary.json"
    assert resolved[0].content == "Inspect the database with [REDACTED]."
    assert resolved[0].sha256 == sha256(instruction.encode()).hexdigest()


def test_final_evidence_redacts_configured_secrets_and_preserves_the_public_schema(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sensitive_value = "provider-runtime-secret-sentinel"
    monkeypatch.setenv("BUB_API_KEY", sensitive_value)
    repository = Path(__file__).resolve().parents[3]
    task = next(
        task for task in load_tasks(repository / "e2e" / "bub" / "tasks") if task.id == "project-database-decision"
    )
    recorded_at = datetime(2026, 8, 13, tzinfo=UTC)
    observation = TaskObservation(
        run_id="evidence-test",
        environment=RunEnvironment(
            commit="abcdef0",
            database="sqlite",
            adapter_version="test-adapter",
            adapter_protocol_version="test-protocol",
            started_at=recorded_at,
            finished_at=recorded_at,
        ),
        task=task,
        status="failed",
        errors=(f"Provider returned {sensitive_value}",),
        harbor=HarborTrialObservation(
            task_checksum=task.dataset.checksum,
            exception_type="ProviderError",
            exception_message=f"Provider returned {sensitive_value}",
        ),
        resolved_instructions=(
            ResolvedInstruction(
                artifact="acp-summary.json",
                content=f"Use credential {sensitive_value} to complete the task.",
                sha256="a" * 64,
            ),
        ),
        memory_before=MemorySnapshot(),
        memory_after=MemorySnapshot(
            entries=(
                MemoryEntrySnapshot(
                    entry_id="entry-1",
                    entry_version_id="entry-version-1",
                    version=1,
                    kind="project-decision",
                    text="The project selected OceanBase.",
                    state="active",
                ),
            )
        ),
    )
    report = MemoryEvaluator.evaluate(observation, experiment="evidence-test")

    write_artifacts(observation, report, tmp_path, settings=HarnessSettings())

    artifacts = {path.name: path.read_text(encoding="utf-8") for path in tmp_path.iterdir()}
    assert set(artifacts) == {"eval-report.json", "replay.json", "report.md"}
    assert all(sensitive_value not in content for content in artifacts.values())
    assert all("[REDACTED]" in content for content in artifacts.values())
    replay = json.loads(artifacts["replay.json"])
    evaluation = json.loads(artifacts["eval-report.json"])
    assert replay["schema"] == "powercontext.e2e-evidence/v1"
    assert replay["task"]["execution"]["type"] == "bub"
    assert replay["resolved_instructions"][0]["content"] == "Use credential [REDACTED] to complete the task."
    assert evaluation["schema"] == "powercontext.e2e-evaluation/v1"
    assert evaluation["cases"][0]["attributes"]["execution_adapter"] == "bub"
