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

"""Transport orchestration tests using mocks; never native Agent evidence."""

import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from powercontext_datus.learning import LearningEvidence, generate_from_examples, publish_completed_usage

from powercontext.client import PowerContextClient
from powercontext.http import CaptureContentSourceResponse


def evidence():
    return LearningEvidence(
        "synthetic", "component-only question", "SELECT 1", "a" * 64, "b" * 64, "c" * 64, "component-only lesson"
    )


def test_learning_capture_source_lineage_without_implicit_approval():
    client = AsyncMock(spec=PowerContextClient)
    client.capture_content_source.return_value = CaptureContentSourceResponse.model_validate({
        "status": "accepted",
        "source": {"name": "content", "source_id": "captured"},
        "position": 1,
    })
    result = asyncio.run(generate_from_examples(client, scope_id="scope", examples=[evidence()]))
    assert result is client.generate_skill.return_value
    capture = client.capture_content_source.await_args.args[0]
    request = client.generate_skill.await_args.args[0]
    assert capture.metadata["phase"] == "independent_learning"
    assert json.loads(capture.content)["native_receipt_digest"] == "b" * 64
    assert request.origin == "source"
    assert request.source_refs == [client.capture_content_source.return_value.source]
    assert len(client.method_calls) == 2  # No approval, publish, delivery, or usage write.


def test_reject_incomplete_or_unbounded_learning_before_transport():
    client = AsyncMock(spec=PowerContextClient)
    for examples in ([], [evidence(), evidence()]):
        with pytest.raises(ValueError):
            asyncio.run(generate_from_examples(client, scope_id="scope", examples=examples))
    client.capture_content_source.assert_not_called()
    with pytest.raises(ValueError, match="SHA-256"):
        replace(evidence(), result_digest="not-a-digest")
    with pytest.raises(ValueError, match="incomplete"):
        replace(evidence(), lesson=" ")


def test_no_usage_feedback_before_both_arms_complete():
    client = AsyncMock(spec=PowerContextClient)
    with pytest.raises(ValueError, match="both arms"):
        asyncio.run(publish_completed_usage(client, [], paired_run_complete=False))
    client.record_skill_usage.assert_not_called()
    asyncio.run(publish_completed_usage(client, [], paired_run_complete=True))
    client.record_skill_usage.assert_not_called()
