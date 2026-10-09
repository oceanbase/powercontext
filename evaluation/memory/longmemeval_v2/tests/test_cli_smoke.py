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

import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from powercontext_eval_longmemeval_v2.catalog import (
    LongMemEvalV2EnvironmentError,
    LongMemEvalV2InputError,
)
from powercontext_eval_longmemeval_v2.cli import app
from powercontext_eval_longmemeval_v2.smoke import PreparedSmokeRun


def test_longmemeval_v2_smoke_prepares_input_artifacts_without_a_model(monkeypatch, tmp_path: Path) -> None:
    def prepare(**kwargs: object) -> PreparedSmokeRun:
        assert kwargs == {
            "data_root": Path("/data"),
            "dataset_lock": Path("/dataset-lock.json"),
            "harness_root": Path("/harness"),
            "smoke_manifest": Path("/smoke.json"),
            "output_dir": Path("/output"),
        }
        return PreparedSmokeRun(
            output_dir=tmp_path / "output",
            manifest_path=tmp_path / "output" / "manifest.json",
            subset_path=tmp_path / "output" / "subset.json",
        )

    monkeypatch.setattr("powercontext_eval_longmemeval_v2.cli.prepare_smoke_run", prepare)
    result = CliRunner().invoke(
        app,
        [
            "smoke",
            "--data-root",
            "/data",
            "--dataset-lock",
            "/dataset-lock.json",
            "--harness-root",
            "/harness",
            "--smoke-manifest",
            "/smoke.json",
            "--output-dir",
            "/output",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"classification": "smoke-subset"' in result.output


def test_longmemeval_v2_smoke_reports_invalid_configuration_as_bad_parameter(
    monkeypatch,
) -> None:
    def prepare(**_kwargs: object) -> PreparedSmokeRun:
        raise LongMemEvalV2InputError("Smoke manifest schema is unsupported")

    monkeypatch.setattr("powercontext_eval_longmemeval_v2.cli.prepare_smoke_run", prepare)
    result = CliRunner().invoke(app, ["smoke", *_longmemeval_smoke_arguments()])

    assert result.exit_code == 2
    assert "Invalid value" in result.output
    assert "Smoke manifest schema is unsupported" in result.output


def test_longmemeval_v2_smoke_reports_environment_failure_with_exit_one(
    monkeypatch,
) -> None:
    def prepare(**_kwargs: object) -> PreparedSmokeRun:
        raise LongMemEvalV2EnvironmentError("LongMemEval-V2 SHA-256 mismatch for questions.jsonl")

    monkeypatch.setattr("powercontext_eval_longmemeval_v2.cli.prepare_smoke_run", prepare)
    result = CliRunner().invoke(app, ["smoke", *_longmemeval_smoke_arguments()])

    assert result.exit_code == 1
    assert "LongMemEval-V2 smoke failed" in result.output
    assert "SHA-256 mismatch" in result.output
    assert "Invalid value" not in result.output


def _longmemeval_smoke_arguments() -> list[str]:
    return [
        "--data-root",
        "/data",
        "--dataset-lock",
        "/dataset-lock.json",
        "--harness-root",
        "/harness",
        "--smoke-manifest",
        "/smoke.json",
        "--output-dir",
        "/output",
    ]


def test_cli_help_describes_the_evaluation_runner() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "LongMemEval-V2" in result.output
    assert not isinstance(result.exception, RuntimeError)


def test_cli_module_is_directly_executable() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "powercontext_eval_longmemeval_v2.cli", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "smoke" in result.stdout
