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

"""Pure deterministic Markdown report rendering."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticSerializationError

from powercontext_eval.artifacts import ArmState
from powercontext_eval.benchmarks.swebench_pro.gold_overrides import (
    OFFICIAL_EVALUATION_DIRECT,
    OFFICIAL_EVALUATION_DOCKER_PROXY,
    OFFICIAL_EVALUATION_PROXY_BYPASSED,
    SOURCE559_DATASET_PATCH_SHA256,
    SOURCE559_INSTANCE_ID,
    SOURCE559_REFERENCE_DATASET,
    SOURCE559_REFERENCE_FILE_OID,
    SOURCE559_REFERENCE_PATCH_SHA256,
    SOURCE559_REFERENCE_REVISION,
    SOURCE595_DATASET_PATCH_SHA256,
    SOURCE595_INSTANCE_ID,
)


class MetricSet(BaseModel):
    """Comparable measurements captured from one arm."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    patch_bytes: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    elapsed_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class TestGroupReport(BaseModel):
    """Serializable official result for one required test group."""

    __test__ = False
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    passed: int = Field(ge=0)
    total: int = Field(ge=0)
    failed: tuple[str, ...] = ()

    @field_validator("failed", mode="before")
    @classmethod
    def parse_serialized_failed_names(cls, value: object) -> object:
        if isinstance(value, list):
            return tuple(value)
        return value

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.passed > self.total or len(self.failed) != self.total - self.passed:
            raise ValueError("Official test group counts do not match failed names")
        if any(not name for name in self.failed) or len(set(self.failed)) != len(self.failed):
            raise ValueError("Official failed test names must be non-blank and unique")
        return self


class ArmReport(BaseModel):
    """Audited report input for one fixed treatment arm."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    arm: Literal["off", "on"]
    state: ArmState
    resolved: bool
    passed: bool | None
    treatment_valid: bool
    patch_applied: bool | None = None
    fail_to_pass: TestGroupReport = Field(default_factory=lambda: TestGroupReport(passed=0, total=0))
    pass_to_pass: TestGroupReport = Field(default_factory=lambda: TestGroupReport(passed=0, total=0))
    log_excerpt: str | None = Field(default=None, max_length=4_000)
    metrics: MetricSet = Field(default_factory=MetricSet)
    failure_status: str | None = None
    invalid_reason: str | None = None

    @field_validator("state", mode="before")
    @classmethod
    def parse_serialized_state(cls, value: object) -> object:
        """Accept the exact enum value emitted by JSON serialization."""

        if isinstance(value, str):
            return ArmState(value)
        return value


class GoldValidationAudit(BaseModel):
    """Independent provenance record for the Gold validation patch."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    instance_id: str = Field(min_length=1, max_length=300)
    mode: Literal["dataset_patch", "verified_override"]
    dataset_patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    validation_patch_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dataset_patch_status: Literal["unverified", "known_failed"]
    reference_validation_status: Literal["not_applicable", "passed"]
    attempt_gold_validation_status: Literal["pending", "passed", "failed"]
    official_evaluation_transport: Literal["direct", "docker_proxy", "proxy_bypassed_for_test_isolation"] = (
        OFFICIAL_EVALUATION_DOCKER_PROXY
    )
    official_evaluation_test_selection: Literal[
        "dataset_selected_files",
        "required_unit_tests_only_for_invalid_integration_target",
    ] = "dataset_selected_files"
    source_dataset: str | None = None
    source_revision: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    source_file_oid: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    source_kind: Literal["verified_reference_submission"] | None = None

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        if self.mode == "dataset_patch":
            if self.official_evaluation_transport not in {
                OFFICIAL_EVALUATION_DIRECT,
                OFFICIAL_EVALUATION_DOCKER_PROXY,
            }:
                raise ValueError("Dataset Gold validation transport is invalid")
            if self.validation_patch_sha256 != self.dataset_patch_sha256:
                raise ValueError("Dataset Gold validation must use the dataset patch")
            if self.dataset_patch_status != "unverified" or self.reference_validation_status != "not_applicable":
                raise ValueError("Dataset Gold validation provenance is invalid")
            if any(
                value is not None
                for value in (self.source_dataset, self.source_revision, self.source_file_oid, self.source_kind)
            ):
                raise ValueError("Dataset Gold validation cannot contain reference provenance")
            if self.instance_id == SOURCE595_INSTANCE_ID:
                if (
                    self.dataset_patch_sha256 != SOURCE595_DATASET_PATCH_SHA256
                    or self.official_evaluation_test_selection
                    != "required_unit_tests_only_for_invalid_integration_target"
                ):
                    raise ValueError("source595 requires the pinned official test-selection correction")
            elif self.official_evaluation_test_selection != "dataset_selected_files":
                raise ValueError("Official test-selection correction is not valid for this instance")
        else:
            if self.official_evaluation_transport not in {
                OFFICIAL_EVALUATION_DIRECT,
                OFFICIAL_EVALUATION_PROXY_BYPASSED,
            }:
                raise ValueError("Gold override validation requires the audited proxy bypass")
            if self.official_evaluation_test_selection != "dataset_selected_files":
                raise ValueError("Gold override validation must retain the dataset test selection")
            if self.dataset_patch_status != "known_failed" or self.reference_validation_status != "passed":
                raise ValueError("Gold override validation provenance is invalid")
            if not all(
                value is not None
                for value in (self.source_dataset, self.source_revision, self.source_file_oid, self.source_kind)
            ):
                raise ValueError("Gold override validation requires complete reference provenance")
            if (
                self.dataset_patch_sha256 != SOURCE559_DATASET_PATCH_SHA256
                or self.validation_patch_sha256 != SOURCE559_REFERENCE_PATCH_SHA256
                or self.source_dataset != SOURCE559_REFERENCE_DATASET
                or self.source_revision != SOURCE559_REFERENCE_REVISION
                or self.source_file_oid != SOURCE559_REFERENCE_FILE_OID
            ):
                raise ValueError("Gold override provenance does not match the pinned reference")
        return self


