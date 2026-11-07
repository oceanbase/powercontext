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

"""Prepare one approved operation input consistently for budgeting and generation."""

from __future__ import annotations

from dataclasses import replace

from pydantic import BaseModel, ValidationError

from powercontext.builtin.artifacts.prompt.service import current_prompt
from powercontext.builtin.inference.errors import InferencePrivacyError
from powercontext.builtin.inference.input_encoding import INPUT_ENCODING_SCHEMA, InputEncodingMode, encode_input
from powercontext.builtin.inference.privacy import PrivacyPolicy
from powercontext.builtin.inference.tokens import TokenEstimator
from powercontext.builtin.inference.usage import record_input_encoding


def render_input(
    value: BaseModel,
    *,
    stage: str,
    mode: InputEncodingMode,
    estimator: TokenEstimator,
    policy: PrivacyPolicy,
    observe: bool = False,
    schema: str = INPUT_ENCODING_SCHEMA,
) -> str:
    """Apply prompt compatibility, privacy prechecks and representation fallback.

    Only actual generation records an encoding report. Budget probes select the
    same representation without reporting another model invocation. The final
    provider request still needs its complete transport-boundary admission.
    """

    original = value.model_dump_json()
    # Typed encodings can deepen JSON or change literal key/value sequences.
    # Check the original decoded scalars as well as its raw serialization.
    if policy.enforcing:
        original_check = policy.inspect_structured_input(original)
        if original_check.decision != "allow":
            raise InferencePrivacyError("before_send", original_check.decision, original_check.reason_codes)
    selected = current_prompt(stage)
    encoded = encode_input(
        original,
        stage=stage,
        schema=schema,
        mode=mode,
        estimator=estimator,
        allowed=selected is None or selected.selection != "artifact",
    )
    if encoded.report.reason in {"applied", "shadow_candidate"}:
        try:
            valid = type(value).model_validate_json(original).model_dump_json() == original
        except ValidationError:
            valid = False
        if not valid:
            encoded = replace(
                encoded,
                prompt=original,
                report=replace(
                    encoded.report,
                    reason="roundtrip_failed",
                    selected_bytes=encoded.report.original_bytes,
                    selected_estimated_tokens=encoded.report.original_estimated_tokens,
                ),
            )
    if policy.enforcing and encoded.prompt != original:
        candidate_check = policy.inspect_structured_input(encoded.prompt)
        if candidate_check.decision != "allow":
            if (
                candidate_check.decision != "withhold"
                or not candidate_check.reason_codes
                or any(
                    reason not in {"payload_too_large", "json_invalid_or_too_deep"}
                    for reason in candidate_check.reason_codes
                )
            ):
                # A newly exposed prohibited value is an enforcement failure,
                # never evidence that the encoding merely exceeded a limit.
                raise InferencePrivacyError("before_send", candidate_check.decision, candidate_check.reason_codes)
            # The original content passed above. It still needs full wire
            # admission; a representation fallback is never a safety bypass.
            encoded = replace(
                encoded,
                prompt=original,
                report=replace(
                    encoded.report,
                    reason="privacy_profile_unsupported",
                    selected_bytes=encoded.report.original_bytes,
                    selected_estimated_tokens=encoded.report.original_estimated_tokens,
                ),
            )
    if observe:
        record_input_encoding(encoded.report)
    return encoded.prompt
