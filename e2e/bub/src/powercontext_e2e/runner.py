"""Run and evaluate complete Bub session replays."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from bub import BubFramework
from bub.channels.message import ChannelMessage
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from powercontext.client import PowerContextClient
from powercontext.http import ListMemoryEntriesRequest, PrepareContextRequest
from pydantic_ai.exceptions import AgentRunError
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluationReason, Evaluator, EvaluatorContext
from pydantic_evals.evaluators.llm_as_a_judge import judge_input_output
from pydantic_evals.reporting import EvaluationReport

from .models import (
    MemoryEntrySnapshot,
    MemorySnapshot,
    PreparedContextSnapshot,
    ReplayObservation,
    RunEnvironment,
    ScenarioSpec,
    SessionObservation,
)

Mode = Literal["acceptance", "live", "offline-rescore"]
Report = EvaluationReport[ScenarioSpec, ReplayObservation, dict[str, str]]
Context = EvaluatorContext[ScenarioSpec, ReplayObservation, dict[str, str]]


@dataclass
class ReplayEvaluator(Evaluator[ScenarioSpec, ReplayObservation, dict[str, str]]):
    """Score public behavior and attach Pydantic Evals' native span tree."""

    judge_model: OpenAIChatModel | str | None = None

    async def evaluate(self, ctx: Context) -> dict[str, bool | float | str | EvaluationReason]:
        if not ctx.output.spans:
            ctx.output.spans = tuple(span for span in ctx.span_tree if span.name.startswith("bub."))

        ctx.attributes.update({
            "commit": ctx.output.environment.commit,
            "database": ctx.output.environment.database,
            "mode": ctx.output.environment.mode,
            "run_id": ctx.output.run_id,
        })
        ctx.metrics.update({
            "memory_entries_after": len(ctx.output.memory_after.entries),
            "model_calls": sum(span.name == "bub.model" for span in ctx.output.spans),
            "sessions_completed": sum(session.status == "completed" for session in ctx.output.sessions),
            "span_count": len(ctx.output.spans),
        })

        observed_by_id = {session.id: session for session in ctx.output.sessions}
        expected_ids = [session.id for session in ctx.inputs.sessions]
        observed_ids = [session.id for session in ctx.output.sessions]
        results: dict[str, bool | float | str | EvaluationReason] = {
            "run_completed": EvaluationReason(
                value=ctx.output.status == "completed",
                reason=None if ctx.output.status == "completed" else "; ".join(ctx.output.errors),
            ),
            "sessions_completed": EvaluationReason(
                value=observed_ids == expected_ids,
                reason=None
                if observed_ids == expected_ids
                else f"Expected {expected_ids!r}, observed {observed_ids!r}.",
            ),
        }
        if ctx.output.environment.mode == "live":
            results["agent_trace_recorded"] = any(span.name == "bub.agent" for span in ctx.output.spans)
            results["model_trace_recorded"] = any(span.name == "bub.model" for span in ctx.output.spans)

        for session_spec in ctx.inputs.sessions:
            observed = observed_by_id.get(session_spec.id)
            if observed is None:
                continue
            results[f"{session_spec.id}_completed"] = EvaluationReason(
                value=observed.status == "completed",
                reason=observed.error,
            )
            if session_spec.expected_memory:
                results[f"{session_spec.id}_memory"] = _contains_fragments(
                    [entry.text for entry in observed.memory_after.entries],
                    session_spec.expected_memory,
                    "Memory",
                )
            if session_spec.expected_context:
                results[f"{session_spec.id}_context"] = _contains_fragments(
                    [observed.prepared_context.content],
                    session_spec.expected_context,
                    "Prepared context",
                )
            if session_spec.expected_answer and ctx.output.environment.mode == "live":
                results.update(await self._answer_quality(session_spec.input, session_spec.expected_answer, observed))
        return results

    async def _answer_quality(
        self,
        question: str,
        expected_answer: str,
        observed: SessionObservation,
    ) -> dict[str, float | str | EvaluationReason]:
        prefix = f"{observed.id}_answer"
        lexical_score = _token_recall(expected_answer, observed.output)
        results: dict[str, float | str | EvaluationReason] = {f"{prefix}_token_recall": lexical_score}
        if self.judge_model is None:
            results[f"{prefix}_judge"] = EvaluationReason(
                value="not_configured",
                reason="Set POWERCONTEXT_E2E_JUDGE_MODEL or use an OpenAI-compatible BUB_MODEL.",
            )
            return results

        try:
            grading = await judge_input_output(
                {"question": question, "expected_answer": expected_answer},
                {"answer": observed.output},
                "Judge whether the answer conveys the expected fact without requiring matching wording.",
                model=self.judge_model,
            )
        except AgentRunError as exc:
            results[f"{prefix}_judge"] = EvaluationReason(value="unavailable", reason=f"{type(exc).__name__}: {exc}")
            return results
        results[f"{prefix}_judge"] = EvaluationReason(
            value="pass" if grading.pass_ else "fail",
            reason=grading.reason,
        )
        results[f"{prefix}_judge_score"] = EvaluationReason(value=grading.score, reason=grading.reason)
        return results


