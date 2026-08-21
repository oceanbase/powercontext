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

import signal
import subprocess
import sys
import textwrap
from pathlib import Path

from typer.testing import CliRunner

from powercontext_eval.cli import _request_worker_stop, app
from powercontext_eval.web.config import WebConfig


def test_top_level_help_exposes_service_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "web" in result.output
    assert "worker" in result.output


def test_web_builds_config_from_cli_root_and_environment(monkeypatch, tmp_path: Path) -> None:
    calls: dict[str, object] = {}
    application = object()

    monkeypatch.setenv("POWERCONTEXT_EVAL_PORT", "8123")
    monkeypatch.setenv("POWERCONTEXT_EVAL_HOST", "127.0.0.2")
    monkeypatch.setenv("POWERCONTEXT_EVAL_TOKENSFLOW_EGRESS_NETWORK", "tokensflow-egress")
    monkeypatch.setenv("POWERCONTEXT_EVAL_PROXY_URL", "http://127.0.0.1:8081")

    def fake_create_app(config: object) -> object:
        calls["config"] = config
        return application

    monkeypatch.setattr("powercontext_eval.web.api.create_app", fake_create_app)

    def fake_run(app: object, *, host: str, port: int) -> None:
        calls.update(app=app, host=host, port=port)

    monkeypatch.setattr("uvicorn.run", fake_run)

    result = CliRunner().invoke(app, ["web", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    config = calls["config"]
    assert isinstance(config, WebConfig)
    assert config.root == tmp_path
    assert calls == {"config": config, "app": application, "host": "127.0.0.2", "port": 8123}


def test_worker_initializes_store_and_runs_with_configured_poll(monkeypatch, tmp_path: Path) -> None:
    calls: list[tuple[object, ...]] = []

    class FakeStore:
        def __init__(self, database: Path, *, lease_duration: object, max_attempts: int) -> None:
            calls.append(("store", database, lease_duration, max_attempts))

        def initialize(self) -> None:
            calls.append(("initialize",))

    class FakeWorker:
        def __init__(self, config: object, store: object, *, usage_probe: object) -> None:
            calls.append(("worker", config, store, usage_probe))

        def run_forever(self) -> None:
            calls.append(("run_forever",))

        def stop(self) -> None:
            calls.append(("stop",))

    monkeypatch.setenv("POWERCONTEXT_EVAL_POLL_SECONDS", "2.5")
    monkeypatch.setenv("POWERCONTEXT_EVAL_LEASE_SECONDS", "90")
    monkeypatch.setenv("POWERCONTEXT_EVAL_TOKENSFLOW_EGRESS_NETWORK", "tokensflow-egress")
    monkeypatch.setenv("POWERCONTEXT_EVAL_PROXY_URL", "http://127.0.0.1:8081")
    monkeypatch.setattr("powercontext_eval.web.store.TaskStore", FakeStore)
    monkeypatch.setattr("powercontext_eval.web.worker.EvaluationWorker", FakeWorker)
    monkeypatch.setattr("signal.getsignal", lambda _signal: signal.SIG_DFL)
    monkeypatch.setattr("signal.signal", lambda *args: calls.append(("signal", *args)))

    result = CliRunner().invoke(app, ["worker", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert calls[0][0] == "store"
    assert calls[0][3] == 5
    assert calls[1] == ("initialize",)
    assert calls[2][0] == "worker"
    assert isinstance(calls[2][1], WebConfig)
    assert ("run_forever",) in calls
    assert calls[2][1].poll_seconds == 2.5
    assert ("stop",) not in calls


def test_signal_callback_requests_graceful_worker_stop() -> None:
    calls: list[str] = []

    class Worker:
        def stop(self) -> None:
            calls.append("stop")

    _request_worker_stop(Worker(), signal.SIGTERM, None)

    assert calls == ["stop"]


def test_worker_signal_handler_ignores_reentrant_sigterm_while_first_stop_is_running() -> None:
    program = textwrap.dedent(
        """
        import os
        import signal
        import threading

        from powercontext_eval.cli import _worker_signal_handlers


        class Worker:
            def __init__(self) -> None:
                self.calls = 0
                self.lock = threading.Lock()
                self.first_stop_entered = threading.Event()
                self.second_signal_sent = threading.Event()

            def stop(self) -> None:
                self.calls += 1
                with self.lock:
                    self.first_stop_entered.set()
                    assert self.second_signal_sent.wait(timeout=1)


        worker = Worker()


        def send_second_signal() -> None:
            assert worker.first_stop_entered.wait(timeout=1)
            os.kill(os.getpid(), signal.SIGTERM)
            worker.second_signal_sent.set()


        sender = threading.Thread(target=send_second_signal, daemon=True)
        with _worker_signal_handlers(worker):
            sender.start()
            os.kill(os.getpid(), signal.SIGTERM)
        sender.join(timeout=1)
        print(f"stops={worker.calls}")
        """
    )

    completed = subprocess.run(
        (sys.executable, "-c", program),
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == "stops=1\n"


def test_invalid_configuration_is_concise_and_does_not_print_secrets(monkeypatch) -> None:
    secret = "https://user:secret@proxy.invalid"
    monkeypatch.setenv("POWERCONTEXT_EVAL_ROOT", "relative")
    monkeypatch.setenv("POWERCONTEXT_EVAL_PROXY_URL", secret)

    result = CliRunner().invoke(app, ["web"])

    assert result.exit_code == 2
    assert "Invalid evaluation configuration" in result.output
    assert secret not in result.output
    assert "validation error" not in result.output.casefold()
