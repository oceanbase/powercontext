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

import asyncio
import json
from pathlib import Path

import pytest

from evaluation.memory.atomic_extraction import runner
from evaluation.memory.atomic_extraction.manifest import load_manifest
from evaluation.memory.atomic_extraction.models import CandidateObservation
from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryCandidate

_SECRET_SENTINEL = "Bearer private-provider-secret"  # noqa: S105 - synthetic leak sentinel
_CONTENT_SENTINEL = "PRIVATE_PREDICTED_SOURCE_MARKER"


def test_candidate_observation_accepts_the_description_contract() -> None:
    candidate = AtomicMemoryCandidate(
        kind="fact",
        text="The project uses SQLite.",
        description="Project database choice",
        evidence_ids=("source-1",),
    )

    observation = CandidateObservation.from_candidate(candidate)

    assert observation.kind == candidate.kind
    assert observation.text == candidate.text
    assert observation.evidence_ids == candidate.evidence_ids
    assert CandidateObservation.model_validate_json(observation.model_dump_json()) == observation


def test_provider_bootstrap_failure_writes_incomplete_content_free_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "provider.env"
    env_file.write_text(
        "\n".join((
            "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai:test-model",
            f'POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS=\'{{"Authorization":"{_SECRET_SENTINEL}"}}\'',
            "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL_SETTINGS="
            f'\'{{"temperature":"{_CONTENT_SENTINEL}",'
            f'"openai_prediction":{{"content":"{_CONTENT_SENTINEL}"}}}}\'',
        ))
        + "\n",
        encoding="utf-8",
    )

    async def unavailable(*_args, **_kwargs):
        raise RuntimeError("private provider response")  # noqa: TRY003

    monkeypatch.setattr(runner, "_open_pydantic_ai_model", unavailable)
    output = tmp_path / "run"

    report = asyncio.run(
        runner.run_live_evaluation(
            loaded=load_manifest(),
            env_file=env_file,
            output_directory=output,
            run_id="provider-failure-fixture",
        )
    )

    assert report["status"] == "incomplete"
    assert report["factual_retention"]["false_rejections"] == 0
    assert report["instruction_leakage"]["unobserved"] == report["overall"]["total"]
    manifest = json.loads((output / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["provider"]["header_names"] == ["Authorization"]
    public_settings = manifest["provider"]["model_settings"]
    assert set(public_settings["temperature"]) == {"redacted_sha256"}
    assert set(public_settings["openai_prediction"]) == {"redacted_sha256"}
    artifact_text = "\n".join(path.read_text(encoding="utf-8") for path in output.iterdir())
    assert _SECRET_SENTINEL not in artifact_text
    assert _CONTENT_SENTINEL not in artifact_text
    assert "private provider response" not in artifact_text


def test_safe_settings_preserve_allowlisted_scalars_and_redact_every_other_value() -> None:
    settings = {
        "max_tokens": 1024,
        "temperature": 0,
        "parallel_tool_calls": False,
        "thinking": "high",
        "service_tier": "priority",
        "api_key": "private-api-key",
        "apiKey": "private-camel-api-key",
        "nested": {"access_token": "private-access-token", "tokenizer": "provider-default"},
        "openai_prediction": {"content": _CONTENT_SENTINEL},
        "top_p": _CONTENT_SENTINEL,
        "seed": _CONTENT_SENTINEL,
    }

    safe = runner._safe_settings(settings)

    assert safe["max_tokens"] == 1024
    assert safe["temperature"] == 0
    assert safe["parallel_tool_calls"] is False
    assert safe["thinking"] == "high"
    assert safe["service_tier"] == "priority"
    for key in (
        "api_key",
        "apiKey",
        "nested",
        "openai_prediction",
        "top_p",
        "seed",
    ):
        redacted = safe[key]
        assert isinstance(redacted, dict)
        assert set(redacted) == {"redacted_sha256"}
    assert "private" not in json.dumps(safe)
    assert _CONTENT_SENTINEL not in json.dumps(safe)


@pytest.mark.parametrize(
    "key",
    ("max_tokens", "temperature", "parallel_tool_calls", "thinking", "service_tier"),
)
def test_safe_settings_redact_invalid_values_under_allowlisted_keys(key: str) -> None:
    safe = runner._safe_settings({key: _CONTENT_SENTINEL})
    redacted = safe[key]

    assert isinstance(redacted, dict)
    assert set(redacted) == {"redacted_sha256"}
    assert _CONTENT_SENTINEL not in json.dumps(safe)
