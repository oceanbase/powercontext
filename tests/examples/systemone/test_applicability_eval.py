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

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pytest

from examples.systemone import applicability_eval
from examples.systemone.adapter import SystemOneConfig


@pytest.mark.parametrize("provider", ["jev", "laya"])
def test_explicit_provider_file_is_the_only_configuration_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], provider: str
) -> None:
    path = tmp_path / "provider.env"
    key = "file-only-key" if provider == "jev" else ""
    path.write_text(
        f"SYSTEMONE_PROVIDER={provider}\n"
        "SYSTEMONE_ENDPOINT=https://file.example/decisions\n"
        "SYSTEMONE_MODEL=file-model\n"
        f"SYSTEMONE_API_KEY='{key}'\n",
        encoding="utf-8",
    )
    conflicting = {
        "SYSTEMONE_PROVIDER": "laya" if provider == "jev" else "jev",
        "SYSTEMONE_ENDPOINT": "https://process.example/decisions",
        "SYSTEMONE_MODEL": "process-model",
        "SYSTEMONE_API_KEY": "process-secret-key",
    }
    for name, value in conflicting.items():
        monkeypatch.setenv(name, value)
    argv = ["applicability_eval", "--env-file", str(path)]
    if provider == "laya":
        argv += ["--checkpoint", str(tmp_path / "checkpoint")]
    monkeypatch.setattr(sys, "argv", argv)
    observed: list[SystemOneConfig] = []

    async def simulate_evaluation(args: argparse.Namespace, config: SystemOneConfig) -> Path:
        observed.append(config)
        report = tmp_path / "report.json"
        report.write_text('{"cases": []}', encoding="utf-8")
        return report

    monkeypatch.setattr(applicability_eval, "run", simulate_evaluation)
    environment = {name: os.environ[name] for name in conflicting}
    assert applicability_eval.main() == 0
    assert len(observed) == 1
    config = observed[0]
    assert (config.provider, config.endpoint, config.model) == (
        provider,
        "https://file.example/decisions",
        "file-model",
    )
    assert config.api_key.get_secret_value() == key
    assert {name: os.environ[name] for name in environment} == environment
    output = capsys.readouterr()
    if key:
        assert key not in output.out + output.err
    assert conflicting["SYSTEMONE_API_KEY"] not in output.out + output.err
    assert "process.example" not in output.out + output.err


@pytest.mark.parametrize(
    "document",
    [
        "SYSTEMONE_PROVIDER=jev\nSYSTEMONE_ENDPOINT=https://file.example/decisions\n",
        "SYSTEMONE_PROVIDER=jev\nSYSTEMONE_ENDPOINT=https://file.example/decisions\nSYSTEMONE_MODEL=file-model\n",
        "SYSTEMONE_PROVIDER=jev\nSYSTEMONE_ENDPOINT=https://file.example/decisions\nSYSTEMONE_MODEL=${SYSTEMONE_MODEL}\nSYSTEMONE_API_KEY=file-only-key\n",
        None,
    ],
)
def test_invalid_provider_file_cannot_borrow_process_settings_or_expose_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], document: str | None
) -> None:
    path = tmp_path / "provider.env"
    if document is not None:
        path.write_text(document, encoding="utf-8")
    for name, value in {
        "SYSTEMONE_PROVIDER": "jev",
        "SYSTEMONE_ENDPOINT": "https://process.example/decisions",
        "SYSTEMONE_MODEL": "process-model",
        "SYSTEMONE_API_KEY": "process-secret-key",
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(sys, "argv", ["applicability_eval", "--env-file", str(path)])
    started: list[SystemOneConfig] = []

    async def simulate_evaluation(args: argparse.Namespace, config: SystemOneConfig) -> Path:
        started.append(config)
        report = tmp_path / "report.json"
        report.write_text('{"cases": []}', encoding="utf-8")
        return report

    monkeypatch.setattr(applicability_eval, "run", simulate_evaluation)
    environment = {
        name: os.environ[name]
        for name in ("SYSTEMONE_PROVIDER", "SYSTEMONE_ENDPOINT", "SYSTEMONE_MODEL", "SYSTEMONE_API_KEY")
    }
    with pytest.raises(SystemExit) as error:
        applicability_eval.main()
    assert error.value.code == 2
    assert started == []  # Invalid local configuration must not start paid evaluation.
    assert {name: os.environ[name] for name in environment} == environment
    output = capsys.readouterr()
    assert "process-secret-key" not in output.out + output.err
    assert "file-only-key" not in output.out + output.err
    assert "process.example" not in output.out + output.err
