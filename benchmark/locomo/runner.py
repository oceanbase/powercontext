"""Real PowerContext ingestion, retrieval, answering, and judging for LoCoMo."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any, Literal, TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai.models import infer_model
from pydantic_ai.settings import ModelSettings

from powercontext.builtin.artifacts.memory import (
    MEMORY_RERANK_INSTRUCTIONS_VERSION,
    MemoryHit,
    MemoryRerankMode,
    MemoryRerankTrace,
    memory_extraction_instructions_version,
)
from powercontext.builtin.inference.errors import InferenceTimeoutError, InferenceUnavailableError
from powercontext.builtin.inference.pydantic_ai import InferenceLimits, PydanticAIStructuredGenerator
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource, SearchMemoryRequest, open_builtin_runtime
from powercontext.server.settings import ServerSettings

from .dataset import LoCoMoConversation, LoCoMoDataset, LoCoMoQuestion, load_locomo, render_session
from .metrics import (
    bleu1,
    diagnose_observations,
    exact_match,
    retrieval_metrics,
    set_token_f1,
    summarize_observations,
    token_f1,
)
from .prompts import (
    ANSWER_INSTRUCTIONS,
    ANSWER_INSTRUCTIONS_VERSION,
    ANSWER_SOURCE_INSTRUCTIONS,
    ANSWER_SOURCE_INSTRUCTIONS_VERSION,
    JudgeProfile,
    judge_instructions,
)

Progress = Callable[[str], None]
ResultT = TypeVar("ResultT")
BENCHMARK_MODEL_SETTINGS = ModelSettings(temperature=0.0)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RetrievedMemory(_StrictModel):
    """One exact PowerContext search hit exposed to the answer model."""

    rank: int
    retrieval_rank: int
    text: str
    score: float
    matched_by: tuple[str, ...]
    source_ids: tuple[str, ...]
    source_dates: tuple[str, ...]


class AnswerSourceSession(_StrictModel):
    """One exact captured dialogue session cited by a selected Memory."""

    source_id: str
    date_time: str
    content: str


class AnswerInput(_StrictModel):
    """Question input that intentionally excludes the gold answer."""

    speaker_a: str
    speaker_b: str
    question: str
    memories: tuple[RetrievedMemory, ...]
    source_sessions: tuple[AnswerSourceSession, ...] = ()


class AnswerOutput(_StrictModel):
    answer: str = Field(min_length=1)


class JudgeInput(_StrictModel):
    question: str
    gold_answer: str
    generated_answer: str


class JudgeOutput(_StrictModel):
    label: Literal["CORRECT", "WRONG"]


def load_settings(env_file: Path) -> ServerSettings:
    """Load an explicit dotenv file without returning or logging secret values."""

    if not env_file.is_file():
        raise FileNotFoundError(env_file)
    load_dotenv(env_file, override=True)
    return ServerSettings()


def public_configuration(settings: ServerSettings) -> dict[str, Any]:
    """Return only benchmark-relevant non-secret configuration."""

    return {
        "database_kind": settings.database.kind,
        "generation_model": settings.inference.generation_model,
        "embedding_model": settings.inference.embedding_model,
        "embedding_profile_id": settings.inference.embedding_profile_id,
        "embedding_dimension": settings.inference.embedding_dimension,
        "embedding_normalization": settings.inference.embedding_normalization,
        "embedding_batch_size": settings.inference.embedding_batch_size,
        "memory_extraction_profile": settings.runtime.memory_extraction_profile.value,
        "memory_extraction_instructions": memory_extraction_instructions_version(
            settings.runtime.memory_extraction_profile
        ),
    }


def prepare_run(
    *,
    dataset: LoCoMoDataset,
    settings: ServerSettings,
    run_id: str,
    output_directory: Path,
    top_k: int,
    answer_k: int | None = None,
    rerank_mode: MemoryRerankMode = MemoryRerankMode.NONE,
    answer_source_content: bool = False,
    judge_profile: JudgeProfile = JudgeProfile.STRICT,
    categories: tuple[int, ...],
    conversation_limit: int | None,
    question_limit: int | None,
    operation_retries: int,
) -> dict[str, Any]:
    """Create or validate the immutable identity of a resumable run."""

    selected_answer_k = top_k if answer_k is None else answer_k
    if top_k < 1 or top_k > 50:
        raise ValueError("top_k must be between 1 and 50")  # noqa: TRY003
    if selected_answer_k < 1 or selected_answer_k > top_k:
        raise ValueError("answer_k must be between 1 and top_k")  # noqa: TRY003
    normalized_run_id = normalize_run_id(run_id)
    selected_conversations = (
        dataset.conversations if conversation_limit is None else dataset.conversations[:conversation_limit]
    )
    selected_questions = dataset.selected_questions(
        categories=categories,
        conversation_limit=conversation_limit,
        question_limit=question_limit,
    )
    manifest = {
        "schema": "powercontext.benchmark.locomo.run.v5",
        "run_id": normalized_run_id,
        "dataset_path": str(dataset.path),
        "dataset_sha256": dataset.sha256,
        "dataset_conversation_count": len(dataset.conversations),
        "dataset_session_count": len(dataset.sessions),
        "dataset_question_count": len(dataset.questions),
        "selected_conversation_count": len(selected_conversations),
        "selected_question_count": len(selected_questions),
        "categories": list(categories),
        "top_k": top_k,
        "candidate_k": top_k,
        "answer_k": selected_answer_k,
        "rerank_mode": rerank_mode.value,
        "rerank_instructions": (MEMORY_RERANK_INSTRUCTIONS_VERSION if rerank_mode is MemoryRerankMode.LLM else None),
        "answer_source_content": answer_source_content,
        "conversation_limit": conversation_limit,
        "question_limit": question_limit,
        "operation_retries": operation_retries,
        "generation_temperature": BENCHMARK_MODEL_SETTINGS["temperature"],
        "ingestion": "source-capture-and-memory-extraction",
        "retrieval_mode": "hybrid",
        "answer_instructions": (
            ANSWER_SOURCE_INSTRUCTIONS_VERSION if answer_source_content else ANSWER_INSTRUCTIONS_VERSION
        ),
        "judge_profile": judge_profile.value,
        "judge_instructions": judge_instructions(judge_profile)[1],
        "configuration": public_configuration(settings),
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    manifest_path = output_directory / "run.json"
    if manifest_path.is_file():
        observed = json.loads(manifest_path.read_text(encoding="utf-8"))
        if observed != manifest:
            raise ValueError(f"run manifest does not match requested benchmark: {manifest_path}")  # noqa: TRY003
    else:
        _write_json(manifest_path, manifest)
    return manifest


async def ingest_dataset(
    dataset: LoCoMoDataset,
    *,
    settings: ServerSettings,
    run_id: str,
    output_directory: Path,
    conversation_limit: int | None = None,
    concurrency: int = 4,
    operation_retries: int = 3,
    progress: Progress = print,
) -> dict[str, Any]:
    """Capture every session and extract Memory, resuming from persisted Source cursors."""

    if concurrency < 1:
        raise ValueError("ingestion concurrency must be positive")  # noqa: TRY003
    if operation_retries < 1:
        raise ValueError("operation_retries must be positive")  # noqa: TRY003
    conversations = dataset.conversations if conversation_limit is None else dataset.conversations[:conversation_limit]
    config = _runtime_config(settings, generation=True)
    started = perf_counter()
    total_sessions = sum(len(conversation.sessions) for conversation in conversations)
    completed_sessions = 0
    resumed_sessions = 0
    unchanged_flushes = 0
    transient_retries = 0
    lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(concurrency)
    conversation_results: dict[str, dict[str, Any]] = {}

    async with open_builtin_runtime(config) as runtime:

        async def ingest_conversation(conversation: LoCoMoConversation) -> None:
            nonlocal completed_sessions, resumed_sessions, transient_retries, unchanged_flushes
            async with semaphore:
                scope = scope_id(run_id, conversation.sample_id)
                source_app = runtime.sources.for_scope(scope)
                memory_app = runtime.memory.for_scope(scope)
                for session in conversation.sessions:
                    await source_app.capture(
                        CaptureSource(
                            source_id=session.session_id,
                            content=render_session(conversation, session),
                            metadata={
                                "benchmark": "locomo",
                                "dataset_sha256": dataset.sha256,
                                "sample_id": conversation.sample_id,
                                "session_id": session.session_id,
                                "date_time": session.date_time,
                            },
                        )
                    )
                cursor = await memory_app.cursor()
                if cursor.sequence > len(conversation.sessions):
                    raise RuntimeError(f"scope {scope} contains unexpected benchmark Sources")  # noqa: TRY003
                async with lock:
                    resumed_sessions += cursor.sequence
                    completed_sessions += cursor.sequence
                current_page = await memory_app.list()
                previous_revision = None if current_page.memory_ref is None else current_page.memory_ref.revision
                flush_durations: list[float] = []
                while cursor.sequence < len(conversation.sessions):
                    flush_started = perf_counter()
                    result, retry_count = await _retry_transient(
                        lambda: memory_app.flush(limit=1),
                        attempts=operation_retries,
                    )
                    async with lock:
                        transient_retries += retry_count
                    flush_durations.append((perf_counter() - flush_started) * 1_000)
                    if not result.processed or result.current_cursor != cursor.sequence + 1:
                        raise RuntimeError(f"scope {scope} did not advance exactly one Source")  # noqa: TRY003
                    current_revision = None if result.memory_ref is None else result.memory_ref.revision
                    if current_revision == previous_revision:
                        async with lock:
                            unchanged_flushes += 1
                    previous_revision = current_revision
                    cursor = await memory_app.cursor()
                    async with lock:
                        completed_sessions += 1
                        current = completed_sessions
                    progress(
                        f"[ingest] {current}/{total_sessions} sessions; {conversation.sample_id} {cursor.sequence}/{len(conversation.sessions)}"
                    )
                entries = await memory_app.list()
                conversation_results[conversation.sample_id] = {
                    "scope_id": scope,
                    "session_count": len(conversation.sessions),
                    "memory_entry_count": len(entries.entries),
                    "memory_revision": None if entries.memory_ref is None else entries.memory_ref.revision,
                    "flush_latency_ms_p50": _percentile(flush_durations, 0.50),
                    "flush_latency_ms_p95": _percentile(flush_durations, 0.95),
                }

        await asyncio.gather(*(ingest_conversation(conversation) for conversation in conversations))

    report = {
        "schema": "powercontext.benchmark.locomo.ingestion.v1",
        "run_id": normalize_run_id(run_id),
        "completed_at": datetime.now(UTC).isoformat(),
        "database_kind": settings.database.kind,
        "conversation_count": len(conversations),
        "session_count": total_sessions,
        "resumed_session_count": resumed_sessions,
        "newly_processed_session_count": total_sessions - resumed_sessions,
        "no_memory_change_flush_count": unchanged_flushes,
        "transient_retry_count": transient_retries,
        "memory_entry_count": sum(value["memory_entry_count"] for value in conversation_results.values()),
        "duration_seconds": perf_counter() - started,
        "conversations": dict(sorted(conversation_results.items())),
    }
    _write_json(output_directory / "ingestion.json", report)
    return report


async def evaluate_dataset(  # noqa: C901
    dataset: LoCoMoDataset,
    *,
    settings: ServerSettings,
    run_id: str,
    output_directory: Path,
    top_k: int = 30,
    answer_k: int | None = None,
    rerank_mode: MemoryRerankMode = MemoryRerankMode.NONE,
    answer_source_content: bool = False,
    judge_profile: JudgeProfile = JudgeProfile.STRICT,
    categories: tuple[int, ...] = (1, 2, 3, 4),
    conversation_limit: int | None = None,
    question_limit: int | None = None,
    concurrency: int = 8,
    operation_retries: int = 3,
    retry_errors: bool = True,
    progress: Progress = print,
) -> dict[str, Any]:
    """Run Hybrid retrieval, generated answers, and a same-model correctness judge."""

    if top_k < 1 or top_k > 50:
        raise ValueError("top_k must be between 1 and 50")  # noqa: TRY003
    selected_answer_k = top_k if answer_k is None else answer_k
    if selected_answer_k < 1 or selected_answer_k > top_k:
        raise ValueError("answer_k must be between 1 and top_k")  # noqa: TRY003
    if concurrency < 1:
        raise ValueError("evaluation concurrency must be positive")  # noqa: TRY003
    if operation_retries < 1:
        raise ValueError("operation_retries must be positive")  # noqa: TRY003
    generation_model = settings.inference.generation_model
    if generation_model is None:
        raise ValueError("LoCoMo answer evaluation requires a configured generation model")  # noqa: TRY003
    selected = dataset.selected_questions(
        categories=categories,
        conversation_limit=conversation_limit,
        question_limit=question_limit,
    )
    if not selected:
        raise ValueError("LoCoMo selection contains no questions")  # noqa: TRY003
    observations_path = output_directory / "observations.jsonl"
    observed = _read_observations(observations_path)
    pending = tuple(
        question
        for question in selected
        if question.question_id not in observed
        or (retry_errors and observed[question.question_id].get("status") != "ok")
    )
    progress(f"[evaluate] selected={len(selected)} resumed={len(selected) - len(pending)} pending={len(pending)}")
    if pending:
        config = _runtime_config(
            settings,
            generation=False,
            rerank_mode=rerank_mode,
            rerank_candidate_limit=top_k,
        )
        limits = InferenceLimits(
            timeout_seconds=settings.inference.generation_timeout_seconds,
            max_requests=settings.inference.generation_max_requests,
        )
        conversation_by_id = {conversation.sample_id: conversation for conversation in dataset.conversations}
        semaphore = asyncio.Semaphore(concurrency)
        async with AsyncExitStack() as resources:
            runtime = await resources.enter_async_context(open_builtin_runtime(config))
            model = await resources.enter_async_context(infer_model(generation_model))
            answer_generator = PydanticAIStructuredGenerator(
                model=model,
                instructions=ANSWER_SOURCE_INSTRUCTIONS if answer_source_content else ANSWER_INSTRUCTIONS,
                input_type=AnswerInput,
                output_type=AnswerOutput,
                limits=limits,
                model_settings=BENCHMARK_MODEL_SETTINGS,
            )
            judge_generator = PydanticAIStructuredGenerator(
                model=model,
                instructions=judge_instructions(judge_profile)[0],
                input_type=JudgeInput,
                output_type=JudgeOutput,
                limits=limits,
                model_settings=BENCHMARK_MODEL_SETTINGS,
            )
            entry_sources = await _entry_source_maps(runtime, dataset, run_id, conversation_limit)

            async def evaluate_one(question: LoCoMoQuestion) -> dict[str, Any]:
                async with semaphore:
                    return await _evaluate_question(
                        runtime=runtime,
                        answer_generator=answer_generator,
                        judge_generator=judge_generator,
                        question=question,
                        conversation=conversation_by_id[question.sample_id],
                        entry_sources=entry_sources[question.sample_id],
                        run_id=run_id,
                        top_k=top_k,
                        answer_k=selected_answer_k,
                        rerank_mode=rerank_mode,
                        answer_source_content=answer_source_content,
                        operation_retries=operation_retries,
                    )

            tasks = tuple(asyncio.create_task(evaluate_one(question)) for question in pending)
            completed = len(selected) - len(pending)
            for task in asyncio.as_completed(tasks):
                observation = await task
                _append_jsonl(observations_path, observation)
                observed[observation["question_id"]] = observation
                completed += 1
                if completed % 10 == 0 or completed == len(selected):
                    error_count = sum(
                        observed[question.question_id].get("status") != "ok"
                        for question in selected
                        if question.question_id in observed
                    )
                    progress(f"[evaluate] {completed}/{len(selected)} questions; errors={error_count}")

    ordered = tuple(observed[question.question_id] for question in selected if question.question_id in observed)
    if len(ordered) != len(selected):
        raise RuntimeError("evaluation did not produce every selected observation")  # noqa: TRY003
    summary = {
        "schema": "powercontext.benchmark.locomo.summary.v1",
        "run_id": normalize_run_id(run_id),
        "completed_at": datetime.now(UTC).isoformat(),
        "question_count": len(ordered),
        "top_k": top_k,
        "candidate_k": top_k,
        "answer_k": selected_answer_k,
        "rerank_mode": rerank_mode.value,
        "answer_source_content": answer_source_content,
        "judge_profile": judge_profile.value,
        "categories": list(categories),
        "metrics": summarize_observations(ordered),
        "diagnostics": diagnose_observations(ordered),
        "metric_notes": {
            "llm_judge": "Same configured model answers and judges; this is not an independent human label.",
            "evidence": "Session-level Source provenance (D1, D2, ...), which is looser than LoCoMo turn-level evidence.",
            "candidate_evidence": "Candidate evidence scores the coarse retrieval pool before reranking or truncation.",
            "errors": "Failed questions remain in the denominator and score zero.",
            "category_5": "Excluded by the scored-set contract, which includes categories 1-4.",
        },
    }
    _write_json(output_directory / "summary.json", summary)
    (output_directory / "summary.md").write_text(render_summary(summary), encoding="utf-8")
    return summary


def render_summary(summary: Mapping[str, Any]) -> str:
    """Render a compact human-readable result with explicit metric caveats."""

    metrics = summary["metrics"]
    overall = metrics["overall"]
    lines = [
        "# PowerContext LoCoMo benchmark result",
        "",
        f"- Run: `{summary['run_id']}`",
        f"- Questions: `{overall['question_count']}` (errors: `{overall['error_count']}`)",
        f"- LLM-judge accuracy: `{overall['llm_judge']:.4f}`",
        f"- Reference-compatible set-token F1: `{overall['reference_set_f1']:.4f}`",
        f"- Normalized token F1: `{overall['token_f1']:.4f}`",
        f"- Exact match: `{overall['exact_match']:.4f}`",
        f"- BLEU-1: `{overall['bleu1']:.4f}`",
        f"- Coarse candidates / Answer context: `{summary['candidate_k']}` / `{summary['answer_k']}`",
        f"- Rerank mode: `{summary['rerank_mode']}`",
        f"- Exact cited Source expansion: `{summary['answer_source_content']}`",
        f"- Judge profile: `{summary['judge_profile']}`",
        f"- Answer-context evidence Hit: `{overall['evidence_hit']:.4f}`",
        f"- Answer-context evidence Recall: `{overall['evidence_recall']:.4f}`",
        f"- Answer-context evidence MRR: `{overall['evidence_mrr']:.4f}`",
        f"- Candidate evidence Hit@{summary['candidate_k']}: `{overall['candidate_evidence_hit']:.4f}`",
        "",
        "| Category | Questions | Judge accuracy | Set F1 | Evidence hit | Evidence recall |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, value in metrics.items():
        if not name.startswith("category_"):
            continue
        lines.append(
            f"| {name.removeprefix('category_')} | {value['question_count']} | {value['llm_judge']:.4f} | "
            f"{value['reference_set_f1']:.4f} | {value['evidence_hit']:.4f} | {value['evidence_recall']:.4f} |"
        )
    lines.extend([
        "",
        "## Interpretation boundaries",
        "",
        f"- {summary['metric_notes']['llm_judge']}",
        f"- {summary['metric_notes']['evidence']}",
        f"- {summary['metric_notes']['candidate_evidence']}",
        f"- {summary['metric_notes']['errors']}",
        f"- {summary['metric_notes']['category_5']}",
        "",
    ])
    return "\n".join(lines)


async def _evaluate_question(
    *,
    runtime,
    answer_generator: PydanticAIStructuredGenerator[AnswerInput, AnswerOutput],
    judge_generator: PydanticAIStructuredGenerator[JudgeInput, JudgeOutput],
    question: LoCoMoQuestion,
    conversation: LoCoMoConversation,
    entry_sources: Mapping[tuple[str, str], tuple[str, ...]],
    run_id: str,
    top_k: int,
    answer_k: int,
    rerank_mode: MemoryRerankMode,
    answer_source_content: bool,
    operation_retries: int,
) -> dict[str, Any]:
    total_started = perf_counter()
    phase = "search"
    try:
        search_started = perf_counter()
        result, search_retries = await _retry_transient(
            lambda: runtime.memory.for_scope(scope_id(run_id, question.sample_id)).search(
                SearchMemoryRequest(
                    query=question.question,
                    limit=answer_k if rerank_mode is MemoryRerankMode.LLM else top_k,
                    mode="hybrid",
                )
            ),
            attempts=operation_retries,
        )
        search_and_rerank_latency = (perf_counter() - search_started) * 1_000
        dates = {session.session_id: session.date_time for session in conversation.sessions}
        candidate_hits = result.hits if result.rerank is None else result.rerank.candidate_hits
        retrieval_rank_by_hit = {
            (hit.entry_id, hit.entry_version_id): rank for rank, hit in enumerate(candidate_hits, start=1)
        }
        candidates = tuple(
            _retrieved_memory(
                hit=hit,
                rank=rank,
                retrieval_rank=rank,
                entry_sources=entry_sources,
                dates=dates,
            )
            for rank, hit in enumerate(candidate_hits, start=1)
        )
        selected_hits = result.hits if result.rerank is not None else result.hits[:answer_k]
        memories = tuple(
            _retrieved_memory(
                hit=hit,
                rank=rank,
                retrieval_rank=retrieval_rank_by_hit[(hit.entry_id, hit.entry_version_id)],
                entry_sources=entry_sources,
                dates=dates,
            )
            for rank, hit in enumerate(selected_hits, start=1)
        )
        rerank_latency = 0.0 if result.rerank is None else result.rerank.latency_ms
        search_latency = max(search_and_rerank_latency - rerank_latency, 0.0)
        rerank_metadata = _rerank_metadata(rerank_mode, result.rerank, len(candidates), len(memories))
        rerank_usage = _empty_usage() if result.rerank is None else result.rerank.usage.model_dump(mode="json")
        source_sessions = _answer_source_sessions(conversation, memories) if answer_source_content else ()
        phase = "answer"
        answer_started = perf_counter()
        answer_result, answer_retries = await _retry_transient(
            lambda: answer_generator.generate(
                AnswerInput(
                    speaker_a=conversation.speaker_a,
                    speaker_b=conversation.speaker_b,
                    question=question.question,
                    memories=memories,
                    source_sessions=source_sessions,
                )
            ),
            attempts=operation_retries,
        )
        answer_latency = (perf_counter() - answer_started) * 1_000
        generated_answer = answer_result.output.answer.strip()
        phase = "judge"
        judge_started = perf_counter()
        judge_result, judge_retries = await _retry_transient(
            lambda: judge_generator.generate(
                JudgeInput(
                    question=question.question,
                    gold_answer=question.answer,
                    generated_answer=generated_answer,
                )
            ),
            attempts=operation_retries,
        )
        judge_latency = (perf_counter() - judge_started) * 1_000
        evidence_scores = retrieval_metrics(
            evidence_sessions=question.evidence_sessions,
            hit_source_ids=tuple(memory.source_ids for memory in memories),
        )
        candidate_evidence_scores = retrieval_metrics(
            evidence_sessions=question.evidence_sessions,
            hit_source_ids=tuple(memory.source_ids for memory in candidates),
        )
        return {
            "schema": "powercontext.benchmark.locomo.observation.v2",
            "question_id": question.question_id,
            "sample_id": question.sample_id,
            "category": question.category,
            "question": question.question,
            "gold_answer": question.answer,
            "generated_answer": generated_answer,
            "evidence_raw": list(question.evidence_raw),
            "evidence": list(question.evidence),
            "evidence_sessions": list(question.evidence_sessions),
            "status": "ok",
            "retrieval_mode": result.mode,
            "candidate_hits": [memory.model_dump(mode="json") for memory in candidates],
            "hits": [memory.model_dump(mode="json") for memory in memories],
            "rerank": rerank_metadata,
            "answer_context": {
                "source_content": answer_source_content,
                "source_ids": [source.source_id for source in source_sessions],
            },
            "metrics": {
                "exact_match": exact_match(generated_answer, question.answer),
                "token_f1": token_f1(generated_answer, question.answer),
                "reference_set_f1": set_token_f1(generated_answer, question.answer),
                "bleu1": bleu1(generated_answer, question.answer),
                "llm_judge": float(judge_result.output.label == "CORRECT"),
                **evidence_scores,
                **{f"candidate_{name}": score for name, score in candidate_evidence_scores.items()},
            },
            "latency_ms": {
                "search": search_latency,
                "rerank": rerank_latency,
                "answer": answer_latency,
                "judge": judge_latency,
                "total": (perf_counter() - total_started) * 1_000,
            },
            "usage": {
                "rerank": rerank_usage,
                "answer": answer_result.usage.model_dump(mode="json"),
                "judge": judge_result.usage.model_dump(mode="json"),
            },
            "transient_retries": {
                "search": search_retries,
                "rerank": 0,
                "answer": answer_retries,
                "judge": judge_retries,
            },
        }
    except Exception as error:  # Each failed benchmark item remains an explicit zero in the denominator.
        return {
            "schema": "powercontext.benchmark.locomo.observation.v2",
            "question_id": question.question_id,
            "sample_id": question.sample_id,
            "category": question.category,
            "question": question.question,
            "gold_answer": question.answer,
            "status": "error",
            "error_type": type(error).__name__,
            "error_stage": phase,
            "latency_ms": {"total": (perf_counter() - total_started) * 1_000},
        }


def _rerank_metadata(
    mode: MemoryRerankMode,
    trace: MemoryRerankTrace | None,
    candidate_count: int,
    answer_count: int,
) -> dict[str, Any]:
    if trace is None:
        return {
            "mode": mode.value,
            "policy_id": None,
            "candidate_count": candidate_count,
            "answer_count": answer_count,
            "selected_retrieval_ranks": list(range(1, answer_count + 1)),
            "discarded_rank_count": 0,
            "used_fallback": False,
        }
    return {
        "mode": mode.value,
        "policy_id": trace.policy_id,
        "candidate_count": candidate_count,
        "answer_count": answer_count,
        "selected_retrieval_ranks": list(trace.selected_ranks),
        "discarded_rank_count": trace.discarded_rank_count,
        "used_fallback": trace.used_fallback,
    }


def _retrieved_memory(
    *,
    hit: MemoryHit,
    rank: int,
    retrieval_rank: int,
    entry_sources: Mapping[tuple[str, str], tuple[str, ...]],
    dates: Mapping[str, str],
) -> RetrievedMemory:
    source_ids = entry_sources.get((hit.entry_id, hit.entry_version_id), ())
    return RetrievedMemory(
        rank=rank,
        retrieval_rank=retrieval_rank,
        text=hit.text,
        score=hit.score,
        matched_by=hit.matched_by,
        source_ids=source_ids,
        source_dates=tuple(
            dates[source_id.rsplit(":", maxsplit=1)[-1]]
            for source_id in source_ids
            if source_id.rsplit(":", maxsplit=1)[-1] in dates
        ),
    )


def _empty_usage() -> dict[str, int | None]:
    return {"requests": 0, "input_tokens": 0, "output_tokens": 0}


def _answer_source_sessions(
    conversation: LoCoMoConversation,
    memories: tuple[RetrievedMemory, ...],
) -> tuple[AnswerSourceSession, ...]:
    """Expand only the exact Source sessions cited by selected Memory entries."""

    sessions = {session.session_id: session for session in conversation.sessions}
    selected_ids: list[str] = []
    for memory in memories:
        for source_id in memory.source_ids:
            session_id = source_id.rsplit(":", maxsplit=1)[-1]
            if session_id in sessions and session_id not in selected_ids:
                selected_ids.append(session_id)
    return tuple(
        AnswerSourceSession(
            source_id=session_id,
            date_time=sessions[session_id].date_time,
            content=render_session(conversation, sessions[session_id]),
        )
        for session_id in selected_ids
    )


async def _entry_source_maps(runtime, dataset: LoCoMoDataset, run_id: str, conversation_limit: int | None):
    conversations = dataset.conversations if conversation_limit is None else dataset.conversations[:conversation_limit]
    mappings: dict[str, dict[tuple[str, str], tuple[str, ...]]] = {}
    for conversation in conversations:
        page = await runtime.memory.for_scope(scope_id(run_id, conversation.sample_id)).list()
        mappings[conversation.sample_id] = {
            (record.entry.entry_id, record.entry.entry_version_id): tuple(
                source.source_id for source in record.entry.sources
            )
            for record in page.entries
        }
    return mappings


def _runtime_config(
    settings: ServerSettings,
    *,
    generation: bool,
    rerank_mode: MemoryRerankMode = MemoryRerankMode.NONE,
    rerank_candidate_limit: int = 30,
) -> BuiltinConfig:
    rerank_enabled = rerank_mode is MemoryRerankMode.LLM
    inference = (
        settings.inference
        if generation or rerank_enabled
        else settings.inference.model_copy(update={"generation_model": None})
    )
    return BuiltinConfig(
        runtime=settings.runtime.model_copy(
            update={
                "schedule_seconds": None,
                "experience_schedule_seconds": None,
                "memory_rerank_enabled": rerank_enabled,
                "memory_rerank_candidate_limit": rerank_candidate_limit,
            }
        ),
        database=settings.database,
        inference=inference,
        external_skills=settings.external_skills,
    )


async def _retry_transient(action: Callable[[], Awaitable[ResultT]], *, attempts: int) -> tuple[ResultT, int]:
    """Retry only stable transient inference failures; stateful flushes remain cursor-idempotent."""

    for attempt in range(1, attempts + 1):
        try:
            return await action(), attempt - 1
        except (InferenceTimeoutError, InferenceUnavailableError):
            if attempt == attempts:
                raise
            await asyncio.sleep(min(2 ** (attempt - 1), 8))
    raise AssertionError


def scope_id(run_id: str, sample_id: str) -> str:
    """Return the isolated, stable PowerContext namespace for one conversation."""

    return f"benchmark:locomo:{normalize_run_id(run_id)}:{sample_id}"


def normalize_run_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip()).strip("-")
    if not normalized:
        raise ValueError("run_id must contain letters or digits")  # noqa: TRY003
    if len(normalized) > 160:
        raise ValueError("run_id must not exceed 160 normalized characters")  # noqa: TRY003
    return normalized


def _read_observations(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    values: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        question_id = value.get("question_id")
        if not isinstance(question_id, str):
            raise TypeError(f"observation line {line_number} has no question ID")  # noqa: TRY003
        values[question_id] = value
    return values


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True))
        stream.write("\n")
        stream.flush()


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _percentile(values: Sequence[float], percentage: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentage
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def load_benchmark_dataset(path: Path) -> LoCoMoDataset:
    """Public loader alias used by the CLI and tests."""

    return load_locomo(path)


__all__ = [
    "evaluate_dataset",
    "ingest_dataset",
    "load_benchmark_dataset",
    "load_settings",
    "normalize_run_id",
    "prepare_run",
    "public_configuration",
    "render_summary",
    "scope_id",
]