async def evaluate_scenario(
    scenario: ScenarioSpec,
    *,
    mode: Literal["acceptance", "live"],
    output_dir: Path,
) -> bool:
    _configure_tracing()
    judge = _judge_model() if mode == "live" else None
    evaluator = ReplayEvaluator(judge_model=judge)
    dataset = Dataset[ScenarioSpec, ReplayObservation, dict[str, str]](
        name="powercontext-session-replay",
        cases=[Case(name=scenario.id, inputs=scenario, metadata={"mode": mode})],
        evaluators=[evaluator],
    )

    async def run(inputs: ScenarioSpec) -> ReplayObservation:
        return await _run_replay(inputs, mode=mode, judge_model=_judge_model_name(judge))

    report = await dataset.evaluate(run, name=f"{mode}:{scenario.id}", max_concurrency=1, progress=False)
    observation = report.cases[0].output
    write_artifacts(observation, report, output_dir)
    return not report.failures and all(result.value for result in report.cases[0].assertions.values())


async def rescore_replay(replay_path: Path, output_dir: Path) -> bool:
    _configure_tracing()
    observation = ReplayObservation.model_validate_json(replay_path.read_text(encoding="utf-8"))
    environment = observation.environment.model_copy(update={"mode": "offline-rescore"})
    observation = observation.model_copy(update={"environment": environment})
    evaluator = ReplayEvaluator(judge_model=_judge_model())
    dataset = Dataset[ScenarioSpec, ReplayObservation, dict[str, str]](
        name="powercontext-session-replay",
        cases=[Case(name=observation.scenario.id, inputs=observation.scenario, metadata={"mode": "offline-rescore"})],
        evaluators=[evaluator],
    )

    async def recorded(_: ScenarioSpec) -> ReplayObservation:
        return observation

    report = await dataset.evaluate(recorded, name=f"offline:{observation.scenario.id}", progress=False)
    write_artifacts(report.cases[0].output, report, output_dir)
    return not report.failures and all(result.value for result in report.cases[0].assertions.values())


