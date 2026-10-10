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


"""SWE-bench Pro web, worker, and runtime contract commands."""

from __future__ import annotations

import json
import os
import signal
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Annotated, Any, Protocol

import typer
from pydantic import ValidationError

if TYPE_CHECKING:
    from powercontext_eval_swebench_pro.web.config import WebConfig


class _Stoppable(Protocol):
    def stop(self) -> None: ...


def run_codex_contract_smoke(**kwargs: Any) -> Any:
    """Load the SWE-bench Pro contract runner only when its command is used."""

    from powercontext_eval_swebench_pro.powercontext_sut import run_codex_contract_smoke as implementation

    return implementation(**kwargs)


def _request_worker_stop(worker: _Stoppable, _signum: int, _frame: FrameType | None) -> None:
    """Request that a worker exit after its current task finishes."""
    worker.stop()


def _web_config(root_path: Path | None) -> WebConfig:
    from powercontext_eval_swebench_pro.web.config import WebConfig

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


def web(root_path: Annotated[Path | None, typer.Option("--root")] = None) -> None:
    """Serve the evaluation console API and frontend."""
    import uvicorn

    from powercontext_eval_swebench_pro.web.api import create_app

    config = _web_config(root_path)
    uvicorn.run(create_app(config), host=config.host, port=config.port)


def worker(root_path: Annotated[Path | None, typer.Option("--root")] = None) -> None:
    """Run queued task pairs at configured parallelism until shutdown is requested."""
    from powercontext_eval_swebench_pro.web.store import TaskStore
    from powercontext_eval_swebench_pro.web.usage import CodexUsageProbe
    from powercontext_eval_swebench_pro.web.worker import EvaluationWorker

    config = _web_config(root_path)
    store = TaskStore(
        config.database_path,
        lease_duration=timedelta(seconds=config.lease_seconds),
        max_attempts=config.max_attempts,
    )
    store.initialize()
    service = EvaluationWorker(
        config,
        store,
        usage_probe=CodexUsageProbe(
            codex_binary=config.codex_binary,
            auth_json=config.auth_json,
            codex_config=config.codex_config,
            proxy_url=config.proxy_url,
            timeout_seconds=config.usage_probe_timeout_seconds,
        ),
    )
    with _worker_signal_handlers(service):
        service.run_forever()


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
