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

from typing import Any

from powercontext_eval.benchmarks.longmemeval_v2.prepare_worker import _prepare_record


class FakeHarness:
    def validate_memory_context_items(self, memory_context: Any, *, question_id: str) -> list[dict[str, str]]:
        assert question_id == "question-1"
        assert isinstance(memory_context, list)
        return memory_context

    def truncate_memory_context(
        self,
        memory_context: list[dict[str, str]],
        *,
        max_tokens: int,
        question_id: str,
    ) -> tuple[list[dict[str, str]], int, int]:
        assert max_tokens == 20
        assert question_id == "question-1"
        return memory_context[:1], 30, 10

    def get_system_prompt(self, domain: str) -> str:
        assert domain == "enterprise"
        return "system"

    def build_messages(
        self,
        *,
        system_prompt: str,
        question_text: str,
        image_path: str | None,
        memory_context: list[dict[str, str]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        assert system_prompt == "system"
        assert question_text == "Where is the evidence?"
        assert image_path is None
        assert memory_context == [{"type": "text", "value": "memory-a"}]
        messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": question_text}]
        return messages, messages


def test_prepare_record_uses_harness_budget_and_emits_replayable_prompt() -> None:
    record = {
        "question_id": "question-1",
        "domain": "enterprise",
        "question": {"text": "Where is the evidence?", "image": None},
        "scope_id": "scope-1",
        "haystack_digest": "digest-1",
        "gold_answer": "must-not-be-copied",
        "question_type": "must-not-be-copied",
        "judge": {"must": "not-be-copied"},
        "memory_context": [
            {"type": "text", "value": "memory-a"},
            {"type": "text", "value": "memory-b"},
        ],
    }

    prepared = _prepare_record(FakeHarness(), record, sequence=1, memory_context_max_tokens=20)

    assert prepared["memory_context"] == [{"type": "text", "value": "memory-a"}]
    assert prepared["memory_context_original_tokens"] == 30
    assert prepared["memory_context_tokens"] == 10
    assert prepared["memory_context_was_truncated"] is True
    assert prepared["memory_context_max_tokens"] == 20
    assert prepared["prompt_sha256"]
    assert prepared["messages"] == prepared["prompt_messages"]
    assert not {"answer", "gold_answer", "question_type", "judge", "scorer", "eval_function"} & set(prepared)
