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

from pathlib import Path

import pytest
from pydantic import ValidationError

from powercontext_eval_swebench_pro.artifacts import ArmState
from powercontext_eval_swebench_pro.report import (
    ArmReport,
    InvalidReportBundle,
    MetricSet,
    ReportBundle,
    TestGroupReport,
    render_report,
)


def _valid_bundle() -> ReportBundle:
    return ReportBundle(
        title="SWE-bench Pro evaluation",
        revisions={"powercontext": "b" * 40, "harness": "a" * 40},
        configuration={"model": "gpt-5.6-sol", "effort": "medium"},
        off=ArmReport(
            arm="off",
            state=ArmState.TREATMENT_VALIDATED,
            resolved=True,
            passed=False,
            treatment_valid=True,
            metrics=MetricSet(
                patch_bytes=100,
                input_tokens=200,
                cached_input_tokens=150,
                output_tokens=30,
                reasoning_output_tokens=10,
                elapsed_seconds=12.25,
            ),
        ),
        on=ArmReport(
            arm="on",
            state=ArmState.TREATMENT_VALIDATED,
            resolved=True,
            passed=True,
            treatment_valid=True,
            metrics=MetricSet(
                patch_bytes=90,
                input_tokens=180,
                cached_input_tokens=170,
                output_tokens=25,
                reasoning_output_tokens=12,
                elapsed_seconds=11.5,
            ),
        ),
    )


def test_report_is_deterministic_and_orders_off_before_on() -> None:
    bundle = _valid_bundle()
    first = render_report(bundle)
    second = render_report(bundle)
    assert first == second
    assert first.index("PowerContext OFF") < first.index("PowerContext ON")
    assert "Pass status | FAIL" in first
    assert "Pass status | PASS" in first
    assert "## Comparison" in first
    assert "Pass delta | +1" in first


def test_report_retains_official_patch_and_test_group_details() -> None:
    base = _valid_bundle()
    off = base.off.model_copy(
        update={
            "patch_applied": True,
            "fail_to_pass": TestGroupReport(passed=0, total=1, failed=("TestLoad",)),
            "pass_to_pass": TestGroupReport(passed=12, total=12, failed=()),
            "log_excerpt": "TestLoad failed",
        }
    )
    bundle = base.model_copy(update={"off": off})

    parsed = ReportBundle.model_validate_json(bundle.model_dump_json(), strict=True)
    report = render_report(parsed)

    assert parsed.off.patch_applied is True
    assert parsed.off.fail_to_pass.failed == ("TestLoad",)
    assert "Patch applied | YES" in report
    assert "FAIL_TO_PASS | 0 / 1" in report
    assert "PASS_TO_PASS | 12 / 12" in report
    assert "TestLoad failed" in report


def test_mapping_insertion_order_does_not_change_report() -> None:
    first = _valid_bundle()
    second = first.model_copy(
        update={
            "revisions": dict(reversed(list(first.revisions.items()))),
            "configuration": dict(reversed(list(first.configuration.items()))),
        }
    )
    assert render_report(first) == render_report(second)


def test_missing_metrics_render_na_and_do_not_invent_zero() -> None:
    bundle = _valid_bundle().model_copy(
        update={
            "off": _valid_bundle().off.model_copy(update={"metrics": MetricSet()}),
            "on": _valid_bundle().on.model_copy(update={"metrics": MetricSet()}),
        }
    )
    report = render_report(bundle)
    assert "Patch bytes | N/A" in report
    assert "Input tokens | N/A" in report
    assert "Elapsed seconds | N/A" in report
    assert "Comparison unavailable" in report
    assert "comparable metrics are missing" in report


def test_cached_and_reasoning_tokens_are_reported_with_their_deltas() -> None:
    report = render_report(_valid_bundle())
    assert "| Cached input tokens | 150 |" in report
    assert "| Reasoning output tokens | 10 |" in report
    assert "| Cached input tokens delta | +20 |" in report
    assert "| Reasoning output tokens delta | +2 |" in report


def test_metrics_written_before_cached_and_reasoning_counts_still_load() -> None:
    older = {"patch_bytes": 1, "input_tokens": 2, "output_tokens": 3, "elapsed_seconds": 4.0}
    metrics = MetricSet.model_validate(older, strict=True)
    assert metrics.cached_input_tokens is None
    assert metrics.reasoning_output_tokens is None


def test_isolation_flags_round_trip_and_older_reports_load_without_them() -> None:
    bundle = _valid_bundle()
    flagged = bundle.model_copy(
        update={"on": bundle.on.model_copy(update={"isolation_flags": ("network_command", "web_search")})}
    )
    parsed = ReportBundle.model_validate_json(flagged.model_dump_json(), strict=True)
    assert parsed.on.isolation_flags == ("network_command", "web_search")
    report = render_report(parsed)
    assert "| Isolation flags | network_command, web_search |" in report
    assert "| Isolation flags | N/A |" in report

    older = bundle.model_dump(mode="json")
    del older["off"]["isolation_flags"]
    assert ReportBundle.model_validate_json(json.dumps(older), strict=True).off.isolation_flags == ()


