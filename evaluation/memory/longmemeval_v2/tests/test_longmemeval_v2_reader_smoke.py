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
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Self
from urllib.request import OpenerDirector

import pytest
from typer.testing import CliRunner

from powercontext_eval_longmemeval_v2.cli import app
from powercontext_eval_longmemeval_v2.costs import ModelPricePolicy
from powercontext_eval_longmemeval_v2.reader_smoke import (
    AnthropicCompatibleReader,
    DeepSeekOpenAIReader,
    ReaderSmokeError,
    run_reader_smoke,
)


@pytest.mark.parametrize("reader_type", [AnthropicCompatibleReader, DeepSeekOpenAIReader])
@pytest.mark.parametrize("url", ["https://provider.example/?token=secret", "https://provider.example/#token"])
def test_reader_rejects_query_and_fragment_urls(reader_type: type[object], url: str) -> None:
    with pytest.raises(ReaderSmokeError, match="without credentials"):
        reader_type(
            url,
            token="token",
            model="model",
            max_tokens=1,
            temperature=0.0,
            timeout_seconds=1,
        )  # type: ignore[operator]


class FakeReader:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict[str, object]]]] = []

    def complete(self, *, system: str, content: list[dict[str, object]]) -> Mapping[str, object]:
        self.calls.append((system, content))
        return {
            "model": "deepseek-flash-latest",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "\\boxed{answer}"}],
            "usage": {"input_tokens": 123, "output_tokens": 7},
        }


def prepared_artifacts(tmp_path: Path) -> Path:
    root = tmp_path / "prepared"
    root.mkdir()
    (root / "prepare-manifest.json").write_text(
        json.dumps(
            {
                "classification": "smoke-subset-prepare-only",
                "reader": None,
                "judge": None,
            }
        ),
        encoding="utf-8",
    )
    (root / "prepare-summary.json").write_text(
        json.dumps(
            {
                "classification": "smoke-subset-prepare-only",
                "question_count": 10,
                "failed": 0,
            }
        ),
        encoding="utf-8",
    )
    with (root / "prepared-prompts.jsonl").open("w", encoding="utf-8") as stream:
        for index in range(10):
            stream.write(
                json.dumps(
                    {
                        "question_id": f"question-{index}",
                        "sequence": index + 1,
                        "domain": "web",
                        "scope_id": "scope-1",
                        "haystack_digest": "digest-1",
                        "prompt_sha256": f"prompt-{index}",
                        "gold_answer": "must-not-be-copied",
                        "messages": [
                            {"role": "system", "content": "system"},
                            {
                                "role": "user",
                                "content": [{"type": "text", "text": "question"}],
                            },
                        ],
                    }
                )
                + "\n"
            )
    return root


