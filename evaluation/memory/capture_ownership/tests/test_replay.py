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

import asyncio
import json

import pytest

from powercontext_eval_capture_ownership.host import repository_root
from powercontext_eval_capture_ownership.models import Manifest, RunConfig, read_json
from powercontext_eval_capture_ownership.replay import freeze, run_experiment, verify_lock
from powercontext_eval_capture_ownership.report import summarize


def test_suspended_response_cannot_bypass_the_per_extraction_request_budget(manifest, tmp_path, monkeypatch):
    pytest.importorskip("powercontext.server.factory")
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    from powercontext_eval_capture_ownership import replay

    calls = 0

    async def respond(_messages, _info):
        nonlocal calls
        calls += 1
        if calls % 2:
            return ModelResponse(parts=[], state="suspended")
        return ModelResponse(parts=[TextPart('{"candidates":[]}')])

    async def model(*_args):
        return FunctionModel(respond)

    monkeypatch.setattr(replay, "_model", model)
    manifest = manifest.model_copy(update={"cases": manifest.cases[:1]})
    root = repository_root()
    config = RunConfig(schema_version="capture-ownership.config.v1", max_requests=1)
    run = asyncio.run(run_experiment(root, manifest, freeze(root, manifest, config), tmp_path / "run", mode="offline"))
    assert calls == 4
    assert [row["status"] for row in run["rows"]] == [
        "generation_failed",
        "completed",
        "generation_failed",
        "completed",
    ]


@pytest.mark.parametrize(
    "usage_details",
    [
        pytest.param({}, id="basic-usage"),
        pytest.param(
            {"cache_read_tokens": 17, "cache_write_tokens": 3, "details": {"reasoning_tokens": 2}},
            id="extended-usage",
        ),
    ],
)
def test_failed_candidate_keeps_known_usage_without_leaking_it_into_the_next_failure(
    manifest, tmp_path, monkeypatch, usage_details
):
    pytest.importorskip("powercontext.server.factory")
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel
    from pydantic_ai.usage import RequestUsage

    from powercontext_eval_capture_ownership import replay

    calls = 0

    async def respond(_messages, _info):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("No provider response is available")
        candidates = (
            []
            if calls == 3
            else [
                {
                    "kind": "fact",
                    "text": "Synthetic fact",
                    "description": "Synthetic fact summary",
                    "evidence_ids": ["missing"],
                }
            ]
        )
        return ModelResponse(
            parts=[TextPart(json.dumps({"candidates": candidates}))],
            usage=RequestUsage(input_tokens=100 + calls, output_tokens=5 + 2 * calls, **usage_details),
        )

    async def model(*_args):
        return FunctionModel(respond)

    # Exercise live accounting with a local FunctionModel; no provider requests are made.
    monkeypatch.setattr(replay, "_model", model)
    manifest = manifest.model_copy(update={"cases": manifest.cases[:1]})
    root = repository_root()
    config = RunConfig(
        schema_version="capture-ownership.config.v1", input_cny_per_million=0.15, output_cny_per_million=1.5
    )
    lock = freeze(root, manifest, config)
    run = asyncio.run(run_experiment(root, manifest, lock, tmp_path / "run", mode="live"))
    assert calls == 4
    assert [row["status"] for row in run["rows"]] == [
        "generation_failed",
        "generation_failed",
        "completed",
        "generation_failed",
    ]
    assert run["rows"][0]["provider_usage"] == {"requests": 1, "input_tokens": 101, "output_tokens": 7}
    assert run["rows"][1]["provider_usage"] is None
    assert run["rows"][2]["provider_usage"] == {"requests": 1, "input_tokens": 103, "output_tokens": 11}
    assert run["rows"][3]["provider_usage"] == {"requests": 1, "input_tokens": 104, "output_tokens": 13}
    saved = read_json(tmp_path / "run/run.json")
    assert saved == run
    totals = summarize(manifest, lock, run)["totals_by_arm"]
    joined = totals["joined-source-v1"]
    assert joined["provider_input_tokens"] == 204
    assert joined["provider_output_tokens"] == 18
    assert joined["known_usage_cost_cny"] == pytest.approx((204 * 0.15 + 18 * 1.5) / 1_000_000)
    candidate = totals["host-turn-candidate-v1"]
    assert candidate["provider_input_tokens"] is None
    assert candidate["known_provider_input_tokens"] == 104
    assert candidate["known_provider_output_tokens"] == 13
    assert candidate["known_usage_cost_cny"] == pytest.approx((104 * 0.15 + 13 * 1.5) / 1_000_000)
    assert candidate["provider_usage_complete"] is False


def test_real_http_capture_readback_pipeline_and_metadata_isolation(manifest, tmp_path):
    pytest.importorskip("powercontext.server.factory")
    root = repository_root()
    config = RunConfig(schema_version="capture-ownership.config.v1")
    lock = freeze(root, manifest, config)
    run = asyncio.run(run_experiment(root, manifest, lock, tmp_path / "private", mode="offline"))
    assert len(run["rows"]) == len(manifest.cases) * 4
    assert all(row["status"] in {"completed", "skipped"} for row in run["rows"])
    assert all(row["parser_matches_ground_truth"] for row in run["rows"])
    cases = {case.case_id: case for case in manifest.cases}
    for row in run["rows"]:
        assert row["candidates"] == []
        assert row["materialization_path"] == "ContentSource"
        if row["arm"] == "joined-source-v1":
            evidence = row["input"]["evidence"][0]
            assert evidence["content"]["content"] == cases[row["case_id"]].prompt
            # Runtime compaction retains the body once and removes its metadata copy.
            assert "content" not in evidence["source_metadata"]
            assert row["occupancy"]["text_carriers"] == 1
        else:
            assert "source_metadata" in row["input"]["evidence"][0]
            assert "content" not in row["input"]["evidence"][0]["source_metadata"]
            assert row["occupancy"]["labeled_utf8_bytes"].get("history", 0) == 0
            assert row["occupancy"]["labeled_utf8_bytes"].get("prepared_context", 0) == 0
    saved = read_json(tmp_path / "private/run.json")
    assert saved == run
    assert summarize(manifest, lock, run)["decision"] == "insufficient_evidence"


def test_changed_manifest_is_rejected_before_replay(manifest):
    pytest.importorskip("powercontext.server.factory")
    root = repository_root()
    config = RunConfig(schema_version="capture-ownership.config.v1")
    lock = freeze(root, manifest, config)
    changed = Manifest.model_validate(
        manifest.model_dump(mode="json") | {"cases": [manifest.cases[0].model_dump(mode="json")]}
    )
    with pytest.raises(ValueError, match="differs"):
        verify_lock(root, changed, lock)
