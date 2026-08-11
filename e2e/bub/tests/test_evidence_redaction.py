from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from pydantic_evals.otel import SpanNode

from powercontext_e2e.models import (
    MemoryEntrySnapshot,
    MemorySnapshot,
    PreparedContextSnapshot,
    ReplayObservation,
    RunEnvironment,
    ScenarioSpec,
    SessionObservation,
)
from powercontext_e2e.runner import write_artifacts


def test_write_artifacts_redacts_known_runtime_secrets_at_every_sink(monkeypatch, tmp_path: Path) -> None:
    runtime_secrets = ("provider-runtime-secret-sentinel", "server-runtime-secret-sentinel")
    monkeypatch.setenv("BUB_API_KEY", runtime_secrets[0])
    monkeypatch.setenv("POWERCONTEXT_SERVER_AUTH_TOKEN", runtime_secrets[1])

    observation = _observation(
        session_input=f"Question containing {runtime_secrets[0]}",
        memory_text="The project selected OceanBase.",
        context="OceanBase supports shared persistent context.",
        output=f"Output containing {runtime_secrets[0]}",
        error=f"Request failed with {runtime_secrets[1]}",
        span_attributes={"gen_ai.operation.name": "chat"},
    )
    report = _report(
        assertion_reason=f"Judge echoed {runtime_secrets[0]}",
        score_reason="The expected fact was present.",
        label_reason="pass",
        attributes={"mode": "live"},
        failure=f"Request failed with {runtime_secrets[1]}",
        rendered=f"Run contained {runtime_secrets[0]} and {runtime_secrets[1]}",
    )

    write_artifacts(observation, report, tmp_path)

    artifacts = {path.name: path.read_text(encoding="utf-8") for path in tmp_path.iterdir()}
    assert set(artifacts) == {"eval-report.json", "replay.json", "report.md"}
    for artifact_name, content in artifacts.items():
        for secret in runtime_secrets:
            assert secret not in content, f"{secret!r} leaked into {artifact_name}"
        assert "[REDACTED]" in content

    json.loads(artifacts["replay.json"])
    json.loads(artifacts["eval-report.json"])


def test_write_artifacts_preserves_normal_live_replay_schema(tmp_path: Path) -> None:
    observation = _observation(
        session_input="Which database was selected?",
        memory_text="The project selected OceanBase; Bearer authentication is unrelated.",
        context="OceanBase supports shared persistent context.",
        output="The project selected OceanBase.",
        error=None,
        span_attributes={
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "deepseek-v4-flash",
            "gen_ai.usage.input_tokens": 21,
            "http.url": "https://provider.example/v1?mode=live",
        },
    )
    report = _report(
        assertion_reason="The run completed.",
        score_reason="The expected fact was present.",
        label_reason="pass",
        attributes={"mode": "live"},
        failure=None,
        rendered="All checks passed.",
    )
    expected_replay = json.loads(observation.model_dump_json(by_alias=True))
    expected_evaluation = {
        "schema": "powercontext.session-replay-evaluation/v1",
        "experiment": "live:database-decision",
        "cases": [
            {
                "name": "database-decision",
                "assertions": {"run_completed": {"value": True, "reason": "The run completed."}},
                "scores": {"answer": {"value": 1.0, "reason": "The expected fact was present."}},
                "labels": {"judge": {"value": "pass", "reason": "pass"}},
                "metrics": {"model_calls": 1},
                "attributes": {"mode": "live"},
                "task_duration": 0.25,
                "total_duration": 0.5,
            }
        ],
        "failures": [],
    }

    write_artifacts(observation, report, tmp_path)

    actual_replay = json.loads((tmp_path / "replay.json").read_text(encoding="utf-8"))
    actual_evaluation = json.loads((tmp_path / "eval-report.json").read_text(encoding="utf-8"))
    assert actual_replay == expected_replay
    assert actual_evaluation == expected_evaluation
    assert ReplayObservation.model_validate(actual_replay) == observation


def _observation(
    *,
    session_input: str,
    memory_text: str,
    context: str,
    output: str,
    error: str | None,
    span_attributes: dict[str, Any],
) -> ReplayObservation:
    scenario = ScenarioSpec.model_validate({
        "schema": "powercontext.session-replay/v1",
        "id": "database-decision",
        "sessions": [
            {
                "id": "recall",
                "input": session_input,
                "expected_answer": "The project selected OceanBase.",
            }
        ],
    })
    memory = MemorySnapshot(
        entries=(
            MemoryEntrySnapshot(
                entry_id="memory-1",
                entry_version_id="memory-version-1",
                version=1,
                kind="fact",
                text=memory_text,
                state="committed",
            ),
        )
    )
    session = SessionObservation(
        id="recall",
        agent_session_id="session-1",
        status="failed" if error else "completed",
        error=error,
        prepared_context=PreparedContextSnapshot(status="ready", content=context),
        output=output,
        memory_after=memory,
    )
    timestamp = datetime(2026, 8, 11, 1, 2, 3, tzinfo=UTC)
    span = SpanNode(
        name="bub.model",
        trace_id=1,
        span_id=2,
        parent_span_id=None,
        start_timestamp=timestamp,
        end_timestamp=timestamp,
        attributes=span_attributes,
    )
    return ReplayObservation(
        run_id="database-decision-run",
        environment=RunEnvironment(
            mode="live",
            commit="abcdef0",
            database="sqlite",
            agent_model="deepseek:deepseek-v4-flash",
            generation_model="deepseek:deepseek-v4-flash",
            judge_model="deepseek:deepseek-v4-flash",
            started_at=timestamp,
        ),
        scenario=scenario,
        status="failed" if error else "completed",
        errors=(error,) if error else (),
        memory_before=memory,
        memory_after=memory,
        sessions=(session,),
        spans=(span,),
    )


def _report(
    *,
    assertion_reason: str,
    score_reason: str,
    label_reason: str,
    attributes: dict[str, Any],
    failure: str | None,
    rendered: str,
) -> Any:
    case = SimpleNamespace(
        name="database-decision",
        assertions={"run_completed": SimpleNamespace(value=True, reason=assertion_reason)},
        scores={"answer": SimpleNamespace(value=1.0, reason=score_reason)},
        labels={"judge": SimpleNamespace(value="pass", reason=label_reason)},
        metrics={"model_calls": 1},
        attributes=attributes,
        task_duration=0.25,
        total_duration=0.5,
    )
    failures = [] if failure is None else [SimpleNamespace(name="judge", error_message=failure)]
    return SimpleNamespace(
        name="live:database-decision",
        cases=[case],
        failures=failures,
        render=lambda include_reasons: rendered,
    )