def test_reader_smoke_writes_responses_and_never_persists_credentials_or_gold(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    transport = FakeReader()
    output = tmp_path / "reader"
    monkeypatch.setenv("PRIVATE_TOKEN_ENV", "super-secret-token")

    result = run_reader_smoke(
        prepared_dir=prepared_artifacts(tmp_path),
        output_dir=output,
        base_url_env="PRIVATE_BASE_URL_ENV",
        token_env="PRIVATE_TOKEN_ENV",
        model="deepseek-flash-latest",
        max_questions=1,
        transport=transport,
    )

    assert transport.calls == [("system", [{"type": "text", "text": "question"}])]
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert manifest["reader"]["token_env"] == "PRIVATE_TOKEN_ENV"
    assert "super-secret-token" not in json.dumps(manifest)
    [output_record] = [json.loads(line) for line in result.outputs_path.read_text(encoding="utf-8").splitlines()]
    assert output_record["response_text"] == "\\boxed{answer}"
    assert output_record["usage"] == {"input_tokens": 123, "output_tokens": 7}
    assert "gold_answer" not in output_record
    summary = json.loads(result.summary_path.read_text(encoding="utf-8"))
    assert summary["usage"] == {"input_tokens": 123, "output_tokens": 7}
    assert summary["cost"]["cost_usd"] is None
    assert summary["cost"]["unavailable_reason"] == "no price policy was configured for this run"


def test_reader_smoke_refuses_to_overwrite_before_loading_prepared_artifacts(
    tmp_path: Path,
) -> None:
    output = tmp_path / "reader"
    output.mkdir()

    with pytest.raises(ReaderSmokeError, match="Refusing to overwrite"):
        run_reader_smoke(
            prepared_dir=tmp_path / "missing",
            output_dir=output,
            transport=FakeReader(),
        )


def test_deepseek_reader_uses_openai_messages_and_disables_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[Any] = []

    class Response:
        status = 200

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "model": "deepseek-flash",
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": "\\boxed{answer}"},
                        }
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 3},
                }
            ).encode()

    def fake_urlopen(request: Any, *, timeout: float) -> Response:
        assert timeout == 30
        requests.append(request)
        return Response()

    monkeypatch.setattr(OpenerDirector, "open", lambda self, *args, **kwargs: fake_urlopen(*args, **kwargs))
    reader = DeepSeekOpenAIReader(
        "https://api.deepseek.com",
        token="super-secret-token",
        model="deepseek-flash",
        max_tokens=512,
        temperature=0.0,
        timeout_seconds=30,
    )

    response = reader.complete(system="system", content=[{"type": "text", "text": "question"}])

    assert response["usage"] == {"input_tokens": 12, "output_tokens": 3}
    assert response["content"] == [{"type": "text", "text": "\\boxed{answer}"}]
    request = requests[0]
    assert request.full_url == "https://api.deepseek.com/chat/completions"
    payload = json.loads(request.data)
    assert payload["model"] == "deepseek-flash"
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": [{"type": "text", "text": "question"}]},
    ]
    assert "super-secret-token" not in request.data.decode()


