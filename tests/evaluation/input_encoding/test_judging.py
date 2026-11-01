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
import threading
from collections import Counter
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from evaluation.performance.input_encoding import judging
from evaluation.performance.input_encoding.dataset import digest, projection, sources
from evaluation.performance.input_encoding.identity import ConfigurationIdentityError
from evaluation.performance.input_encoding.report import judge_report
from powercontext.builtin.inference.errors import InferencePrivacyError, InvalidInferenceOutputError
from powercontext.builtin.inference.models import InferenceUsage
from powercontext.builtin.runtime.config import InferenceConfig


@pytest.mark.parametrize("detail", ["provider did not return a valid result", "untrusted-provider-secret"])
def test_judge_failure_keeps_usage_but_only_allowlisted_error_detail(tmp_path, detail):
    class Generator:
        async def generate(self, value):
            error = InvalidInferenceOutputError("generate", detail)
            error.usage = InferenceUsage(requests=2, input_tokens=100, output_tokens=25)
            raise error

    path = tmp_path / "record.json"
    record = asyncio.run(judging._call(Generator(), judging.JudgeInput(), path))
    assert record["status"] == "failed"
    assert record["usage"]["total_tokens"] == 125
    assert record.get("error_detail") == (detail if detail == "provider did not return a valid result" else None)
    assert "untrusted-provider-secret" not in path.read_text()


def test_judge_records_safe_privacy_diagnostics_and_wrapped_usage(tmp_path):
    class Generator:
        async def generate(self, value):
            error = InferencePrivacyError(
                "before_release",
                "withhold",
                ("response_incomplete",),
                usage=InferenceUsage(requests=1, input_tokens=100, output_tokens=4096),
            )
            raise RuntimeError("untrusted-provider-secret") from error

    path = tmp_path / "record.json"
    record = asyncio.run(judging._call(Generator(), judging.JudgeInput(), path))
    assert record["privacy_error"] == {
        "phase": "before_release",
        "decision": "withhold",
        "reason_codes": ["response_incomplete"],
    }
    assert record["usage"]["total_tokens"] == 4196
    assert "untrusted-provider-secret" not in path.read_text()


def test_judge_applies_protection_and_output_budget_before_opening_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-provider-before")
    (tmp_path / "manifest.json").write_text(json.dumps({"model": "openai-chat:example"}))
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"mode": "enforcing", "policy_version": "test-policy"}))
    args = SimpleNamespace(
        dataset=tmp_path / "unused-dataset",
        env_file=tmp_path / "unused-env",
        output=tmp_path,
        protection_config=policy,
        max_output_tokens=4096,
        timeout=180,
        concurrency=1,
    )
    opened = []
    generators = []

    @contextmanager
    def settings_context(**kwargs):
        yield SimpleNamespace(
            inference=InferenceConfig(
                generation_model="openai-chat:example",
                generation_model_settings={"max_tokens": 7},
                generation_streaming="auto",
                generation_stream_idle_timeout_seconds=17.5,
            )
        )

    async def open_model(model, **kwargs):
        opened.append(kwargs)
        return None, object()

    def generator(**kwargs):
        generators.append(kwargs)
        return object()

    monkeypatch.setattr(judging, "server_settings_context", settings_context)
    monkeypatch.setattr(judging, "load_catalog", lambda path: object())
    monkeypatch.setattr(judging, "_open_pydantic_ai_model", open_model)
    monkeypatch.setattr(judging, "PydanticAIStructuredGenerator", generator)
    asyncio.run(judging.judge(args))
    assert opened[0]["protection"].mode == "enforcing"
    assert opened[0]["protection"].policy_version == "test-policy"
    assert opened[0]["disable_provider_retries"] is True
    assert opened[0]["streaming"] == "auto"
    assert opened[0]["stream_idle_timeout_seconds"] == 17.5
    assert all(item["limits"].max_output_tokens_per_request == 4096 for item in generators)
    assert all(item["model_settings"]["max_tokens"] == 4096 for item in generators)
    saved = json.loads((tmp_path / "judge/manifest.json").read_text())
    assert saved["max_output_tokens"] == 4096
    assert saved["protection_mode"] == "enforcing"
    assert set(saved["prompt_hashes"]) == {"author", "solver", "grader"}
    assert saved["configuration_identity"]["private_config_digest"]

    before = (tmp_path / "judge/manifest.json").read_bytes()
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-provider-after")
    with pytest.raises(ConfigurationIdentityError, match="Private configuration changed"):
        asyncio.run(judging.judge(args))
    assert (tmp_path / "judge/manifest.json").read_bytes() == before
    assert not (tmp_path / "judge/questions").exists()
    assert not (tmp_path / "judge/answers").exists()