class ReportBundle(BaseModel):
    """Complete, side-effect-free input to report rendering."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    title: str
    revisions: Mapping[str, str]
    configuration: Mapping[str, str]
    off: ArmReport
    on: ArmReport
    gold_validation: GoldValidationAudit | None = None

    @model_validator(mode="after")
    def validate_gold_binding(self) -> Self:
        instance_id = self.configuration.get("instance")
        audit = self.gold_validation
        if audit is not None and audit.attempt_gold_validation_status != "passed":
            raise ValueError("Final reports require successful Gold validation")
        if instance_id == SOURCE559_INSTANCE_ID:
            if (
                audit is None
                or audit.instance_id != instance_id
                or audit.mode != "verified_override"
                or audit.attempt_gold_validation_status != "passed"
            ):
                raise ValueError("source559 reports require the verified Gold override audit")
        elif audit is not None and (audit.instance_id != instance_id or audit.mode == "verified_override"):
            raise ValueError("Gold validation audit is not bound to the report instance")
        return self

    @field_validator("revisions", "configuration")
    @classmethod
    def reject_sensitive_mapping_keys(cls, values: Mapping[str, str]) -> Mapping[str, str]:
        """Keep credential-shaped fields and environment dumps out of retained reports."""

        forbidden_words = {
            "api_key",
            "authorization",
            "cookie",
            "credential",
            "credentials",
            "env",
            "environment",
            "passwd",
            "password",
            "secret",
            "token",
        }
        forbidden_compounds = {
            "accesskey",
            "accesstoken",
            "apikey",
            "authkey",
            "authtoken",
            "clientsecret",
            "privatekey",
            "refreshtoken",
            "secretkey",
        }
        normalized_values: dict[str, str] = {}
        for original_key, value in values.items():
            key = unicodedata.normalize("NFKC", original_key)
            if not key.isascii():
                raise ValueError("Report mapping keys must contain only ASCII characters")
            if key in normalized_values:
                raise ValueError("Report mapping keys collide after normalization")
            words = [
                word.casefold()
                for chunk in re.split(r"[^A-Za-z0-9]+", key)
                for word in re.findall(r"[A-Z]+(?=[A-Z][a-z]|[0-9]|$)|[A-Z]?[a-z]+|[0-9]+", chunk)
            ]
            collapsed = "".join(words)
            adjacent_compounds = {words[index] + words[index + 1] for index in range(len(words) - 1)}
            if (
                set(words) & forbidden_words
                or collapsed in forbidden_compounds
                or adjacent_compounds & forbidden_compounds
            ):
                raise ValueError("Report mapping contains a forbidden field name")
            normalized_values[key] = value
        return normalized_values


class InvalidReportBundle(ValueError):
    """A report bundle failed safe boundary revalidation."""


def _cell(value: object | None) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "N/A"
        rendered = format(value, ".12g")
    else:
        rendered = str(value)
    return " ".join(rendered.split()).replace("|", "\\|")


def _status(value: bool | None, true: str, false: str) -> str:
    if value is None:
        return "N/A"
    return true if value else false


def _mapping_table(title: str, values: Mapping[str, str]) -> list[str]:
    lines = [f"## {title}", "", "| Key | Value |", "| --- | --- |"]
    lines.extend(f"| {_cell(key)} | {_cell(values[key])} |" for key in sorted(values))
    if not values:
        lines.append("| N/A | N/A |")
    lines.append("")
    return lines


def _arm_section(label: str, arm: ArmReport) -> list[str]:
    metrics = arm.metrics
    details = arm.failure_status or arm.invalid_reason
    return [
        f"## PowerContext {label}",
        "",
        "| Field | Value |",
        "| --- | --- |",
        f"| Resolution status | {_status(arm.resolved, 'RESOLVED', 'UNRESOLVED')} |",
        f"| Patch applied | {_status(arm.patch_applied, 'YES', 'NO')} |",
        f"| FAIL_TO_PASS | {arm.fail_to_pass.passed} / {arm.fail_to_pass.total} |",
        f"| PASS_TO_PASS | {arm.pass_to_pass.passed} / {arm.pass_to_pass.total} |",
        f"| Failed official tests | {_cell(', '.join((*arm.fail_to_pass.failed, *arm.pass_to_pass.failed)) or None)} |",
        f"| Official log excerpt | {_cell(arm.log_excerpt)} |",
        f"| Lifecycle state | {_cell(arm.state.value)} |",
        f"| Pass status | {_status(arm.passed, 'PASS', 'FAIL')} |",
        f"| Treatment validity | {_status(arm.treatment_valid, 'VALID', 'INVALID')} |",
        f"| Patch bytes | {_cell(metrics.patch_bytes)} |",
        f"| Input tokens | {_cell(metrics.input_tokens)} |",
        f"| Output tokens | {_cell(metrics.output_tokens)} |",
        f"| Elapsed seconds | {_cell(metrics.elapsed_seconds)} |",
        f"| Failure or invalid reason | {_cell(details)} |",
        "",
    ]


def _gold_validation_section(audit: GoldValidationAudit) -> list[str]:
    values = audit.model_dump(mode="python")
    lines = ["## Gold validation audit", "", "| Field | Value |", "| --- | --- |"]
    lines.extend(f"| {_cell(key)} | {_cell(value)} |" for key, value in values.items())
    lines.append("")
    return lines


def _comparison(bundle: ReportBundle) -> list[str]:
    lines = ["## Comparison", ""]
    comparable_states = {ArmState.TREATMENT_VALIDATED, ArmState.REPORTED}
    if not (
        bundle.off.state in comparable_states
        and bundle.on.state in comparable_states
        and bundle.off.treatment_valid
        and bundle.on.treatment_valid
    ):
        return lines + ["Comparison unavailable: both arms must have validated treatment.", ""]

    off_metrics = bundle.off.metrics
    on_metrics = bundle.on.metrics
    comparable = (
        ("Patch bytes delta", off_metrics.patch_bytes, on_metrics.patch_bytes),
        ("Input tokens delta", off_metrics.input_tokens, on_metrics.input_tokens),
        ("Output tokens delta", off_metrics.output_tokens, on_metrics.output_tokens),
        ("Elapsed seconds delta", off_metrics.elapsed_seconds, on_metrics.elapsed_seconds),
    )
    available = [(name, off, on) for name, off, on in comparable if off is not None and on is not None]
    if bundle.off.passed is None or bundle.on.passed is None or not available:
        return lines + ["Comparison unavailable: comparable metrics are missing.", ""]

    lines.extend(["| Metric | ON minus OFF |", "| --- | --- |"])
    pass_delta = int(bundle.on.passed) - int(bundle.off.passed)
    lines.append(f"| Pass delta | {pass_delta:+d} |")
    for name, off_value, on_value in available:
        assert off_value is not None and on_value is not None
        delta = on_value - off_value
        rendered = f"{delta:+.12g}" if isinstance(delta, float) else f"{delta:+d}"
        lines.append(f"| {name} | {rendered} |")
    lines.append("")
    return lines


def _validated_bundle(bundle: ReportBundle) -> ReportBundle:
    """Revalidate copied or mutated model contents without reflecting rejected values."""

    try:
        if type(bundle) is not ReportBundle:
            raise TypeError
        expected_bundle_fields = set(ReportBundle.model_fields)
        if set(bundle.__dict__) != expected_bundle_fields:
            raise ValueError
        for model, model_type in (
            (bundle.off, ArmReport),
            (bundle.on, ArmReport),
        ):
            if type(model) is not model_type or set(model.__dict__) != set(model_type.model_fields):
                raise ValueError
            if type(model.metrics) is not MetricSet or set(model.metrics.__dict__) != set(MetricSet.model_fields):
                raise ValueError
            for group in (model.fail_to_pass, model.pass_to_pass):
                if type(group) is not TestGroupReport or set(group.__dict__) != set(TestGroupReport.model_fields):
                    raise ValueError
        if bundle.gold_validation is not None and (
            type(bundle.gold_validation) is not GoldValidationAudit
            or set(bundle.gold_validation.__dict__) != set(GoldValidationAudit.model_fields)
        ):
            raise ValueError
        serialized = bundle.model_dump(mode="python", round_trip=True, warnings="none")
        return ReportBundle.model_validate(serialized, strict=True)
    except (AttributeError, PydanticSerializationError, TypeError, ValueError):
        raise InvalidReportBundle("Report bundle failed strict validation") from None


def render_report(bundle: ReportBundle) -> str:
    """Render only the supplied validated bundle, with no external reads."""

    bundle = _validated_bundle(bundle)
    if bundle.off.arm != "off" or bundle.on.arm != "on":
        raise ValueError("Report arms must be supplied in OFF then ON roles")
    lines = [f"# {_cell(bundle.title)}", ""]
    lines.extend(_mapping_table("Resolved revisions", bundle.revisions))
    lines.extend(_mapping_table("Configuration", bundle.configuration))
    if bundle.gold_validation is not None:
        lines.extend(_gold_validation_section(bundle.gold_validation))
    lines.extend(_arm_section("OFF", bundle.off))
    lines.extend(_arm_section("ON", bundle.on))
    lines.extend(_comparison(bundle))
    return "\n".join(lines)