def test_reader_smoke_preserves_usage_when_a_completed_response_has_no_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class NoTextResponse:
        status = 200

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(
                {
                    "model": "deepseek-flash",
                    "choices": [{"finish_reason": "stop", "message": {"content": "   "}}],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 20,
                        "prompt_cache_hit_tokens": 40,
                        "prompt_cache_miss_tokens": 60,
                    },
                }
            ).encode()

    def fake_urlopen(request: Any, *, timeout: float) -> NoTextResponse:
        return NoTextResponse()

    monkeypatch.setattr(OpenerDirector, "open", lambda self, *args, **kwargs: fake_urlopen(*args, **kwargs))
    reader = DeepSeekOpenAIReader(
        "https://api.deepseek.com",
        token="token",
        model="deepseek-flash",
        max_tokens=512,
        temperature=0.0,
        timeout_seconds=30,
    )
    policy = ModelPricePolicy(
        provider="deepseek-openai",
        model="deepseek-flash",
        currency="USD",
        input_cache_hit_price_per_million=1.0,
        input_cache_miss_price_per_million=2.0,
        output_price_per_million=3.0,
        price_policy_revision="test-prices",
    )
    output = tmp_path / "reader"

    with pytest.raises(ReaderSmokeError, match="Reader failed for 10"):
        run_reader_smoke(
            prepared_dir=prepared_artifacts(tmp_path),
            output_dir=output,
            provider="deepseek-openai",
            model="deepseek-flash",
            transport=reader,
            price_policy=policy,
        )

    summary = json.loads((output / "reader-summary.json").read_text(encoding="utf-8"))
    assert summary["failed"] == 10
    assert summary["usage"] == {
        "input_tokens": 1_000,
        "output_tokens": 200,
        "input_cache_hit_tokens": 400,
        "input_cache_miss_tokens": 600,
    }
    assert summary["cost"]["cost_usd"] == pytest.approx(2_200 / 1_000_000)
    failures = [
        json.loads(line) for line in (output / "reader-failures.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(failures) == 10
    assert all(failure["error_type"] == "ReaderResponseError" for failure in failures)
    assert all(
        failure["usage"]
        == {
            "input_tokens": 100,
            "output_tokens": 20,
            "input_cache_hit_tokens": 40,
            "input_cache_miss_tokens": 60,
        }
        for failure in failures
    )
    assert all(isinstance(failure["reader_latency_ms"], (int, float)) for failure in failures)
    assert (output / "reader-outputs.jsonl").read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("reader_type", [AnthropicCompatibleReader, DeepSeekOpenAIReader])
@pytest.mark.parametrize(
    ("url", "allow_http"),
    [
        ("http://127.0.0.1:1", False),
        ("http://user:secret@127.0.0.1:1", True),
        ("http://@127.0.0.1:1", True),
        ("http://127.0.0.1:1/?token=secret", True),
        ("http://127.0.0.1:1/#token", True),
        ("file:///tmp/model", True),
    ],
)
def test_reader_http_opt_in_does_not_accept_unsafe_urls(reader_type: Any, url: str, allow_http: bool) -> None:
    with pytest.raises(ReaderSmokeError, match="without credentials"):
        reader_type(
            url,
            token="secret",
            model="model",
            max_tokens=10,
            temperature=0,
            timeout_seconds=1,
            allow_insecure_http=allow_http,
        )


@pytest.mark.parametrize("provider", ["anthropic-compatible", "deepseek-openai"])
def test_reader_cli_requires_http_opt_in_and_records_it(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    model_http_server: Any,
    provider: str,
) -> None:
    server = model_http_server()
    output = tmp_path / "reader"
    monkeypatch.setenv("MODEL_TEST_TOKEN", "local-test-token")
    arguments = [
        "reader-smoke",
        "--prepared-dir",
        str(prepared_artifacts(tmp_path)),
        "--output-dir",
        str(output),
        "--provider",
        provider,
        "--base-url",
        server.url,
        "--token-env",
        "MODEL_TEST_TOKEN",
        "--max-questions",
        "1",
    ]
    rejected = CliRunner().invoke(app, arguments)
    assert rejected.exit_code == 1, rejected.output
    assert "HTTPS" in rejected.output
    assert not output.exists()
    assert not server.calls

    accepted = CliRunner().invoke(app, [*arguments, "--allow-insecure-http"])
    assert accepted.exit_code == 0, accepted.output
    assert len(server.calls) == 1
    assert server.calls[0]["authorization"] == "Bearer local-test-token"
    expected_path = "/v1/messages" if provider == "anthropic-compatible" else "/chat/completions"
    assert server.calls[0]["path"] == expected_path
    manifest = json.loads((output / "reader-manifest.json").read_text())
    assert manifest["reader"]["allow_insecure_http"] is True
    summary = json.loads((output / "reader-summary.json").read_text())
    assert summary["succeeded"] == 1
    assert summary["failed"] == 0
    assert all("local-test-token" not in path.read_text() for path in output.iterdir())


@pytest.mark.parametrize("reader_type", [AnthropicCompatibleReader, DeepSeekOpenAIReader])
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_reader_does_not_forward_credentials_on_redirect(reader_type: Any, status: int, model_http_server: Any) -> None:
    origin, destination = model_http_server(), model_http_server()
    origin.redirect_url = destination.url + "/steal-token"
    origin.redirect_status = status
    reader = reader_type(
        origin.url,
        token="local-test-token",
        model="model",
        max_tokens=10,
        temperature=0,
        timeout_seconds=2,
        allow_insecure_http=True,
    )
    with pytest.raises(ReaderSmokeError, match=f"HTTP {status}"):
        reader.complete(system="system", content=[{"type": "text", "text": "question"}])
    assert len(origin.calls) == 1
    assert not destination.calls