def test_automatic_judge_grades_successful_outputs_despite_oversized_failed_case(tmp_path, monkeypatch):
    successful = SimpleNamespace(
        instance_id="successful-case",
        repo="synthetic/repository",
        problem_statement="Repair validation.",
        patch="+validate(value)\n",
        test_patch="+assert validation_rejects_invalid\n",
    )
    oversized = SimpleNamespace(**{**vars(successful), "instance_id": "oversized-case", "patch": "x" * (37 * 12_000)})
    assert len(sources(oversized)) == 39
    with pytest.raises(ValidationError):
        projection(oversized)
    catalog = {case.instance_id: case for case in (successful, oversized)}
    monkeypatch.setattr(judging, "load_catalog", lambda path: SimpleNamespace(require=catalog.__getitem__))
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-provider-key")
    jobs = [
        {"instance_id": successful.instance_id, "mode": "off", "repeat": 0},
        {"instance_id": oversized.instance_id, "mode": "active", "repeat": 0},
    ]
    (tmp_path / "manifest.json").write_text(json.dumps({"model": "openai-chat:example", "jobs": jobs}))
    (tmp_path / "attempts").mkdir()
    attempts = []
    for job, status in zip(jobs, ("succeeded", "failed"), strict=True):
        row = {**job, "attempt_id": digest(job)[:24], "status": status, "content": {"lesson": "Repair validation."}}
        attempts.append(row)
        (tmp_path / "attempts" / f"{row['attempt_id']}.json").write_text(json.dumps(row))

    @contextmanager
    def settings_context(**kwargs):
        yield SimpleNamespace(inference=InferenceConfig(generation_model="openai-chat:example"))

    async def open_model(*args, **kwargs):
        return None, object()

    calls = []

    class Generator:
        def __init__(self, **kwargs):
            self.output_type = kwargs["output_type"]

        async def generate(self, value):
            calls.append(self.output_type)
            if self.output_type is judging.TransferQuestions:
                output = judging.TransferQuestions(
                    questions=[
                        judging.TransferQuestion(
                            question="What should be repaired?",
                            expected_answer="Validation.",
                            source_excerpt="Repair validation.",
                        )
                        for _ in range(3)
                    ]
                )
            elif self.output_type is judging.TransferAnswers:
                output = judging.TransferAnswers(answers=["Validation."] * 3)
            else:
                output = judging.Assessment(
                    grounding=2,
                    contradiction_free=2,
                    outcome_honesty=2,
                    actionability=2,
                    transfer_correct=[True] * 3,
                    explanation="Synthetic controlled assessment.",
                )
            return SimpleNamespace(output=output, usage=None)

    monkeypatch.setattr(judging, "server_settings_context", settings_context)
    monkeypatch.setattr(judging, "_open_pydantic_ai_model", open_model)
    monkeypatch.setattr(judging, "PydanticAIStructuredGenerator", Generator)
    args = SimpleNamespace(
        dataset=tmp_path / "unused-dataset",
        env_file=tmp_path / "unused-env",
        output=tmp_path,
        protection_config=None,
        max_output_tokens=4096,
        timeout=180,
        concurrency=1,
    )
    asyncio.run(judging.judge(args))
    assert calls == [judging.TransferQuestions, judging.TransferAnswers, judging.Assessment]
    assert not (tmp_path / "judge/questions" / f"{digest(oversized.instance_id)[:24]}.json").exists()
    report = judge_report(tmp_path, attempts, Counter({"off": 1, "active": 1}))
    assert report["arms"]["off"]["quality_status_counts"]["assessed"] == 1
    assert report["arms"]["active"]["quality_status_counts"]["producer_failed"] == 1
    assert report["arms"]["active"]["assessed_attempts"] == 0
    assert report["arms"]["active"]["planned_attempts"] == 1
    assert report["arms"]["active"]["transfer_questions_all_planned"] == 3


