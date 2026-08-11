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


def test_write_artifacts_redacts_every_evidence_sink(monkeypatch, tmp_path: Path) -> None:
    credentials = {
        "BUB_API_KEY": "sk-bub-evidence-sentinel",
        "OPENAI_API_KEY": "sk-openai-evidence-sentinel",
        "POWERCONTEXT_SERVER_AUTH_TOKEN": "server-auth-evidence-sentinel",
        "POWERCONTEXT_CODEX_AUTHORIZATION": "Bearer codex-auth-evidence-sentinel",
        "POWERCONTEXT_SERVER_DATABASE_URL": (
            "mysql+aoceanbase://root:database-evidence-sentinel@db.example/powercontext"
        ),
    }
    for name, value in credentials.items():
        monkeypatch.setenv(name, value)

    extra_sentinels = (
        "header-evidence-sentinel",
        "span-database-evidence-sentinel",
        "query-evidence-sentinel",
        "attribute-evidence-sentinel",
        "failure-evidence-sentinel",
        "basic-evidence-sentinel",
        "sk-unregistered-evidence-sentinel",
    )
    observation = _observation(
        session_input=f"Question containing {credentials['BUB_API_KEY']}",
        memory_text=f"Memory containing {credentials['OPENAI_API_KEY']}",
        context=f"Context containing {credentials['POWERCONTEXT_SERVER_AUTH_TOKEN']}",
        output=f"Output containing {credentials['POWERCONTEXT_CODEX_AUTHORIZATION']}",
        error=f"Database failed at {credentials['POWERCONTEXT_SERVER_DATABASE_URL']}",
        span_attributes={
            "http.request.header.authorization": f"Bearer {extra_sentinels[0]}",
            "db.connection_string": (f"postgresql://user:{extra_sentinels[1]}@db.example/powercontext"),
            "http.url": f"https://provider.example/v1?access_token={extra_sentinels[2]}&mode=live",
        },
    )
    report = _report(
        assertion_reason=f"Judge echoed {credentials['OPENAI_API_KEY']} and {extra_sentinels[6]}",
        score_reason=f"Authorization: {credentials['POWERCONTEXT_CODEX_AUTHORIZATION']}",
        label_reason=f"Provider URL used api_key={extra_sentinels[2]}",
        attributes={"api_key": extra_sentinels[3]},
        failure=f"password={extra_sentinels[4]}",
        rendered=f"Authorization: Basic {extra_sentinels[5]} and {credentials['BUB_API_KEY']}",
    )

    write_artifacts(observation, report, tmp_path)

    artifacts = {path.name: path.read_text(encoding="utf-8") for path in tmp_path.iterdir()}
    assert set(artifacts) == {"eval-report.json", "replay.json", "report.md"}
    sentinels = (
        credentials["BUB_API_KEY"],
        credentials["OPENAI_API_KEY"],
        credentials["POWERCONTEXT_SERVER_AUTH_TOKEN"],
        "codex-auth-evidence-sentinel",
        "database-evidence-sentinel",
        *extra_sentinels,
    )
    for artifact_name, content in artifacts.items():
        for sentinel in sentinels:
            assert sentinel not in content, f"{sentinel!r} leaked into {artifact_name}"
        assert "[REDACTED]" in content

    assert "mysql+aoceanbase://" not in artifacts["replay.json"]
    assert "postgresql://" not in artifacts["replay.json"]
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
