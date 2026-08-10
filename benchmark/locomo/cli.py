"""Command line entry point for the PowerContext LoCoMo benchmark."""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from powercontext.builtin.artifacts.memory import MemoryRerankMode
from powercontext.builtin.runtime import MemoryExtractionProfile

from .prompts import JudgeProfile
from .runner import (
    evaluate_dataset,
    ingest_dataset,
    load_benchmark_dataset,
    load_settings,
    normalize_run_id,
    prepare_rejudge,
    prepare_run,
    public_configuration,
    rejudge_dataset,
)

BENCHMARK_DIRECTORY = Path(__file__).resolve().parent
DEFAULT_DATASET = BENCHMARK_DIRECTORY / "dataset" / "locomo10.json"
DEFAULT_RESULTS = BENCHMARK_DIRECTORY / "results"


def main(argv: list[str] | None = None) -> int:
    """Parse and execute one benchmark command."""

    arguments = _parser().parse_args(argv)
    return int(arguments.handler(arguments))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m benchmark.locomo",
        description="Run LoCoMo through PowerContext Source, Memory, Hybrid retrieval, answer, and judge stages.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="validate the dataset and print non-secret configured capabilities")
    inspect.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    inspect.add_argument("--env-file", type=Path, default=Path(".env"))
    inspect.set_defaults(handler=_inspect)

    run = commands.add_parser("run", help="run or resume ingestion and answer evaluation")
    run.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    run.add_argument("--env-file", type=Path, default=Path(".env"))
    run.add_argument("--run-id", default=None, help="stable namespace used for database state and result resume")
    run.add_argument("--output-directory", type=Path, default=None)
    run.add_argument("--top-k", type=int, default=30)
    run.add_argument(
        "--answer-k",
        type=_positive_int,
        default=None,
        help="maximum memories sent to the answer model; defaults to --top-k",
    )
    run.add_argument(
        "--rerank-mode",
        type=MemoryRerankMode,
        choices=tuple(MemoryRerankMode),
        default=MemoryRerankMode.NONE,
        help="PowerContext Memory search policy; llm enables the runtime listwise reranker",
    )
    run.add_argument(
        "--answer-source-content",
        action="store_true",
        help="expand selected Memory citations with their exact captured dialogue sessions",
    )
    answer_inference = run.add_mutually_exclusive_group()
    answer_inference.add_argument(
        "--answer-inference-aware",
        action="store_true",
        help="use the inference Answer policy for every question; requires --answer-source-content",
    )
    answer_inference.add_argument(
        "--answer-unknown-fallback-inference",
        action="store_true",
        help=(
            "retry only an exact normalized Unknown answer with the inference Answer policy; "
            "requires --answer-source-content"
        ),
    )
    run.add_argument(
        "--judge-profile",
        type=JudgeProfile,
        choices=tuple(JudgeProfile),
        default=JudgeProfile.STRICT,
        help="LLM correctness policy; strict remains the default",
    )
    run.add_argument(
        "--memory-extraction-profile",
        type=MemoryExtractionProfile,
        choices=tuple(MemoryExtractionProfile),
        default=None,
        help="override the Server runtime profile for this isolated run",
    )
    run.add_argument("--categories", type=_categories, default=(1, 2, 3, 4))
    run.add_argument("--conversation-limit", type=_positive_int)
    run.add_argument("--question-limit", type=_positive_int)
    run.add_argument("--ingest-concurrency", type=_positive_int, default=4)
    run.add_argument("--evaluate-concurrency", type=_positive_int, default=8)
    run.add_argument("--operation-retries", type=_positive_int, default=3)
    run.add_argument("--skip-ingestion", action="store_true")
    run.add_argument("--skip-evaluation", action="store_true")
    run.add_argument(
        "--keep-errors",
        action="store_true",
        help="do not retry error observations already present in the JSONL checkpoint",
    )
    run.set_defaults(handler=_run)

    rejudge = commands.add_parser("rejudge", help="grade frozen generated answers with an independent judge model")
    rejudge.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    rejudge.add_argument("--env-file", type=Path, default=Path(".env"))
    rejudge.add_argument("--source-directory", type=Path, required=True)
    rejudge.add_argument("--output-directory", type=Path, required=True)
    rejudge.add_argument("--run-id", required=True)
    rejudge.add_argument(
        "--judge-model", required=True, help="explicit independent judge model, for example openai:qwen"
    )
    rejudge.add_argument(
        "--judge-profile",
        type=JudgeProfile,
        choices=tuple(JudgeProfile),
        default=JudgeProfile.TOPICAL,
        help="existing LLM correctness policy; defaults to topical",
    )
    rejudge.add_argument("--concurrency", type=_positive_int, default=8)
    rejudge.add_argument("--operation-retries", type=_positive_int, default=3)
    rejudge.add_argument(
        "--keep-errors",
        action="store_true",
        help="do not retry failed judge observations already present in the JSONL checkpoint",
    )
    rejudge.set_defaults(handler=_rejudge)
    return parser


