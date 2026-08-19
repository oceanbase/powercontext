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

import json
import subprocess
import sys
from email.message import Message
from io import BytesIO
from pathlib import Path
from typing import Self, cast
from urllib.error import HTTPError
from urllib.request import Request

from typer.testing import CliRunner

from powercontext_eval.cli import app
from powercontext_eval.runner import MinimalRunResult, RunConfig


def test_cli_help_describes_the_evaluation_runner() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "PowerContext evaluation runner" in result.output
    assert not isinstance(result.exception, RuntimeError)


def test_codex_contract_smoke_is_an_executable_injectable_cli(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_contract_smoke(**kwargs: object) -> dict[str, object]:
        calls.append(kwargs)
        return {
            "off_prompt_sources": 0,
            "on_prompt_sources": 1,
            "status": "passed",
            "tokensflow": {
                "off": {"identity_match": True, "queue_caught_up": True},
                "on": {"identity_match": True, "queue_caught_up": True},
            },
        }

    monkeypatch.setattr("powercontext_eval.cli.run_codex_contract_smoke", fake_contract_smoke)
    result = CliRunner().invoke(
        app,
        [
            "codex-contract-smoke",
            "--run-root",
            "/tmp/contract",
            "--task-image",
            "fixture:image",
            "--codex-bin",
            "/tools/codex",
            "--tokensflow-bin",
            "/tools/tokensflow",
            "--tokensflow-user-home",
            "/tokensflow-home",
            "--tokensflow-egress-network",
            "bridge",
            "--uv-bin",
            "/tools/uv",
            "--powercontext-source",
            "/source",
            "--powercontext-sha",
            "a" * 40,
            "--auth-json",
            "/auth.json",
            "--proxy-url",
            "http://127.0.0.1:18080",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"status": "passed"' in result.output
    assert '"queue_caught_up": true' in result.output
    assert "/tokensflow-home" not in result.output
    assert calls == [
        {
            "run_root": "/tmp/contract",
            "task_image": "fixture:image",
            "codex_bin": "/tools/codex",
            "tokensflow_bin": "/tools/tokensflow",
            "tokensflow_user_home": "/tokensflow-home",
            "tokensflow_egress_network": "bridge",
            "uv_bin": "/tools/uv",
            "powercontext_source": "/source",
            "powercontext_sha": "a" * 40,
            "auth_json": "/auth.json",
            "proxy_url": "http://127.0.0.1:18080",
            "prompt": "Reply with exactly OK.",
        }
    ]


def test_cli_module_is_directly_executable() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "powercontext_eval.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "codex-contract-smoke" in result.stdout


def test_swebench_pro_run_derives_portable_paths_from_explicit_root(monkeypatch) -> None:
    calls: list[tuple[object, object]] = []
    instance = object()

    def fake_run(config: object, *, instance: object) -> MinimalRunResult:
        calls.append((config, instance))
        return MinimalRunResult("run-fixed", Path("/srv/evaluation/runs/run-fixed/report.md"), False, True)

    class FakeCatalog:
        def require(self, instance_id: str) -> object:
            assert instance_id == "instance_owner__repo-b"
            return instance

    monkeypatch.setattr(
        "powercontext_eval.cli.SweBenchProCatalog.load",
        lambda path: FakeCatalog(),
    )
    monkeypatch.setattr("powercontext_eval.cli.run_swebench_pro_instance", fake_run)
    result = CliRunner().invoke(
        app,
        [
            "swebench-pro",
            "run",
            "--run-id",
            "run-fixed",
            "--root",
            "/srv/evaluation",
            "--proxy-url",
            "http://127.0.0.1:8081",
            "--instance-id",
            "instance_owner__repo-b",
            "--tokensflow-egress-network",
            "bridge",
            "--tokensflow",
            "--model",
            "gpt-5.6-luna",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"run_id": "run-fixed"' in result.output
    assert '"off_resolved": false' in result.output
    assert '"on_resolved": true' in result.output
    assert len(calls) == 1
    assert calls[0][1] is instance
    config = cast(RunConfig, calls[0][0])
    assert config.tokensflow_enabled is True
    assert config.tokensflow_binary == Path("/srv/evaluation/bin/tokensflow")
    assert config.tokensflow_user_home == Path("/srv/evaluation/tokensflow-home")
    assert config.tokensflow_egress_network == "bridge"
    assert config.model == "gpt-5.6-luna"
    assert config.reasoning_effort == "medium"


def test_swebench_pro_run_defaults_optional_integrations_off(monkeypatch) -> None:
    captured: list[RunConfig] = []

    class FakeCatalog:
        def require(self, _instance_id: str) -> object:
            return object()

    def fake_run(config: RunConfig, *, instance: object) -> MinimalRunResult:
        del instance
        captured.append(config)
        return MinimalRunResult("run-direct", Path("/tmp/report.md"), False, False)

    monkeypatch.setattr("powercontext_eval.cli.SweBenchProCatalog.load", lambda _path: FakeCatalog())
    monkeypatch.setattr("powercontext_eval.cli.run_swebench_pro_instance", fake_run)

    result = CliRunner().invoke(
        app,
        [
            "swebench-pro",
            "run",
            "--root",
            "/srv/evaluation",
            "--instance-id",
            "instance_owner__repo-b",
        ],
    )

    assert result.exit_code == 0, result.output
    assert len(captured) == 1
    assert captured[0].tokensflow_enabled is False
    assert captured[0].tokensflow_binary is None
    assert captured[0].tokensflow_user_home is None
    assert captured[0].tokensflow_egress_network is None
    assert captured[0].proxy_url is None


def test_cli_creates_a_luna_batch_atomically_paused(monkeypatch) -> None:
    calls: list[tuple[Request, float]] = []

    class Response:
        def __init__(self, payload: bytes) -> None:
            self.payload = payload

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return self.payload

    def fake_urlopen(request: Request, *, timeout: float) -> Response:
        calls.append((request, timeout))
        if request.full_url.endswith("/api/capabilities"):
            return Response(b'{"models":["gpt-5.6-sol","gpt-5.6-luna"]}')
        return Response(b'{"batch_id":"batch-luna"}')

    monkeypatch.setattr("powercontext_eval.cli.urlopen", fake_urlopen, raising=False)
    result = CliRunner().invoke(
        app,
        [
            "swebench-pro",
            "create-batch",
            "--idempotency-key",
            "luna-paused-cli",
            "--model",
            "gpt-5.6-luna",
            "--task-set",
            "swebench-pro-stability-v1",
            "--start-paused",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"batch_id": "batch-luna"' in result.output
    assert len(calls) == 1
    request, timeout = calls[0]
    assert timeout == 30
    assert request.full_url == "http://127.0.0.1:8787/api/batches"
    assert isinstance(request.data, bytes)
    payload = json.loads(request.data)
    assert payload["model"] == "gpt-5.6-luna"
    assert payload["task_set"] == "swebench-pro-stability-v1"
    assert payload["initial_control_intent"] == "pause"


def test_cli_surfaces_server_rejection_for_a_new_unconfigured_model(monkeypatch) -> None:
    calls: list[Request] = []

    def fake_urlopen(request: Request, *, timeout: float) -> None:
        assert timeout == 30
        calls.append(request)
        raise HTTPError(
            request.full_url,
            422,
            "Unprocessable Entity",
            hdrs=Message(),
            fp=BytesIO(b'{"error":{"code":"invalid_request","message":"The evaluation request is invalid."}}'),
        )

    monkeypatch.setattr("powercontext_eval.cli.urlopen", fake_urlopen, raising=False)
    result = CliRunner().invoke(
        app,
        [
            "swebench-pro",
            "create-batch",
            "--idempotency-key",
            "unconfigured-model",
            "--model",
            "gpt-5.6-luna",
        ],
    )

    assert result.exit_code == 2
    assert "not enabled" in result.output
    assert [request.full_url for request in calls] == ["http://127.0.0.1:8787/api/batches"]