def test_invalid_treatment_is_not_scored_as_failure() -> None:
    bundle = _valid_bundle().model_copy(
        update={
            "on": ArmReport(
                arm="on",
                state=ArmState.INVALID_TREATMENT,
                resolved=True,
                passed=None,
                treatment_valid=False,
                invalid_reason="PowerContext prompt hook evidence is missing",
            )
        }
    )
    report = render_report(bundle)
    assert "Treatment validity | INVALID" in report
    assert "Pass status | N/A" in report
    assert "Comparison unavailable" in report
    assert "both arms must have validated treatment" in report
    assert "PowerContext prompt hook evidence is missing" in report


@pytest.mark.parametrize(
    ("off_state", "on_state"),
    [
        (ArmState.REPORTED, ArmState.REPORTED),
        (ArmState.TREATMENT_VALIDATED, ArmState.REPORTED),
        (ArmState.REPORTED, ArmState.TREATMENT_VALIDATED),
    ],
)
def test_reported_and_treatment_validated_arms_remain_comparable(off_state: ArmState, on_state: ArmState) -> None:
    base = _valid_bundle()
    bundle = base.model_copy(
        update={
            "off": base.off.model_copy(update={"state": off_state}),
            "on": base.on.model_copy(update={"state": on_state}),
        }
    )
    report = render_report(bundle)
    assert "Pass delta | +1" in report
    assert "Comparison unavailable" not in report


def test_infrastructure_failure_and_table_text_are_normalized() -> None:
    bundle = _valid_bundle().model_copy(
        update={
            "off": ArmReport(
                arm="off",
                state=ArmState.INFRASTRUCTURE_ERROR,
                resolved=False,
                passed=None,
                treatment_valid=False,
                failure_status="proxy |\n unavailable",
            )
        }
    )
    report = render_report(bundle)
    assert "proxy \\| unavailable" in report
    assert "Resolution status | UNRESOLVED" in report
    assert "Comparison unavailable" in report


def test_renderer_rejects_unknown_or_sensitive_fields() -> None:
    data = _valid_bundle().model_dump()
    data["environment"] = {"TOKEN": "secret"}
    with pytest.raises(ValidationError):
        ReportBundle.model_validate(data)

    arm = _valid_bundle().off.model_dump()
    arm["api_key"] = "secret"
    with pytest.raises(ValidationError):
        ArmReport.model_validate(arm)

    data = _valid_bundle().model_dump()
    data["configuration"]["api_token"] = "secret"
    with pytest.raises(ValidationError):
        ReportBundle.model_validate(data)


@pytest.mark.parametrize(
    "injected",
    [
        {"configuration": {"api_token": "do-not-leak-value"}},
        {"off": {"api_key": "do-not-leak-value"}},
        {"off": {"passed": "do-not-leak-value"}},
    ],
)
def test_render_revalidates_model_copy_updates_without_leaking_values(injected: dict[str, object]) -> None:
    bundle = _valid_bundle().model_copy(update=injected)
    with pytest.raises(InvalidReportBundle) as caught:
        render_report(bundle)
    assert "do-not-leak-value" not in str(caught.value)


def test_render_rejects_nested_model_copy_extra_without_leaking_value() -> None:
    base = _valid_bundle()
    injected_arm = base.off.model_copy(update={"api_key": "nested-do-not-leak"})
    bundle = base.model_copy(update={"off": injected_arm})
    with pytest.raises(InvalidReportBundle) as caught:
        render_report(bundle)
    assert "nested-do-not-leak" not in str(caught.value)


@pytest.mark.parametrize(
    "sensitive_key",
    [
        "apiKey",
        "APIKey",
        "accessToken",
        "authToken",
        "clientSecret",
        "refresh_token",
        "Authorization",
    ],
)
def test_render_rejects_camel_case_sensitive_configuration_without_leaking_value(sensitive_key: str) -> None:
    secret_value = "camel-case-do-not-leak"
    bundle = _valid_bundle().model_copy(update={"configuration": {sensitive_key: secret_value}})
    with pytest.raises(InvalidReportBundle) as caught:
        render_report(bundle)
    assert secret_value not in str(caught.value)
    assert secret_value not in render_report(_valid_bundle())


def test_similar_non_sensitive_configuration_names_remain_reportable() -> None:
    bundle = _valid_bundle().model_copy(
        update={"configuration": {"tokenizer_version": "v2", "environmental_mode": "controlled"}}
    )
    report = render_report(bundle)
    assert "tokenizer_version" in report
    assert "environmental_mode" in report


@pytest.mark.parametrize("sensitive_key", ["ａｐｉ＿ｋｅｙ", "ａｃｃｅｓｓＴｏｋｅｎ", "tоken", "seсret"])
def test_render_rejects_nfkc_or_non_ascii_sensitive_keys_without_leaking_value(sensitive_key: str) -> None:
    secret_value = "unicode-do-not-leak"
    bundle = _valid_bundle().model_copy(update={"configuration": {sensitive_key: secret_value}})
    with pytest.raises(InvalidReportBundle) as caught:
        render_report(bundle)
    assert secret_value not in str(caught.value)
    assert secret_value not in render_report(_valid_bundle())


def test_renderer_has_no_process_network_time_or_filesystem_side_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("renderer attempted an external side effect")

    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("socket.create_connection", forbidden)
    monkeypatch.setattr("time.time", forbidden)
    monkeypatch.chdir(tmp_path)
    before = list(tmp_path.iterdir())
    render_report(_valid_bundle())
    assert list(tmp_path.iterdir()) == before


def test_float_format_and_trailing_newline_are_stable() -> None:
    report = render_report(_valid_bundle())
    assert "Elapsed seconds | 12.25" in report
    assert report.endswith("\n")