def _inspect(arguments: argparse.Namespace) -> int:
    dataset = load_benchmark_dataset(arguments.dataset)
    settings = load_settings(arguments.env_file)
    categories = {str(category): 0 for category in range(1, 6)}
    for question in dataset.questions:
        categories[str(question.category)] += 1
    print(
        json.dumps(
            {
                "dataset": {
                    "path": str(dataset.path),
                    "sha256": dataset.sha256,
                    "conversations": len(dataset.conversations),
                    "sessions": len(dataset.sessions),
                    "turns": sum(len(session.turns) for session in dataset.sessions),
                    "questions": len(dataset.questions),
                    "scored_questions": len(dataset.selected_questions()),
                    "questions_by_category": categories,
                },
                "configuration": public_configuration(settings),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def _run(arguments: argparse.Namespace) -> int:
    if arguments.skip_ingestion and arguments.skip_evaluation:
        raise ValueError("cannot skip both ingestion and evaluation")  # noqa: TRY003
    dataset = load_benchmark_dataset(arguments.dataset)
    settings = load_settings(arguments.env_file)
    if arguments.memory_extraction_profile is not None:
        settings = settings.model_copy(
            update={
                "runtime": settings.runtime.model_copy(
                    update={"memory_extraction_profile": arguments.memory_extraction_profile}
                )
            }
        )
    run_id = normalize_run_id(arguments.run_id or datetime.now(UTC).strftime("locomo-%Y%m%dT%H%M%SZ"))
    output_directory = (
        (DEFAULT_RESULTS / run_id).resolve()
        if arguments.output_directory is None
        else arguments.output_directory.resolve()
    )
    manifest = prepare_run(
        dataset=dataset,
        settings=settings,
        run_id=run_id,
        output_directory=output_directory,
        top_k=arguments.top_k,
        answer_k=arguments.answer_k,
        rerank_mode=arguments.rerank_mode,
        answer_source_content=arguments.answer_source_content,
        answer_inference_aware=arguments.answer_inference_aware,
        answer_unknown_fallback_inference=arguments.answer_unknown_fallback_inference,
        judge_profile=arguments.judge_profile,
        categories=arguments.categories,
        conversation_limit=arguments.conversation_limit,
        question_limit=arguments.question_limit,
        operation_retries=arguments.operation_retries,
    )
    print(json.dumps({"run": manifest, "output_directory": str(output_directory)}, ensure_ascii=False, indent=2))

    async def scenario() -> None:
        if not arguments.skip_ingestion:
            result = await ingest_dataset(
                dataset,
                settings=settings,
                run_id=run_id,
                output_directory=output_directory,
                conversation_limit=arguments.conversation_limit,
                concurrency=arguments.ingest_concurrency,
                operation_retries=arguments.operation_retries,
            )
            print(json.dumps({"ingestion": result}, ensure_ascii=False, indent=2))
        if not arguments.skip_evaluation:
            summary = await evaluate_dataset(
                dataset,
                settings=settings,
                run_id=run_id,
                output_directory=output_directory,
                top_k=arguments.top_k,
                answer_k=arguments.answer_k,
                rerank_mode=arguments.rerank_mode,
                answer_source_content=arguments.answer_source_content,
                answer_inference_aware=arguments.answer_inference_aware,
                answer_unknown_fallback_inference=arguments.answer_unknown_fallback_inference,
                judge_profile=arguments.judge_profile,
                categories=arguments.categories,
                conversation_limit=arguments.conversation_limit,
                question_limit=arguments.question_limit,
                concurrency=arguments.evaluate_concurrency,
                operation_retries=arguments.operation_retries,
                retry_errors=not arguments.keep_errors,
            )
            print(json.dumps({"summary": summary}, ensure_ascii=False, indent=2))

    asyncio.run(scenario())
    return 0


def _rejudge(arguments: argparse.Namespace) -> int:
    dataset = load_benchmark_dataset(arguments.dataset)
    settings = load_settings(arguments.env_file)
    source_directory = arguments.source_directory.resolve()
    output_directory = arguments.output_directory.resolve()
    manifest = prepare_rejudge(
        dataset=dataset,
        source_directory=source_directory,
        output_directory=output_directory,
        run_id=arguments.run_id,
        judge_model=arguments.judge_model,
        judge_profile=arguments.judge_profile,
        operation_retries=arguments.operation_retries,
    )
    print(json.dumps({"run": manifest, "output_directory": str(output_directory)}, ensure_ascii=False, indent=2))
    summary = asyncio.run(
        rejudge_dataset(
            dataset,
            settings=settings,
            source_directory=source_directory,
            output_directory=output_directory,
            run_id=arguments.run_id,
            judge_model=arguments.judge_model,
            judge_profile=arguments.judge_profile,
            concurrency=arguments.concurrency,
            operation_retries=arguments.operation_retries,
            retry_errors=not arguments.keep_errors,
        )
    )
    print(json.dumps({"summary": summary}, ensure_ascii=False, indent=2))
    return 0


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be positive")  # noqa: TRY003
    return parsed


def _categories(value: str) -> tuple[int, ...]:
    try:
        values = tuple(dict.fromkeys(int(item.strip()) for item in value.split(",") if item.strip()))
    except ValueError as error:
        raise argparse.ArgumentTypeError("categories must be comma-separated integers") from error  # noqa: TRY003
    if not values or any(category not in {1, 2, 3, 4, 5} for category in values):
        raise argparse.ArgumentTypeError("categories must be selected from 1,2,3,4,5")  # noqa: TRY003
    return values


__all__ = ["main"]
