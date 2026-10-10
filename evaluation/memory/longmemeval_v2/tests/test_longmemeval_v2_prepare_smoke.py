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

import json
import os
import subprocess
import venv
from pathlib import Path
from typing import Any

import pydantic
import pytest

from powercontext_eval_longmemeval_v2 import prepare_smoke
from powercontext_eval_longmemeval_v2.prepare_smoke import (
    PrepareSmokeError,
    prepare_reader_inputs_smoke,
)


def retrieval_artifacts(tmp_path: Path) -> Path:
    root = tmp_path / "retrieval"
    root.mkdir()
    (root / "retrieval-manifest.json").write_text(
        json.dumps(
            {
                "classification": "smoke-subset-retrieval-only",
                "runtime": {"reader": None, "judge": None},
            }
        ),
        encoding="utf-8",
    )
    (root / "summary.json").write_text(
        json.dumps(
            {
                "classification": "smoke-subset-retrieval-only",
                "question_count": 10,
                "failed": 0,
            }
        ),
        encoding="utf-8",
    )
    (root / "retrieval-results.jsonl").write_text("{}\n", encoding="utf-8")
    return root


def test_prepare_smoke_launches_pinned_worker_with_hashed_retrieval_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    retrieval = retrieval_artifacts(tmp_path)
    harness = tmp_path / "harness"
    harness.mkdir()
    harness_python = tmp_path / "python.exe"
    harness_python.write_text("placeholder", encoding="utf-8")
    output = tmp_path / "prepared"
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        prompts_path = Path(command[command.index("--prompts-path") + 1])
        failures_path = Path(command[command.index("--failures-path") + 1])
        summary_path = Path(command[command.index("--summary-path") + 1])
        prompts_path.write_text("{}\n", encoding="utf-8")
        failures_path.write_text("", encoding="utf-8")
        summary_path.write_text("{}\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "worker-ok\n", "")

    monkeypatch.setattr(prepare_smoke, "validate_harness_checkout", lambda root: None)
    monkeypatch.setattr(prepare_smoke.subprocess, "run", fake_run)

    result = prepare_reader_inputs_smoke(
        retrieval_dir=retrieval,
        harness_root=harness,
        harness_python=harness_python,
        output_dir=output,
        processor_revision="processor-sha",
        processor_model="test/processor",
        memory_context_max_tokens=123,
    )

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["processor"] == {
        "model": "test/processor",
        "revision": "processor-sha",
    }
    assert manifest["memory_context_max_tokens"] == 123
    assert manifest["reader"] is None
    assert manifest["judge"] is None
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[1:3] == ["-m", "powercontext_eval_longmemeval_v2.prepare_worker"]
    assert command[command.index("--processor-revision") + 1] == "processor-sha"
    assert kwargs["cwd"] == harness
    assert kwargs["env"]["PYTHONNOUSERSITE"] == "1"


def test_prepare_smoke_refuses_to_overwrite_before_validating_harness(
    tmp_path: Path,
) -> None:
    output = tmp_path / "prepared"
    output.mkdir()

    with pytest.raises(PrepareSmokeError, match="Refusing to overwrite"):
        prepare_reader_inputs_smoke(
            retrieval_dir=tmp_path / "missing",
            harness_root=tmp_path / "missing-harness",
            harness_python=tmp_path / "missing-python",
            output_dir=output,
            processor_revision="processor-sha",
        )


def test_prepare_smoke_resolves_relative_paths_against_the_caller_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    retrieval = retrieval_artifacts(tmp_path)
    harness = tmp_path / "harness"
    harness.mkdir()
    harness_python = tmp_path / "python.exe"
    harness_python.write_text("placeholder", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        prompts_path = Path(command[command.index("--prompts-path") + 1])
        failures_path = Path(command[command.index("--failures-path") + 1])
        summary_path = Path(command[command.index("--summary-path") + 1])
        prompts_path.write_text("{}\n", encoding="utf-8")
        failures_path.write_text("", encoding="utf-8")
        summary_path.write_text("{}\n", encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, "worker-ok\n", "")

    monkeypatch.setattr(prepare_smoke, "validate_harness_checkout", lambda root: None)
    monkeypatch.setattr(prepare_smoke.subprocess, "run", fake_run)

    prepare_reader_inputs_smoke(
        retrieval_dir=Path("retrieval"),
        harness_root=Path("harness"),
        harness_python=Path("python.exe"),
        output_dir=Path("prepared"),
        processor_revision="processor-sha",
    )

    (command, kwargs) = calls[0]
    assert Path(command[0]).is_absolute()
    assert Path(command[0]).resolve() == harness_python.resolve()
    assert Path(command[command.index("--harness-root") + 1]).resolve() == harness.resolve()
    assert (
        Path(command[command.index("--input-path") + 1]).resolve() == (retrieval / "retrieval-results.jsonl").resolve()
    )
    assert (
        Path(command[command.index("--prompts-path") + 1]).resolve()
        == (tmp_path / "prepared" / "prepared-prompts.jsonl").resolve()
    )
    assert Path(kwargs["cwd"]).resolve() == harness.resolve()


def test_prepare_smoke_launches_the_requested_virtualenv_interpreter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    venv_root = tmp_path / "harness-venv"
    venv.create(venv_root, with_pip=False)
    interpreter_suffix = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    interpreter = venv_root / interpreter_suffix
    retrieval = retrieval_artifacts(tmp_path)
    (retrieval / "retrieval-results.jsonl").write_text(
        json.dumps(
            {
                "question_id": "q-1",
                "domain": "enterprise",
                "question": {"text": "Where is the evidence?", "image": None},
                "memory_context": [{"type": "text", "value": "The saved evidence."}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    harness = tmp_path / "harness"
    (harness / "evaluation").mkdir(parents=True)
    (harness / "evaluation" / "__init__.py").write_text("", encoding="utf-8")
    (harness / "evaluation" / "harness.py").write_text(
        """
import sys
from types import SimpleNamespace
MEMORY_CONTEXT_PROCESSOR_LOCAL = SimpleNamespace()

def get_system_prompt(domain):
    return sys.prefix

def validate_memory_context_items(memory_context, *, question_id):
    return memory_context

def truncate_memory_context(memory_context, *, max_tokens, question_id):
    return memory_context, 3, 3

def build_messages(*, system_prompt, question_text, image_path, memory_context):
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": question_text}]
    return messages, messages
""",
        encoding="utf-8",
    )
    (harness / "transformers.py").write_text(
        """
class AutoProcessor:
    @staticmethod
    def from_pretrained(model, *, revision):
        return object()
""",
        encoding="utf-8",
    )
    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.chdir(tmp_path)
    probe = subprocess.run(
        [
            str(interpreter),
            "-c",
            ("import importlib.util; assert importlib.util.find_spec('powercontext_eval_longmemeval_v2') is None"),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stderr
    # Supply runtime dependencies without installing anything into the harness venv.
    dependency_path = str(Path(pydantic.__file__).resolve().parent.parent)
    monkeypatch.setenv("PYTHONPATH", dependency_path)
    monkeypatch.setattr(prepare_smoke, "validate_harness_checkout", lambda root: None)

    result = prepare_reader_inputs_smoke(
        retrieval_dir=Path("retrieval"),
        harness_root=Path("harness"),
        harness_python=Path("harness-venv") / interpreter_suffix,
        output_dir=Path("prepared"),
        processor_revision="processor-sha",
    )

    prepared = json.loads(result.prompts_path.read_text(encoding="utf-8"))
    assert Path(prepared["system_prompt"]).resolve() == venv_root.resolve()
    assert prepared["memory_context"] == [{"type": "text", "value": "The saved evidence."}]
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["succeeded"] == 1
    assert summary["failed"] == 0