@pytest.mark.parametrize("streaming", ["auto", "disabled"])
def test_judge_uses_configured_streaming_for_real_provider_requests(tmp_path, monkeypatch, streaming):
    instance = SimpleNamespace(
        instance_id="fixture-case",
        repo="synthetic/repository",
        problem_statement="Repair validation.",
        patch="+validate(value)\n",
        test_patch="+assert validation_rejects_invalid\n",
    )
    monkeypatch.setattr(judging, "load_catalog", lambda path: SimpleNamespace(require=lambda name: instance))
    job = {"instance_id": instance.instance_id, "mode": "off", "repeat": 0}
    (tmp_path / "manifest.json").write_text(json.dumps({"model": "openai-chat:example", "jobs": [job]}))
    (tmp_path / "attempts").mkdir()
    attempt_id = digest(job)[:24]
    (tmp_path / "attempts" / f"{attempt_id}.json").write_text(
        json.dumps({
            **job,
            "attempt_id": attempt_id,
            "status": "succeeded",
            "content": {"lesson": "Repair validation."},
        })
    )
    outputs = [
        {
            "questions": [
                {
                    "question": "What should be repaired?",
                    "expected_answer": "Validation.",
                    "source_excerpt": "Repair validation.",
                }
            ]
            * 3
        },
        {"answers": ["Validation."] * 3},
        {
            "grounding": 2,
            "contradiction_free": 2,
            "outcome_honesty": 2,
            "actionability": 2,
            "transfer_correct": [True] * 3,
            "explanation": "Synthetic controlled assessment.",
        },
    ]
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            output = outputs[len(requests)]
            requests.append(request)
            response = {"id": "chatcmpl-fixture", "created": 0, "model": "example"}
            if request.get("stream"):
                response.update({
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": {"content": json.dumps(output)}, "finish_reason": None}],
                })
                terminal = {**response, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                payload = b"".join(b"data: " + json.dumps(row).encode() + b"\n\n" for row in (response, terminal))
                payload += b"data: [DONE]\n\n"
                content_type = "text/event-stream"
            else:
                response.update({
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": json.dumps(output)},
                            "finish_reason": "stop",
                        }
                    ],
                })
                payload = json.dumps(response).encode()
                content_type = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format: str, *args) -> None:  # noqa: A002
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = tmp_path / "fixture.env"
    environment.write_text(
        "OPENAI_API_KEY=synthetic-provider-key\n"
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai-chat:example\n"
        f"POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL=http://127.0.0.1:{server.server_port}/v1\n"
        f"POWERCONTEXT_SERVER_INFERENCE_GENERATION_STREAMING={streaming}\n"
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_STREAM_IDLE_TIMEOUT_SECONDS=17.5\n"
    )
    args = SimpleNamespace(
        dataset=tmp_path / "unused-dataset",
        env_file=environment,
        output=tmp_path,
        protection_config=None,
        max_output_tokens=4096,
        timeout=180,
        concurrency=1,
    )
    try:
        asyncio.run(judging.judge(args))
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    assert len(requests) == 3
    assert all(request.get("stream", False) is (streaming == "auto") for request in requests)
    assessment = json.loads((tmp_path / "judge/assessments" / f"{attempt_id}.json").read_text())
    assert assessment["status"] == "succeeded"
    assert assessment["output"]["transfer_correct"] == [True] * 3
