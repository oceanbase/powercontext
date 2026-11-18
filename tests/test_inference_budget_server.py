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

"""A local budget rejection is a semantic request outcome, not an internal failure."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.exceptions import ToolError

from powercontext.builtin.inference import InferenceBudgetExceededError
from powercontext.builtin.inference.budget import AdmissionDecision
from powercontext.server.app import create_app
from powercontext.server.mcp import create_mcp_server


def _rejection() -> InferenceBudgetExceededError:
    return InferenceBudgetExceededError(
        AdmissionDecision(
            allowed=False,
            operation="skill.generate",
            dimension="context_tokens",
            estimated_value=130_000,
            limit_value=114_654,
            output_reserve_tokens=4_096,
            safety_margin_tokens=6_250,
            estimator_id="character:weighted",
            estimator_version="1",
        )
    )


class _RejectingSkill:
    def for_scope(self, scope_id: str, /) -> _RejectingSkill:
        assert scope_id == "scope-a"
        return self

    async def generate(self, request: object, **_kwargs: Any) -> Any:
        raise _rejection()


def test_budget_rejection_reports_the_decision_over_http() -> None:
    client = TestClient(create_app(application=SimpleNamespace(skill=_RejectingSkill())))

    response = client.post(
        "/v1/skill/generate",
        json={"scope_id": "scope-a", "origin": "source", "source_refs": [], "artifact_refs": []},
    )

    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "inference_budget_exceeded"
    assert error["message"] == "The inference request does not fit the configured budget."
    assert error["details"] == {
        "operation": "skill.generate",
        "dimension": "context_tokens",
        "estimated_value": 130_000,
        "limit_value": 114_654,
        "unit": "tokens",
        "output_reserve_tokens": 4_096,
        "safety_margin_tokens": 6_250,
        "estimator_id": "character:weighted",
        "estimator_version": "1",
        "phase": "initial_dispatch",
    }


def test_mcp_surfaces_the_same_budget_decision() -> None:
    async def call() -> str:
        async with Client(
            create_mcp_server(create_app(application=SimpleNamespace(skill=_RejectingSkill())))
        ) as client:
            with pytest.raises(ToolError) as rejected:
                await client.call_tool(
                    "generate_skill",
                    {"scope_id": "scope-a", "origin": "source", "source_refs": [], "artifact_refs": []},
                )
            return str(rejected.value)

    message = asyncio.run(call())

    assert "inference_budget_exceeded" in message
    assert "skill.generate" in message


_BUDGET_FIELDS = (
    "operation",
    "budget_dimension",
    "budget_phase",
    "estimated_value",
    "limit_value",
    "budget_unit",
    "estimator_id",
    "outcome",
)


def test_budget_rejection_is_observable_without_request_content(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="powercontext.server.app")
    client = TestClient(create_app(application=SimpleNamespace(skill=_RejectingSkill())))

    response = client.post(
        "/v1/skill/generate",
        json={"scope_id": "scope-a", "origin": "source", "source_refs": [], "artifact_refs": []},
    )

    assert response.status_code == 422
    records = [record for record in caplog.records if getattr(record, "event", None) == "inference.budget.rejected"]
    assert len(records) == 1
    record = records[0]
    fields = {name: getattr(record, name, None) for name in _BUDGET_FIELDS}
    assert fields == {
        "operation": "skill.generate",
        "budget_dimension": "context_tokens",
        "budget_phase": "initial_dispatch",
        "estimated_value": 130_000,
        "limit_value": 114_654,
        "budget_unit": "tokens",
        "estimator_id": "character:weighted",
        "outcome": "rejected",
    }


@pytest.mark.parametrize("log_format", ["json", "console"])
def test_budget_fields_survive_configured_log_output(log_format: str) -> None:
    import json
    import subprocess
    import sys

    code = f"""
from powercontext.server.settings import ServerLoggingConfig
from powercontext.server.logging import configure_server_logging
from powercontext.server.app import _observe_inference_budget_rejection
from powercontext.builtin.inference.errors import InferenceBudgetExceededError
from powercontext.builtin.inference.budget import AdmissionDecision
configure_server_logging(ServerLoggingConfig(format={log_format!r}))
_observe_inference_budget_rejection(InferenceBudgetExceededError(AdmissionDecision(
    allowed=False, operation="skill.generate", dimension="context_tokens", estimated_value=100, limit_value=50,
    estimator_id="character:weighted", estimator_version="1", output_reserve_tokens=128, safety_margin_tokens=10,
)))
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=30)
    output = result.stdout + result.stderr
    if log_format == "json":
        payloads = [json.loads(line) for line in output.splitlines() if line.startswith("{")]
        fields = next(item for item in payloads if item.get("event") == "inference.budget.rejected")
        assert fields["budget_dimension"] == "context_tokens"
        assert fields["budget_unit"] == "tokens"
        assert fields["unit"] == "inference"
        assert fields["estimated_value"] == 100
        assert fields["limit_value"] == 50
        assert fields["output_reserve_tokens"] == 128
        assert fields["safety_margin_tokens"] == 10
        assert fields["estimator_version"] == "1"
    else:
        for field in (
            'budget_dimension="context_tokens"',
            'budget_unit="tokens"',
            "limit_value=50",
            'estimator_version="1"',
        ):
            assert field in output
