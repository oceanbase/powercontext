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

"""Real HTTP, Memory processes and code execution with simulated external models.

The deterministic model responses make the workflow repeatable; these tests do not
claim that a real generation or Jev service produces the same answers.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from examples.systemone import generation, server
from examples.systemone.generation import Settings
from examples.systemone.scenario import POLICY, TASK
from powercontext.builtin.inference import InferenceUsage
from powercontext.builtin.runtime import DecisionOutcome, DecisionRequest, DecisionResult

_BASELINE = "def cents(text: str) -> int:\n    return round(float(text) * 100)\n"
_REMEMBERED = """\
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

def cents(text: str) -> int:
    try:
        value = Decimal(text)
        if not value.is_finite():
            raise ValueError("finite amount required")
        return int((value * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except InvalidOperation as error:
        raise ValueError("invalid amount") from error
"""


def _settings(*, generation_enabled: bool = True, review_enabled: bool = True) -> Settings:
    return Settings(
        generation_endpoint="https://generation.example/chat/completions" if generation_enabled else "",
        generation_model="test-coder" if generation_enabled else "",
        generation_api_key=SecretStr("test-generation-secret" if generation_enabled else ""),
        jev_endpoint="https://jev.example/v1/systemone" if review_enabled else "",
        jev_model="test-jev" if review_enabled else "",
        jev_api_key=SecretStr("test-jev-secret" if review_enabled else ""),
        laya_endpoint="",
        laya_model="",
        laya_api_key=SecretStr(""),
        laya_checkpoint="",
    )


class _GenerationService:
    """Replace external HTTP only, retaining the example's generation adapter."""

    def __init__(self, *, fail_context_once: bool = False) -> None:
        self.requests: list[dict[str, Any]] = []
        self.contexts: list[str | None] = []
        self.fail_context_once = fail_context_once

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-generation-secret"
        payload = json.loads(request.content)
        self.requests.append(payload)
        has_context = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1" in payload["messages"][1]["content"]
        if has_context and self.fail_context_once:
            self.fail_context_once = False
            raise httpx.ReadTimeout("test-upstream-private-error", request=request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": _REMEMBERED if has_context else _BASELINE}}
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 40, "total_tokens": 60},
            },
        )

    async def generate(self, _client: httpx.AsyncClient, settings: Settings, context: str | None) -> dict[str, Any]:
        self.contexts.append(context)
        async with httpx.AsyncClient(transport=httpx.MockTransport(self.handle)) as client:
            return await generation.generate(client, settings, context)


async def _step(client: httpx.AsyncClient, run_id: str, name: str) -> dict[str, Any]:
    response = await client.post(f"/api/runs/{run_id}/steps/{name}")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["error"] is None, result["error"]
    assert result["busy"] is None
    return result


