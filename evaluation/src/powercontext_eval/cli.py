"""Command-line entry point for the evaluation runner."""

from __future__ import annotations

import json
import os
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Annotated, Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import typer
from pydantic import ValidationError

from powercontext_eval.benchmarks.swebench_pro.catalog import SweBenchProCatalog
from powercontext_eval.codex import DEFAULT_CODEX_MODEL, DEFAULT_REASONING_EFFORT
from powercontext_eval.powercontext_sut import run_codex_contract_smoke
from powercontext_eval.runner import RunConfig, run_swebench_pro_instance
from powercontext_eval.web.batches import BatchCreate

if TYPE_CHECKING:
    from powercontext_eval.web.config import WebConfig

app = typer.Typer(no_args_is_help=True, help="PowerContext evaluation runner.")
swebench_pro_app = typer.Typer(no_args_is_help=True, help="Pinned SWE-bench Pro evaluation.")
app.add_typer(swebench_pro_app, name="swebench-pro")


@app.callback()
def root() -> None:
    """Run reproducible PowerContext evaluations."""


class _Stoppable(Protocol):
    def stop(self) -> None: ...


def _request_worker_stop(worker: _Stoppable, _signum: int, _frame: FrameType | None) -> None:
    """Request that a worker exit after its current task finishes."""
    worker.stop()


def _web_config(root_path: Path | None) -> WebConfig:
    from powercontext_eval.web.config import WebConfig

    try:
        environ = dict(os.environ)
        if root_path is not None:
            environ["POWERCONTEXT_EVAL_ROOT"] = os.fspath(root_path)
        return WebConfig.from_environment(environ)
    except (KeyError, TypeError, ValueError, ValidationError):
        raise typer.BadParameter("Invalid evaluation configuration.", param_hint="--root") from None


@contextmanager
def _worker_signal_handlers(worker: _Stoppable) -> Iterator[None]:
    previous: dict[signal.Signals, Any] = {}
    stop_requested = False

    def handler(signum: int, frame: FrameType | None) -> None:
        nonlocal stop_requested
        if stop_requested:
            return
        stop_requested = True
        _request_worker_stop(worker, signum, frame)

    try:
        for signum in (signal.SIGTERM, signal.SIGINT):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, handler)
        yield
    finally:
        for signum, prior in previous.items():
            signal.signal(signum, prior)


@app.command("web")
def web(root_path: Annotated[Path | None, typer.Option("--root")] = None) -> None:
    """Serve the evaluation console API and frontend."""
    import uvicorn

    from powercontext_eval.web.api import create_app

    config = _web_config(root_path)
    uvicorn.run(create_app(config), host=config.host, port=config.port)


@app.command("worker")
def worker(root_path: Annotated[Path | None, typer.Option("--root")] = None) -> None:
    """Run queued task pairs at configured parallelism until shutdown is requested."""
    from powercontext_eval.web.store import TaskStore
    from powercontext_eval.web.worker import EvaluationWorker

    config = _web_config(root_path)
    store = TaskStore(config.database_path, lease_duration=timedelta(seconds=config.lease_seconds))
    store.initialize()
    service = EvaluationWorker(config, store)
    with _worker_signal_handlers(service):
        service.run_forever()


@app.command("codex-contract-smoke")
def codex_contract_smoke(
    run_root: str = typer.Option(...),
    task_image: str = typer.Option(...),
    codex_bin: str = typer.Option(...),
    tokensflow_bin: str = typer.Option(...),
    tokensflow_user_home: str = typer.Option(...),
    tokensflow_egress_network: str = typer.Option(...),
    uv_bin: str = typer.Option(...),
    powercontext_source: str = typer.Option(...),
    powercontext_sha: str = typer.Option(...),
    auth_json: str = typer.Option(...),
    proxy_url: str = typer.Option(...),
    prompt: str = typer.Option("Reply with exactly OK."),
) -> None:
    """Run OFF/ON identity, daemon, bounded-drain, and Codex contract checks."""

    outcome = run_codex_contract_smoke(
        run_root=run_root,
        task_image=task_image,
        codex_bin=codex_bin,
        tokensflow_bin=tokensflow_bin,
        tokensflow_user_home=tokensflow_user_home,
        tokensflow_egress_network=tokensflow_egress_network,
        uv_bin=uv_bin,
        powercontext_source=powercontext_source,
        powercontext_sha=powercontext_sha,
        auth_json=auth_json,
        proxy_url=proxy_url,
        prompt=prompt,
    )
    typer.echo(json.dumps(outcome, ensure_ascii=False, sort_keys=True))


