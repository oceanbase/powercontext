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

"""SWE-bench Pro command-line commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import typer
from pydantic import ValidationError

from powercontext_eval_swebench_pro.catalog import PUBLIC_V2_TASK_SET, SweBenchProCatalog, TaskSet
from powercontext_eval_swebench_pro.codex import DEFAULT_CODEX_MODEL, DEFAULT_REASONING_EFFORT
from powercontext_eval_swebench_pro.models import Arm, TreatmentMode
from powercontext_eval_swebench_pro.service_cli import codex_contract_smoke, web, worker

app = typer.Typer(no_args_is_help=True, help="Pinned SWE-bench Pro evaluation.")
app.command("web")(web)
app.command("worker")(worker)
app.command("codex-contract-smoke")(codex_contract_smoke)
DEFAULT_DOCKER_NETWORK_POOL = "172.30.0.0/15"


def run_swebench_pro_instance(*args: Any, **kwargs: Any) -> Any:
    """Load the SWE-bench Pro runner only when its command is used."""

    from powercontext_eval_swebench_pro.runner import run_swebench_pro_instance as implementation

    return implementation(*args, **kwargs)


@app.command("run")
def swebench_pro_run(
    root_path: str = typer.Option(..., "--root"),
    powercontext_source: str | None = typer.Option(None),
    powercontext_ref: str = typer.Option("latest"),
    harness_root: str | None = typer.Option(None),
    harness_python: str | None = typer.Option(None),
    dataset_path: str | None = typer.Option(None),
    instance_id: str = typer.Option(...),
    codex_bin: str | None = typer.Option(None),
    tokensflow_enabled: bool = typer.Option(False, "--tokensflow/--no-tokensflow"),
    tokensflow_bin: str | None = typer.Option(None),
    tokensflow_user_home: str | None = typer.Option(None),
    tokensflow_egress_network: str | None = typer.Option(None),
    uv_bin: str | None = typer.Option(None),
    registry_bin: str | None = typer.Option(None),
    auth_json: str | None = typer.Option(None),
    proxy_url: str | None = typer.Option(None),
    docker_network_pool: str = typer.Option(DEFAULT_DOCKER_NETWORK_POOL),
    extra_no_proxy_hosts: str = typer.Option(""),
    model: str = typer.Option(DEFAULT_CODEX_MODEL, "--model"),
    reasoning_effort: str = typer.Option(DEFAULT_REASONING_EFFORT, "--reasoning-effort"),
    run_id: str | None = typer.Option(None),
    database_config: Annotated[
        Path | None,
        typer.Option(
            help="Private JSON object with off/on settings.database configurations; use separate test databases."
        ),
    ] = None,
) -> None:
    """Run Gold, PowerContext OFF/ON, official grading, and report generation."""

    from powercontext_eval_swebench_pro.powercontext_sut import UnsafeSutConfiguration, validated_database_configs
    from powercontext_eval_swebench_pro.runner import RunConfig

    root = Path(root_path)
    harness = Path(harness_root) if harness_root is not None else root / "cache" / "swebench-pro.git"
    dataset = Path(dataset_path) if dataset_path is not None else harness / "helper_code" / "sweap_eval_full_v2.jsonl"
    binaries = root / "bin"
    databases = None
    if database_config is not None:
        try:
            payload = json.loads(database_config.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or set(payload) != {arm.value for arm in Arm}:
                raise TypeError
            databases = validated_database_configs({Arm(key): value for key, value in payload.items()})
        except (OSError, UnicodeError, ValueError, TypeError, UnsafeSutConfiguration):
            raise typer.BadParameter(
                "Database configuration must be a readable JSON object with valid off/on settings for separate databases.",
                param_hint="--database-config",
            ) from None

    catalog = SweBenchProCatalog.load(dataset)
    result = run_swebench_pro_instance(
        RunConfig(
            root=root,
            powercontext_source=(
                Path(powercontext_source) if powercontext_source is not None else root / "source" / "powercontext.git"
            ),
            powercontext_ref=powercontext_ref,
            harness_root=harness,
            harness_python=(
                Path(harness_python)
                if harness_python is not None
                else root / "venvs" / "swebench-pro" / "bin" / "python"
            ),
            codex_binary=Path(codex_bin) if codex_bin is not None else binaries / "codex",
            tokensflow_enabled=tokensflow_enabled,
            tokensflow_binary=(
                Path(tokensflow_bin)
                if tokensflow_bin is not None
                else (binaries / "tokensflow" if tokensflow_enabled else None)
            ),
            tokensflow_user_home=(
                Path(tokensflow_user_home)
                if tokensflow_user_home is not None
                else (root / "tokensflow-home" if tokensflow_enabled else None)
            ),
            tokensflow_egress_network=tokensflow_egress_network,
            uv_binary=Path(uv_bin) if uv_bin is not None else binaries / "uv",
            registry_binary=Path(registry_bin) if registry_bin is not None else binaries / "regctl",
            auth_json=Path(auth_json) if auth_json is not None else root / "codex-home" / "auth.json",
            proxy_url=proxy_url,
            docker_network_pool=docker_network_pool,
            extra_no_proxy_hosts=tuple(host for host in extra_no_proxy_hosts.split(",") if host),
            run_id=run_id or datetime.now(UTC).strftime("run-%Y%m%d-%H%M%S"),
            model=model,
            reasoning_effort=reasoning_effort,
            database_configs=databases,
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


@app.command("create-batch")
def swebench_pro_create_batch(
    idempotency_key: str = typer.Option(..., "--idempotency-key"),
    console_url: str = typer.Option("http://127.0.0.1:8787", "--console-url"),
    powercontext_ref: str = typer.Option("latest", "--powercontext-ref"),
    task_set: str = typer.Option(PUBLIC_V2_TASK_SET, "--task-set"),
    model: str = typer.Option(DEFAULT_CODEX_MODEL, "--model"),
    treatment_mode: Annotated[TreatmentMode, typer.Option("--treatment-mode")] = TreatmentMode.OFF_ON,
    usage_pause_percent: int = typer.Option(80, "--usage-pause-percent", min=1, max=100),
    start_paused: bool = typer.Option(False, "--start-paused/--start-running"),
) -> None:
    """Create one full batch through the console API, optionally atomically paused."""

    from powercontext_eval_swebench_pro.web.batches import BatchCreate

    endpoint = _batch_api_endpoint(console_url)
    try:
        batch = BatchCreate(
            powercontext_ref=powercontext_ref,
            benchmark="swebench-pro",
            task_set=cast(TaskSet, task_set),
            model=model,
            reasoning_effort=DEFAULT_REASONING_EFFORT,
            treatment_mode=treatment_mode,
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
    """Run the SWE-bench Pro command-line application."""

    app()


if __name__ == "__main__":
    main()
