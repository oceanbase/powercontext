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

"""Replay the captured v12 response through the new SDK/Pipeline without provider access."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
from powercontext.builtin.inference import InferenceError

from ..manifests import EvaluationError, file_hash, read_json
from ..models import digest
from ..v7.workflow import write_result
from ..v9_preflight.adapter import Config
from .adapter import ResumableJournal, extract
from .freeze import ROOT, identity
from .session import call_request, load_session, prepare_session


async def replay(baseline_dir, historical_session, output_dir):
    if output_dir.exists():
        raise EvaluationError("v13_replay_output_exists")
    frozen = identity()
    history = read_json(ROOT.parents[2] / "results/v12-live/summary.json")
    old_plan = read_json(historical_session / "plan.json")
    old_private = Path(old_plan["private_root"])
    rows = [json.loads(line) for line in (old_private / "audit/attempts.jsonl").read_bytes().splitlines()]
    old = next(row for row in rows if row["kind"] == "terminal")
    old_start = next(row for row in rows if row["kind"] == "start" and row["attempt_id"] == old["attempt_id"])
    response_path = old_private / "audit" / f"{old['attempt_id']}.response.bin"
    if (
        not old.get("response_capture_complete")
        or file_hash(response_path) != history["raw_response_hash"]
        or old["response_hash"] != history["raw_response_hash"]
    ):
        raise EvaluationError("v13_historical_response_changed")
    await prepare_session(baseline_dir, output_dir / "session")
    public, data, private = load_session(output_dir / "session")
    call = data["calls"][0]
    if call["request_hash"] != old_start["input_hash"]:
        raise EvaluationError("v13_historical_prepared_input_changed")
    observed_requests = []

    def handler(request):
        observed_requests.append(json.loads(request.content))
        return httpx.Response(200, content=response_path.read_bytes(), headers={"content-type": "application/json"})

    try:
        await extract(
            call_request(call),
            Config(**public["provider"]),
            ResumableJournal(private / "audit"),
            httpx.MockTransport(handler),
            api_key="offline-key",
            sample_id=call["sample_id"],
        )
    except (InferenceError, EvaluationError):
        pass
    else:
        raise EvaluationError("v13_historical_failure_unexpected_success")
    events = [json.loads(line) for line in (private / "audit/attempts.jsonl").read_bytes().splitlines()]
    terminal = next(row for row in events if row["kind"] == "terminal")
    request_event = next(row for row in events if row["kind"] == "http_request")
    keys = (
        "failure_stage",
        "error_category",
        "provider_finish_reason",
        "input_tokens",
        "output_tokens",
        "response_hash",
    )
    if (
        len(observed_requests) != 1
        or any(terminal.get(key) != old.get(key) for key in keys)
        or not terminal["pipeline_entered"]
        or terminal["pipeline_returned_successfully"]
        or not request_event["thinking_disabled_verified"]
        or identity() != frozen
    ):
        raise EvaluationError("v13_failure_replay_changed")
    result = {
        "schema": "evidence-admission.v13.recorded-response-replay.v1",
        "identity": frozen,
        "historical_v12_summary_sha256": frozen["contract"]["historical_v12_result_sha256"],
        "request_hash": call["request_hash"],
        "http_request_hash": request_event["http_request_hash"],
        "historical_raw_response_hash": history["raw_response_hash"],
        **{key: terminal.get(key) for key in keys},
        "pipeline_entered": True,
        "pipeline_returned_successfully": False,
        "thinking_disabled_verified": True,
        "failure_reproduced": True,
        "provider_requests": 0,
        "mock_http_requests": 1,
        "usage_is_historical_not_new_billing": True,
        "formal_acceptance": False,
    }
    write_result(output_dir / "result.json", result)
    write_result(output_dir / "COMPLETE.json", {"result_hash": digest(result)})
    return {"failure_reproduced": True, "provider_requests": 0, "mock_http_requests": 1}