@swebench_pro_app.command("run")
def swebench_pro_run(
    root_path: str = typer.Option("/data/powercontext-eval", "--root"),
    powercontext_source: str = typer.Option("/data/powercontext-eval/source/powercontext.git"),
    powercontext_ref: str = typer.Option("latest"),
    harness_root: str = typer.Option("/data/powercontext-eval/cache/swebench-pro.git"),
    harness_python: str = typer.Option("/data/powercontext-eval/venvs/swebench-pro-ca10a60/bin/python"),
    dataset_path: str = typer.Option(
        "/data/powercontext-eval/cache/swebench-pro.git/helper_code/sweap_eval_full_v2.jsonl"
    ),
    instance_id: str = typer.Option(...),
    codex_bin: str = typer.Option("/data/powercontext-eval/bin/codex"),
    tokensflow_bin: str = typer.Option("/data/powercontext-eval/bin/tokensflow"),
    tokensflow_user_home: str = typer.Option("/data/powercontext-eval/tokensflow-home"),
    tokensflow_egress_network: str = typer.Option(...),
    uv_bin: str = typer.Option("/data/powercontext-eval/bin/uv"),
    registry_bin: str = typer.Option("/data/powercontext-eval/bin/regctl"),
    auth_json: str = typer.Option("/data/powercontext-eval/codex-home/auth.json"),
    proxy_url: str = typer.Option("http://127.0.0.1:7890"),
    model: str = typer.Option(DEFAULT_CODEX_MODEL, "--model"),
    reasoning_effort: str = typer.Option(DEFAULT_REASONING_EFFORT, "--reasoning-effort"),
    run_id: str | None = typer.Option(None),
) -> None:
    """Run Gold, PowerContext OFF/ON, official grading, and report generation."""

    from pathlib import Path

    catalog = SweBenchProCatalog.load(Path(dataset_path))
    result = run_swebench_pro_instance(
        RunConfig(
            root=Path(root_path),
            powercontext_source=Path(powercontext_source),
            powercontext_ref=powercontext_ref,
            harness_root=Path(harness_root),
            harness_python=Path(harness_python),
            codex_binary=Path(codex_bin),
            tokensflow_binary=Path(tokensflow_bin),
            tokensflow_user_home=Path(tokensflow_user_home),
            tokensflow_egress_network=tokensflow_egress_network,
            uv_binary=Path(uv_bin),
            registry_binary=Path(registry_bin),
            auth_json=Path(auth_json),
            proxy_url=proxy_url,
            run_id=run_id or datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S"),
            model=model,
            reasoning_effort=reasoning_effort,
        ),
        instance=catalog.require(instance_id),
    )
    typer.echo(
        json.dumps(
            {
                "run_id": result.run_id,
                "report": str(result.report_path),
                "off_resolved": result.off_resolved,
                "on_resolved": result.on_resolved,
            },
            sort_keys=True,
        )
    )


@swebench_pro_app.command("create-batch")
def swebench_pro_create_batch(
    idempotency_key: str = typer.Option(..., "--idempotency-key"),
    console_url: str = typer.Option("http://127.0.0.1:8787", "--console-url"),
    powercontext_ref: str = typer.Option("latest", "--powercontext-ref"),
    model: str = typer.Option(DEFAULT_CODEX_MODEL, "--model"),
    usage_pause_percent: int = typer.Option(80, "--usage-pause-percent", min=1, max=100),
    start_paused: bool = typer.Option(False, "--start-paused/--start-running"),
) -> None:
    """Create one full batch through the console API, optionally atomically paused."""

    endpoint = _batch_api_endpoint(console_url)
    try:
        batch = BatchCreate(
            powercontext_ref=powercontext_ref,
            benchmark="swebench-pro",
            task_set="swebench-pro-public-v2",
            model=model,
            reasoning_effort=DEFAULT_REASONING_EFFORT,
            treatment_mode="off_on",
            idempotency_key=idempotency_key,
            usage_pause_percent=usage_pause_percent,
            initial_control_intent="pause" if start_paused else "run",
        )
    except ValidationError:
        raise typer.BadParameter("Invalid batch configuration.") from None
    request = Request(
        endpoint,
        data=batch.model_dump_json().encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        if error.code == 422:
            raise typer.BadParameter(
                "Codex model is not enabled for new evaluation work.",
                param_hint="--model",
            ) from None
        raise typer.Exit(code=1) from None
    except (URLError, OSError, ValueError, UnicodeDecodeError):
        raise typer.Exit(code=1) from None
    if not isinstance(payload, dict) or not isinstance(payload.get("batch_id"), str):
        raise typer.Exit(code=1)
    typer.echo(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _batch_api_endpoint(console_url: str) -> str:
    parsed = urlsplit(console_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise typer.BadParameter("Invalid console URL.", param_hint="--console-url")
    return console_url.rstrip("/") + "/api/batches"


def main() -> None:
    """Run the evaluation command-line application."""

    app()


if __name__ == "__main__":
    main()