@pytest.mark.parametrize("review_timeout", [False, True], ids=["advisory-yes", "advisory-timeout"])
def test_experiment_persists_actual_results_across_processes_and_server_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, review_timeout: bool
) -> None:
    settings = _settings()
    service = _GenerationService()
    review_requests: list[DecisionRequest] = []
    monkeypatch.setattr(server, "generate", service.generate)

    class ReviewModel:
        policy_id = "explicit-test-reviewer"

        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        async def evaluate(self, request: DecisionRequest, /) -> DecisionResult:
            review_requests.append(request)
            if review_timeout:
                message = "simulated provider timeout"
                raise TimeoutError(message)
            # Deliberately endorse both implementations: tests must remain independent.
            return DecisionResult(DecisionOutcome.YES, self.policy_id, InferenceUsage(requests=1), confidence=0.9)

    monkeypatch.setattr(server, "SystemOneDecisionModel", ReviewModel)

    async def scenario() -> dict[str, Any]:
        app = server.create_app(settings, tmp_path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            created = await client.post("/api/runs", json={"providers": ["jev"]})
            assert created.status_code == 200
            run_id = created.json()["id"]
            assert (await client.post(f"/api/runs/{run_id}/steps/verify")).status_code == 409
            seeded = await _step(client, run_id, "seed")
            recalled = await _step(client, run_id, "recall")
            generated = await _step(client, run_id, "generate")
            repeated = await _step(client, run_id, "generate")
            assert repeated["generated"] == generated["generated"]
            assert len(service.requests) == 2
            assert recalled["recall"]["isolation"]["status"] == "passed"
            prepared = recalled["recall"]["prepared"]["content"]
            assert POLICY in prepared
            assert "ROUND_HALF_EVEN" not in prepared
            assert "ROUND_HALF_EVEN" in recalled["recall"]["isolation"]["other_prepared"]["content"]
            assert service.contexts == [None, prepared]
            assert service.requests[0]["messages"][1]["content"] == TASK
            assert service.requests[1]["messages"][1]["content"] == (
                TASK + "\n\nProject context recalled by PowerContext:\n" + prepared
            )
            assert service.requests[0]["messages"][0] == service.requests[1]["messages"][0]
            assert {key: value for key, value in service.requests[0].items() if key != "messages"} == {
                key: value for key, value in service.requests[1].items() if key != "messages"
            }

        # A fresh application must continue using persisted state, without generation again.
        reopened = server.create_app(settings, tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=reopened), base_url="http://127.0.0.1"
        ) as client:
            restored = (await client.get(f"/api/runs/{run_id}")).json()
            assert restored["generated"] == generated["generated"]
            await _step(client, run_id, "generate")
            reviewed = await _step(client, run_id, "review")
            assert all(request.evidence == (POLICY,) for request in review_requests)
            assert {request.subject for request in review_requests} == {_BASELINE, _REMEMBERED}
            for verdict in reviewed["reviews"]["jev"].values():
                assert verdict["used_fallback"] is review_timeout
                assert verdict["outcome"] == ("abstain" if review_timeout else "yes")
            await _step(client, run_id, "verify")
            completed = await _step(client, run_id, "finish")
            report = await client.get(f"/api/runs/{run_id}/report")
            assert report.status_code == 200
            assert report.json() == completed
            assert "attachment" in report.headers["content-disposition"]
            assert "test-generation-secret" not in report.text
            assert "test-jev-secret" not in report.text
            assert len(service.requests) == 2
            assert completed["phase"] == "completed"
            pids = {
                seeded["seed"]["pid"],
                recalled["recall"]["pid"],
                completed["saved"]["record_pid"],
                completed["saved"]["pid"],
            }
            assert len(pids) == 4
            assert all(pid > 0 for pid in pids)
            assert run_id in completed["saved"]["prepared"]["content"]
            assert "observed_tests" in completed["saved"]["prepared"]["content"]
            return completed

    completed = asyncio.run(scenario())
    baseline = completed["verification"]["without_memory"]
    remembered = completed["verification"]["with_memory"]
    assert baseline["passed"] < baseline["total"] and baseline["exit_code"] == 1
    assert remembered["passed"] == remembered["total"] and remembered["exit_code"] == 0
    for arm, verification in completed["verification"].items():
        source = (tmp_path / completed["id"] / arm / "amount.py").read_bytes()
        assert verification["code_sha256"] == hashlib.sha256(source).hexdigest()
        assert source.decode() == completed["generated"][arm]["code"]


def test_unconfigured_generation_and_partial_provider_failure_can_resume_without_reviewers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _GenerationService(fail_context_once=True)
    monkeypatch.setattr(server, "generate", service.generate)

    async def scenario() -> None:
        app = server.create_app(_settings(generation_enabled=False, review_enabled=False), tmp_path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            status = (await client.get("/api/status")).json()
            assert status["generation"]["configured"] is False
            assert not any(provider["configured"] for provider in status["providers"])
            assert (await client.post("/api/runs", json={"providers": ["jev"]})).status_code == 400
            run_id = (await client.post("/api/runs", json={"providers": []})).json()["id"]
            await _step(client, run_id, "seed")
            await _step(client, run_id, "recall")
            failed = (await client.post(f"/api/runs/{run_id}/steps/generate")).json()
            assert failed["phase"] == "recalled"
            assert failed["busy"] is None
            assert "GENERATION_ENDPOINT" in failed["error"]
            assert not service.requests

        configured = server.create_app(_settings(review_enabled=False), tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=configured), base_url="http://127.0.0.1"
        ) as client:
            failed = (await client.post(f"/api/runs/{run_id}/steps/generate")).json()
            assert failed["phase"] == "recalled"
            assert set(failed["generated"]) == {"without_memory"}
            assert "test-upstream-private-error" not in failed["error"]
            assert failed["busy"] is None

        for changed_target in (
            {"generation_model": "a-different-coder"},
            {"generation_endpoint": "https://a-different-service.example/chat/completions"},
        ):
            changed = server.create_app(_settings(review_enabled=False).model_copy(update=changed_target), tmp_path)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=changed), base_url="http://127.0.0.1"
            ) as client:
                rejected = (await client.post(f"/api/runs/{run_id}/steps/generate")).json()
                assert rejected["phase"] == "recalled"
                assert "Create a new experiment" in rejected["error"]
                assert rejected["generated"] == failed["generated"]
                assert len(service.requests) == 2

        reopened = server.create_app(_settings(review_enabled=False), tmp_path)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=reopened), base_url="http://127.0.0.1"
        ) as client:
            await _step(client, run_id, "generate")
            reviewed = await _step(client, run_id, "review")
            assert reviewed["reviews"] == {}
            await _step(client, run_id, "verify")
            completed = await _step(client, run_id, "finish")
            assert completed["phase"] == "completed"
            assert len(service.requests) == 3
            assert sum(request["messages"][1]["content"] == TASK for request in service.requests) == 1

    asyncio.run(scenario())


