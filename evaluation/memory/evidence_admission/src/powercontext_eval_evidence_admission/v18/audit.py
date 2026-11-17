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

"""Verify persisted scoring inputs against identities captured before Pipeline validation."""

from __future__ import annotations

import json

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionOutput
from pydantic import ValidationError

from ..manifests import EvaluationError, read_json
from ..models import digest


def scoring_output(directory, record):
    """Read the original parsed output; never establish a missing binding during recovery."""
    call = {key: record[key] for key in ("row_id", "input_index", "input_hash")}
    if digest(call) != record["call_id"]:
        raise EvaluationError("v18_scoring_call_binding_changed")
    expected = record.get("parsed_output_hash")
    if not expected:
        raise EvaluationError("v18_scoring_output_binding_unavailable")
    returned = record.get("outcome") == "returned" and record.get("pipeline_returned") is True
    rejected = record.get("outcome") == "output_or_inference_failed" and record.get("pipeline_returned") is False
    if not (returned or rejected):
        raise EvaluationError("v18_scoring_output_outcome_changed")
    audit = directory / "audit"
    call_id = record["call_id"]
    try:
        request = read_json(audit / (call_id + ".request.json"))
        if digest(request) != record["request_facts"]["http_request_hash"]:
            raise EvaluationError("v18_scoring_request_changed")
        if digest(json.loads(request["messages"][2]["content"])) != record["input_hash"]:
            raise EvaluationError("v18_scoring_request_input_changed")
        raw = (audit / (call_id + ".response-bytes.json")).read_bytes()
        if digest(raw.hex()) != record.get("response_hash"):
            raise EvaluationError("v18_scoring_response_changed")
        output = AtomicMemoryExtractionOutput.model_validate(read_json(audit / (call_id + ".parsed.json")))
        if digest(output.model_dump(mode="json")) != expected:
            raise EvaluationError("v18_scoring_parsed_output_changed")
        if returned and (
            record.get("output_hash") != expected or digest(read_json(audit / (call_id + ".output.json"))) != expected
        ):
            raise EvaluationError("v18_scoring_returned_output_changed")
    except (OSError, KeyError, IndexError, TypeError, ValidationError):
        raise EvaluationError("v18_scoring_audit_unavailable_or_invalid") from None
    return output