async def _run_replay(
    scenario: ScenarioSpec,
    *,
    mode: Literal["acceptance", "live"],
    judge_model: str | None,
) -> ReplayObservation:
    run_id = f"{scenario.id}-{uuid4().hex[:12]}"
    scope_id = f"e2e:{run_id}"
    base_url = os.getenv("POWERCONTEXT_BUB_BASE_URL", "http://127.0.0.1:8000")
    workspace = Path(os.getenv("POWERCONTEXT_E2E_WORKSPACE", ".powercontext/e2e-workspace")) / run_id
    workspace.mkdir(parents=True, exist_ok=True)
    os.environ["POWERCONTEXT_BUB_SCOPE_ID"] = scope_id

    errors: list[str] = []
    observations: list[SessionObservation] = []
    memory_before = MemorySnapshot()
    try:
        async with PowerContextClient(base_url, timeout=20) as client:
            await client.get_readiness()
            memory_before = await _memory_snapshot(client, scope_id)

            for index, session in enumerate(scenario.sessions):
                prepared = await _prepared_context(client, scope_id, session.input)
                agent_session_id = f"e2e:{run_id}:{index}:{session.id}"
                try:
                    output = await _run_bub_session(
                        session.input,
                        agent_session_id=agent_session_id,
                        mode=mode,
                        remember=bool(session.expected_memory),
                        workspace=workspace,
                    )
                    status = "completed"
                    error = None
                except Exception as exc:
                    output = ""
                    status = "failed"
                    error = _redact(f"{type(exc).__name__}: {exc}")
                    errors.append(f"Session {session.id}: {error}")
                memory_after_session = await _memory_snapshot(client, scope_id)
                observations.append(
                    SessionObservation(
                        id=session.id,
                        agent_session_id=agent_session_id,
                        status=status,
                        error=error,
                        prepared_context=prepared,
                        output=_redact(output),
                        memory_after=memory_after_session,
                    )
                )
                if status == "failed":
                    break
            memory_after = await _memory_snapshot(client, scope_id)
    except Exception as exc:
        errors.append(_redact(f"{type(exc).__name__}: {exc}"))
        memory_after = observations[-1].memory_after if observations else memory_before

    return ReplayObservation(
        run_id=run_id,
        environment=RunEnvironment(
            mode=mode,
            commit=_commit(),
            database=os.getenv("POWERCONTEXT_E2E_DATABASE", "unknown"),
            agent_model=os.getenv("BUB_MODEL") if mode == "live" else None,
            generation_model=os.getenv("POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL"),
            embedding_profile=os.getenv("POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID"),
            judge_model=judge_model,
            started_at=datetime.now(UTC),
        ),
        scenario=scenario,
        status="completed" if len(observations) == len(scenario.sessions) and not errors else "failed",
        errors=tuple(errors),
        memory_before=memory_before,
        memory_after=memory_after,
        sessions=tuple(observations),
    )


async def _run_bub_session(
    user_input: str,
    *,
    agent_session_id: str,
    mode: Literal["acceptance", "live"],
    remember: bool,
    workspace: Path,
) -> str:
    framework = BubFramework(config_file=workspace / "bub.yml")
    framework.workspace = workspace
    framework.load_hooks()

    if mode == "acceptance":
        tool_name = "powercontext.remember" if remember else "powercontext.context"
        argument_name = "text" if remember else "query"
        user_input = f",{tool_name} {argument_name}={shlex.quote(user_input)}"

    tracer = trace.get_tracer("powercontext.e2e")
    with tracer.start_as_current_span("bub.agent") as span:
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        span.set_attribute("input.value", user_input)
        span.set_attribute("bub.session_id", agent_session_id)
        async with framework.running():
            result = await framework.process_inbound(
                ChannelMessage(
                    channel="cli",
                    chat_id=agent_session_id,
                    session_id=agent_session_id,
                    content=user_input,
                )
            )
        span.set_attribute("output.value", result.model_output)
    return result.model_output


async def _memory_snapshot(client: PowerContextClient, scope_id: str) -> MemorySnapshot:
    response = await client.list_memory_entries(ListMemoryEntriesRequest(scope_id=scope_id))
    return MemorySnapshot(
        entries=tuple(
            MemoryEntrySnapshot(
                entry_id=entry.citation.entry_id,
                entry_version_id=entry.citation.entry_version_id,
                version=entry.version,
                kind=entry.kind,
                text=entry.text,
                state=entry.state.value,
            )
            for entry in response.entries
        )
    )


async def _prepared_context(client: PowerContextClient, scope_id: str, query: str) -> PreparedContextSnapshot:
    prepared = await client.prepare_context(PrepareContextRequest(scope_id=scope_id, query=query))
    return PreparedContextSnapshot(status=prepared.status.value, content=prepared.content or "")


