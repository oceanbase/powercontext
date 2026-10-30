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

"""Deterministic harness checks; controlled models do not establish model comprehension."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest
from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from evaluation.performance.input_encoding import semantics
from powercontext.builtin.inference.errors import InferencePrivacyError
from powercontext.builtin.inference.models import InferenceUsage
from powercontext.builtin.runtime.config import InferenceConfig


def _args(output, **changes):
    return SimpleNamespace(**{
        "output": output,
        "schema": "both",
        "dry_run": False,
        "env_file": output / "synthetic.env",
        "protection_config": None,
        "max_output_tokens": 2048,
        "timeout": 10,
        **changes,
    })


def _records(plan, cases):
    by_id = {case.id: case for case in cases}
    return {
        job["id"]: {
            "id": job["id"],
            "prompt_sha256": job["prompt_sha256"],
            "status": "answered",
            "output": {"answers": by_id[job["case_id"]].expected},
            "score": semantics.score(
                semantics.SemanticAnswer(answers=by_id[job["case_id"]].expected), by_id[job["case_id"]]
            ),
            "usage_event": {"requests": 1, "input_tokens": 10, "output_tokens": 5},
        }
        for job in plan["attempts"]
    }


def test_fixed_probe_has_real_encoding_at_the_asked_v2_fields_and_keeps_short_control():
    cases = semantics.load_cases()
    plan = semantics.build_plan(cases, ("v1", "v2"))
    assert plan["case_count"] == 7
    assert plan["questions_per_mode_and_schema"] == 29
    assert plan["planned_attempts"] == 42
    for job in plan["attempts"]:
        if job["mode"] == "active":
            assert job["candidate_applied"] is job["requires_encoding"]
        assert all(check["covered"] for check in job["required_node_coverage"])
    required = [check for job in plan["attempts"] for check in job["required_node_coverage"]]
    assert {tuple(check["path"]): check["required_root_tag"] for check in required} == {
        ("document", "string_value"): "j",
        ("document", "text"): "s",
    }
    directory = next(case for case in cases if case.id == "directory-roles")
    assert directory.expected["project_path"] == "/home/{{USERNAME_abcdefgh}}/checkout"
    opaque = next(case for case in cases if case.id == "opaque-substitutions")
    assert opaque.expected["visible_token_marker"] == "{{TOKEN_abcdefgh}}"  # noqa: S105 - literal synthetic marker
    assert opaque.expected["environment_marker"] == "${API_KEY}"


@pytest.mark.parametrize(
    ("expected", "actual"),
    [(True, 1), (1, 1.0), (1, "1"), (None, "unknown"), ({"a": 1}, '{"a":1}'), ("a\n\nb\n", "a\nb")],
)
def test_scorer_rejects_type_coercion_or_lost_characters(expected, actual):
    case = semantics.SemanticCase(
        id="literal",
        category="literal",
        requires_encoding=False,
        input=semantics.SemanticInput(document={}, questions=(semantics.Question(id="value", text="Return it."),)),
        expected={"value": expected},
    )
    assert semantics.score(semantics.SemanticAnswer(answers={"value": expected}), case)["correct"]
    assert not semantics.score(semantics.SemanticAnswer(answers={"value": actual}), case)["correct"]


@pytest.mark.parametrize("schema", ["v1", "v2"])
@pytest.mark.parametrize("mode", ["off", "shadow", "active"])
def test_model_receives_selected_representation_without_expected_answers(tmp_path, schema, mode):
    case = semantics.load_cases()[0]
    case = case.model_copy(update={"expected": dict.fromkeys(case.expected, "EXPECTED_ONLY_SENTINEL")})
    job = next(job for job in semantics.build_plan((case,), (schema,))["attempts"] if job["mode"] == mode)
    received = []

    def respond(messages, _):
        received.extend(
            part.content for message in messages for part in message.parts if isinstance(part, UserPromptPart)
        )
        return ModelResponse(
            parts=[TextPart(json.dumps({"answers": case.expected}))],
            usage=RequestUsage(input_tokens=5, output_tokens=3),
        )

    record = asyncio.run(semantics.attempt(FunctionModel(respond), InferenceConfig(), case, job, _args(tmp_path)))
    assert record["status"] == "answered"
    assert received == [job["prompt"]]
    assert "EXPECTED_ONLY_SENTINEL" not in received[0]
    assert (received[0] != case.input.model_dump_json()) is (mode == "active")
    if mode == "active":
        assert json.loads(received[0])["schema"] == semantics.SCHEMAS[schema]


def test_dry_run_never_opens_settings_or_claims_semantic_success(tmp_path, monkeypatch):
    def forbidden(**kwargs):
        pytest.fail("dry-run opened private configuration")

    monkeypatch.setattr(semantics, "server_settings_context", forbidden)
    result = asyncio.run(semantics.run(_args(tmp_path, dry_run=True, env_file=None)))
    assert result["semantic_evaluation"] == "not_run"
    assert result["planned_attempts"] == 42
    assert result["selection_counts"]["applied"] == 12
    assert result["required_rendering_coverage_complete"]
    assert "all_probe_cases_passed" not in result
    assert not (tmp_path / "manifest.json").exists()
    assert not (tmp_path / "attempts").exists()


@pytest.mark.parametrize("status", [None, "running", "error", "unknown"])
def test_report_retains_every_case_and_never_counts_missing_or_unknown_outcomes_as_correct(status):
    cases = semantics.load_cases()
    plan = semantics.build_plan(cases, ("v2",))
    records = _records(plan, cases)
    active = next(job for job in plan["attempts"] if job["mode"] == "active" and job["requires_encoding"])
    if status is None:
        records.pop(active["id"])
    else:
        records[active["id"]]["status"] = status
        records[active["id"]]["usage_event"] = None
    report = semantics.summarize(plan, records)
    assert not report["all_probe_cases_passed"]
    assert not report["billing_complete"]
    assert report["usage"]["total_tokens"] is None
    assert report["usage"]["total_tokens_known_subtotal"] == 20 * 15
    assert report["usage"]["incomplete"]
    assert all(group["planned_cases"] == 7 and group["planned_questions"] == 29 for group in report["groups"])
    assert next(group for group in report["groups"] if group["mode"] == "active")["correct_cases"] == 6


def test_report_rejects_fallback_or_missing_targeted_encoding_even_with_correct_answers():
    cases = semantics.load_cases()
    plan = semantics.build_plan(cases, ("v2",))
    records = _records(plan, cases)
    complete = semantics.summarize(plan, records)
    assert complete["all_probe_cases_passed"]
    active = next(group for group in complete["groups"] if group["mode"] == "active")
    assert active["active_fallback_cases"] == 1
    assert active["active_required_encoded_correct_cases"] == 6
    for kind in ("fallback", "uncovered_node"):
        changed = deepcopy(plan)
        job = next(
            job for job in changed["attempts"] if job["case_id"] == "object-and-json-string" and job["mode"] == "active"
        )
        if kind == "fallback":
            job["candidate_applied"] = False
        else:
            job["required_node_coverage"][0]["covered"] = False
        result = semantics.summarize(changed, records)
        assert not result["all_probe_cases_passed"]
        assert not result["required_encoded_case_coverage_complete"]


def test_missing_usage_counts_do_not_certify_complete_billing():
    cases = semantics.load_cases()
    plan = semantics.build_plan(cases, ("v1",))
    records = _records(plan, cases)
    next(iter(records.values()))["usage_event"] = {
        "requests": 1,
        "input_tokens": None,
        "output_tokens": 5,
        "incomplete": False,
    }
    result = semantics.summarize(plan, records)
    assert result["all_probe_cases_passed"]
    assert not result["billing_complete"]
    assert result["usage"]["total_tokens"] is None
    assert result["usage"]["input_tokens_known_subtotal"] == 200
    assert result["usage"]["incomplete"]


@pytest.mark.parametrize(("input_tokens", "output_tokens"), [(0, 0), (3, 2)])
def test_incomplete_usage_keeps_totals_unknown_in_attempt_and_summary(
    tmp_path, monkeypatch, input_tokens, output_tokens
):
    cases = semantics.load_cases()
    case = cases[0]
    plan = semantics.build_plan(cases, ("v1",))
    job = plan["attempts"][0]

    class Generator:
        async def generate(self, value):
            return SimpleNamespace(
                output=semantics.SemanticAnswer(answers=case.expected),
                usage=InferenceUsage(
                    requests=1, input_tokens=input_tokens, output_tokens=output_tokens, incomplete=True
                ),
            )

    monkeypatch.setattr(semantics, "PydanticAIStructuredGenerator", lambda **kwargs: Generator())
    record = asyncio.run(semantics.attempt(object(), InferenceConfig(), case, job, _args(tmp_path)))
    records = _records(plan, cases)
    records[job["id"]] = record
    report = semantics.summarize(plan, records)
    assert report["all_probe_cases_passed"]
    assert not report["billing_complete"]
    assert record["usage_event"]["input_tokens"] == input_tokens
    for usage, known_input, known_output in (
        (record["usage"], input_tokens, output_tokens),
        (report["usage"], 200 + input_tokens, 100 + output_tokens),
    ):
        assert usage["input_tokens"] is None
        assert usage["output_tokens"] is None
        assert usage["total_tokens"] is None
        assert usage["input_tokens_known_subtotal"] == known_input
        assert usage["output_tokens_known_subtotal"] == known_output
        assert usage["total_tokens_known_subtotal"] == known_input + known_output
        assert usage["incomplete"]


def _mock_live_run(tmp_path, monkeypatch):
    cases = semantics.load_cases()
    plan = semantics.build_plan(cases, ("v1",))
    records = _records(plan, cases)
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-probe-credential")

    @contextmanager
    def settings(**kwargs):
        yield SimpleNamespace(inference=InferenceConfig(generation_model="openai-chat:synthetic"))

    async def open_model(*args, **kwargs):
        return None, object()

    async def attempt(model, config, case, job, args):
        row = records[job["id"]]
        semantics.write_json(args.output / "attempts" / f"{job['id']}.json", row)
        return row

    monkeypatch.setattr(semantics, "server_settings_context", settings)
    monkeypatch.setattr(semantics, "_open_pydantic_ai_model", open_model)
    monkeypatch.setattr(semantics, "attempt", attempt)
    args = _args(tmp_path, schema="v1")
    assert asyncio.run(semantics.run(args))["all_probe_cases_passed"]
    return args, plan


def test_resume_regrades_output_without_replaying_calls(tmp_path, monkeypatch):
    args, plan = _mock_live_run(tmp_path, monkeypatch)
    job = plan["attempts"][0]
    path = tmp_path / "attempts" / f"{job['id']}.json"
    row = json.loads(path.read_text())
    row["output"] = {"answers": {}}
    row["score"]["correct"] = True
    semantics.write_json(path, row)

    async def forbidden(*args, **kwargs):
        pytest.fail("resume opened a provider for saved attempts")

    monkeypatch.setattr(semantics, "_open_pydantic_ai_model", forbidden)
    result = asyncio.run(semantics.run(args))
    assert not result["all_probe_cases_passed"]


@pytest.mark.parametrize("field", ["id", "prompt_sha256"])
def test_resume_rejects_changed_attempt_identity_before_provider(tmp_path, monkeypatch, field):
    args, plan = _mock_live_run(tmp_path, monkeypatch)
    path = tmp_path / "attempts" / f"{plan['attempts'][0]['id']}.json"
    row = json.loads(path.read_text())
    row[field] = "changed"
    semantics.write_json(path, row)
    with pytest.raises(ValueError, match="attempt identity changed"):
        asyncio.run(semantics.run(args))


def test_orphan_attempts_are_rejected_before_private_configuration(tmp_path, monkeypatch):
    semantics.write_json(tmp_path / "attempts" / "orphan.json", {"status": "answered"})

    def forbidden(**kwargs):
        pytest.fail("orphan records reached configuration")

    monkeypatch.setattr(semantics, "server_settings_context", forbidden)
    with pytest.raises(ValueError, match="original manifest"):
        asyncio.run(semantics.run(_args(tmp_path)))


def test_failed_attempt_retains_known_usage_without_raw_provider_errors(tmp_path, monkeypatch):
    class Generator:
        async def generate(self, value):
            error = InferencePrivacyError(
                "before_release",
                "withhold",
                ("response_incomplete",),
                usage=InferenceUsage(requests=1, input_tokens=10, output_tokens=None),
            )
            raise RuntimeError("UNTRUSTED_PROVIDER_DETAIL") from error

    monkeypatch.setattr(semantics, "PydanticAIStructuredGenerator", lambda **kwargs: Generator())
    case = semantics.load_cases()[0]
    job = semantics.build_plan((case,), ("v1",))["attempts"][0]
    record = asyncio.run(semantics.attempt(object(), InferenceConfig(), case, job, _args(tmp_path)))
    assert record["status"] == "error"
    assert record["usage"]["input_tokens_known_subtotal"] == 10
    assert record["usage"]["total_tokens"] is None
    assert record["privacy_error"]["decision"] == "withhold"
    assert "UNTRUSTED_PROVIDER_DETAIL" not in (tmp_path / "attempts" / f"{job['id']}.json").read_text()


def test_changed_runtime_source_rejects_resume_before_provider(tmp_path, monkeypatch):
    args, _ = _mock_live_run(tmp_path, monkeypatch)
    monkeypatch.setattr(semantics, "source_hash", lambda: "changed-runtime-adapter")
    with pytest.raises(ValueError, match="probe identity changed"):
        asyncio.run(semantics.run(args))


def test_provider_open_failure_leaves_fixed_denominators_and_no_success(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-probe-credential")

    @contextmanager
    def settings(**kwargs):
        yield SimpleNamespace(inference=InferenceConfig(generation_model="openai-chat:synthetic"))

    async def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic unavailable provider")  # noqa: TRY003

    monkeypatch.setattr(semantics, "server_settings_context", settings)
    monkeypatch.setattr(semantics, "_open_pydantic_ai_model", unavailable)
    with pytest.raises(RuntimeError, match="synthetic unavailable"):
        asyncio.run(semantics.run(_args(tmp_path)))
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["planned_attempts"] == 42
    assert not report["all_probe_cases_passed"]
    assert all(group["status_counts"] == {"not_run": 7} for group in report["groups"])
    assert report["usage"]["total_tokens"] is None