def test_local_api_rejects_cross_origin_or_untrusted_host_requests(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = server.create_app(_settings(), tmp_path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            for headers in (
                {"origin": "https://untrusted.example"},
                {"host": "untrusted.example"},
                {"sec-fetch-site": "cross-site"},
            ):
                denied = await client.post("/api/runs", json={"providers": []}, headers=headers)
                assert denied.status_code == 403
            status = await client.get("/api/status", headers={"origin": "http://127.0.0.1"})
            assert status.status_code == 200
            assert status.json()["runs"] == []
            assert status.headers["cache-control"] == "no-store"
            assert (await client.get("/api/runs/not-a-uuid")).status_code == 404

    asyncio.run(scenario())


def test_concurrent_requests_and_cancelled_generation_preserve_completed_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _GenerationService()

    async def scenario() -> None:
        reached_second_arm = asyncio.Event()
        block_second_arm = True

        async def delayed_generate(
            client: httpx.AsyncClient, settings: Settings, context: str | None
        ) -> dict[str, Any]:
            if context is not None and block_second_arm:
                reached_second_arm.set()
                await asyncio.Event().wait()
            return await service.generate(client, settings, context)

        monkeypatch.setattr(server, "generate", delayed_generate)
        app = server.create_app(_settings(review_enabled=False), tmp_path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1") as client:
            run_id = (await client.post("/api/runs", json={"providers": []})).json()["id"]
            await _step(client, run_id, "seed")
            await _step(client, run_id, "recall")
            active = asyncio.create_task(client.post(f"/api/runs/{run_id}/steps/generate"))
            try:
                await asyncio.wait_for(reached_second_arm.wait(), timeout=10)
                duplicate = await client.post(f"/api/runs/{run_id}/steps/generate")
                assert duplicate.status_code == 409
            finally:
                active.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await active
            interrupted = (await client.get(f"/api/runs/{run_id}")).json()
            assert interrupted["phase"] == "recalled"
            assert interrupted["busy"] is None
            assert "interrupted" in interrupted["error"]
            assert set(interrupted["generated"]) == {"without_memory"}
            block_second_arm = False
            generated = await _step(client, run_id, "generate")
            assert generated["phase"] == "generated"
            assert len(service.requests) == 2

    asyncio.run(scenario())


def test_truncated_generation_is_reported_without_substituting_code() -> None:
    async def scenario() -> None:
        transport = httpx.MockTransport(
            lambda _request: httpx.Response(
                200, json={"choices": [{"finish_reason": "length", "message": {"content": _REMEMBERED}}]}
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(RuntimeError, match="invalid or incomplete output; no code was substituted"):
                await generation.generate(client, _settings(), None)

    asyncio.run(scenario())


def test_cancelling_a_starting_memory_worker_reaps_the_real_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_spawn = asyncio.create_subprocess_exec
    children: list[asyncio.subprocess.Process] = []

    async def scenario() -> None:
        started = asyncio.Event()
        return_child = asyncio.Event()

        async def spawning(*args: Any, **kwargs: Any) -> asyncio.subprocess.Process:
            child = await original_spawn(*args, **kwargs)
            children.append(child)
            started.set()
            await return_child.wait()
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawning)
        experiment = server.create_app(_settings(review_enabled=False), tmp_path).state.experiment
        run = experiment.create([])
        active = asyncio.create_task(experiment.worker(run, "seed"))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
        finally:
            active.cancel()
            return_child.set()
            with pytest.raises(asyncio.CancelledError):
                await active

    asyncio.run(scenario())
    assert children
    assert all(child.returncode is not None for child in children)