def _contains_fragments(values: list[str], expected: tuple[str, ...], label: str) -> EvaluationReason:
    folded_values = [value.casefold() for value in values]
    missing = [fragment for fragment in expected if not any(fragment.casefold() in value for value in folded_values)]
    return EvaluationReason(
        value=not missing,
        reason=None if not missing else f"{label} did not contain {missing!r}.",
    )


def _token_recall(expected: str, actual: str) -> float:
    expected_tokens = set(expected.casefold().split())
    actual_tokens = set(actual.casefold().split())
    return 1.0 if not expected_tokens else len(expected_tokens & actual_tokens) / len(expected_tokens)


def _judge_model() -> OpenAIChatModel | str | None:
    configured = os.getenv("POWERCONTEXT_E2E_JUDGE_MODEL")
    model = configured or os.getenv("BUB_MODEL")
    if not model:
        return None
    if configured and configured != os.getenv("BUB_MODEL"):
        return configured
    provider, separator, model_name = model.partition(":")
    if separator and provider == "openai" and os.getenv("BUB_API_KEY"):
        return OpenAIChatModel(
            model_name,
            provider=OpenAIProvider(
                api_key=os.environ["BUB_API_KEY"],
                base_url=os.getenv("BUB_API_BASE"),
            ),
        )
    if separator and provider == "deepseek" and os.getenv("BUB_API_KEY"):
        os.environ.setdefault("DEEPSEEK_API_KEY", os.environ["BUB_API_KEY"])
        return model
    return None


def _judge_model_name(model: OpenAIChatModel | str | None) -> str | None:
    if model is None:
        return None
    if isinstance(model, str):
        return model
    return f"openai:{model.model_name}"


def _configure_tracing() -> None:
    provider = trace.get_tracer_provider()
    if not hasattr(provider, "add_span_processor"):
        trace.set_tracer_provider(TracerProvider())


def _commit() -> str:
    configured = os.getenv("GITHUB_SHA")
    if configured:
        return configured
    git = shutil.which("git")
    if git is None:
        return "unknown"
    completed = subprocess.run(  # noqa: S603 - executable is resolved by shutil.which
        [git, "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _redact(value: str) -> str:
    secret = os.getenv("BUB_API_KEY")
    return value.replace(secret, "[REDACTED]") if secret else value


def write_artifacts(observation: ReplayObservation, report: Report, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "replay.json").write_text(
        observation.model_dump_json(by_alias=True, indent=2) + "\n",
        encoding="utf-8",
    )

    cases = [
        {
            "name": case.name,
            "assertions": {
                name: {"value": result.value, "reason": result.reason}
                for name, result in sorted(case.assertions.items())
            },
            "scores": {
                name: {"value": result.value, "reason": result.reason} for name, result in sorted(case.scores.items())
            },
            "labels": {
                name: {"value": result.value, "reason": result.reason} for name, result in sorted(case.labels.items())
            },
            "metrics": dict(sorted(case.metrics.items())),
            "attributes": case.attributes,
            "task_duration": case.task_duration,
            "total_duration": case.total_duration,
        }
        for case in report.cases
    ]
    report_payload: dict[str, Any] = {
        "schema": "powercontext.session-replay-evaluation/v1",
        "experiment": report.name,
        "cases": cases,
        "failures": [{"name": failure.name, "error": failure.error_message} for failure in report.failures],
    }
    (output_dir / "eval-report.json").write_text(
        json.dumps(report_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "report.md").write_text(
        "# PowerContext session replay\n\n"
        f"- Scenario: `{observation.scenario.id}`\n"
        f"- Mode: `{observation.environment.mode}`\n"
        f"- Database: `{observation.environment.database}`\n"
        f"- Status: `{observation.status}`\n\n"
        "## Evaluation\n\n"
        f"```text\n{report.render(include_reasons=True)}\n```\n",
        encoding="utf-8",
    )
