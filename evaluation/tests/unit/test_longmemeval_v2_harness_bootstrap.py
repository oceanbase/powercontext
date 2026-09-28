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
from collections.abc import Mapping
from pathlib import Path

import pytest

from powercontext_eval.benchmarks.longmemeval_v2 import harness_bootstrap
from powercontext_eval.benchmarks.longmemeval_v2.adapter import AUDIT_SCHEMA, PowerContextMemory

_MEMORY_CONTRACT = """
MEMORY_TYPES = {}

def register_memory(memory_cls):
    MEMORY_TYPES[memory_cls.memory_type] = memory_cls
    return memory_cls

def build_memory(config):
    return MEMORY_TYPES[config["memory_type"]](config["memory_params"])
"""

_HARNESS = """
import json
import sys
from pathlib import Path

from memory_modules.memory import build_memory

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
memory = build_memory(payload["memory_config"])
memory.configure_runtime(
    query_trace_dir=Path("unused"),
    generation_temperature=None,
    generation_top_p=None,
    cancel_event=None,
)
memory.insert(payload["trajectory"])
memory.set_query_context(query_invocation_id="invocation-1")
try:
    context = memory.query(payload["question"], query_image=payload["query_image"])
    post_query = memory.post_query_hook(
        query=payload["question"],
        query_image=payload["query_image"],
        memory_context=context,
    )
finally:
    memory.clear_query_context()
Path(sys.argv[2]).write_text(
    json.dumps({"context": context, "post_query": post_query}),
    encoding="utf-8",
)
"""


class FakePowerContext:
    def __init__(self) -> None:
        self.captures: list[dict[str, object]] = []
        self.memories: list[dict[str, object]] = []
        self.searches: list[dict[str, object]] = []

    def capture_content_source(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        request = dict(payload)
        self.captures.append(request)
        return {"source": {"name": "content", "source_id": request["source_id"]}}

    def remember_memory(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        request = dict(payload)
        self.memories.append(request)
        return {
            "entry": {
                "citation": {
                    "memory_ref": {"family": "memory", "artifact_id": "memory", "revision": 1},
                    "entry_id": "entry-1",
                    "entry_version_id": "entry-1-v1",
                }
            }
        }

    def search_memory(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        self.searches.append(dict(payload))
        return {
            "hits": [
                {
                    "text": "The remembered assignment procedure.",
                    "citation": {
                        "memory_ref": {"family": "memory", "artifact_id": "memory", "revision": 1},
                        "entry_id": "entry-1",
                        "entry_version_id": "entry-1-v1",
                    },
                }
            ]
        }

    def prepare_context(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        content = "The remembered assignment procedure."
        return {
            "schema": "powercontext.prepared-context.v1",
            "status": "ready",
            "content": content,
            "content_bytes": len(content.encode()),
        }


def test_bootstrap_registers_adapter_and_runs_harness_insert_query_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    harness_root = tmp_path / "LongMemEval-V2"
    memory_modules = harness_root / "memory_modules"
    evaluation = harness_root / "evaluation"
    memory_modules.mkdir(parents=True)
    evaluation.mkdir()
    (memory_modules / "__init__.py").write_text("", encoding="utf-8")
    (memory_modules / "memory.py").write_text(_MEMORY_CONTRACT, encoding="utf-8")
    (evaluation / "harness.py").write_text(_HARNESS, encoding="utf-8")

    audit_path = tmp_path / "adapter-audit.jsonl"
    input_path = tmp_path / "input.json"
    output_path = tmp_path / "output.json"
    secret_gold = "GOLD-MUST-NOT-ENTER-ADAPTER"
    input_path.write_text(
        json.dumps(
            {
                "memory_config": {
                    "memory_type": "powercontext",
                    "memory_params": {"scope_id": "smoke-scope", "audit_path": str(audit_path)},
                },
                "trajectory": {
                    "id": "trajectory-1",
                    "domain": "enterprise",
                    "environment": "workarena",
                    "goal": "Assign the incident.",
                    "outcome": "success",
                    "start_url": "https://example.test",
                    "states": [
                        {
                            "state_index": 0,
                            "step": 0,
                            "url": "https://example.test",
                            "action": "click('assign')",
                            "thought": "Use the documented group.",
                            "accessibility_tree": "Assignment group Network",
                            "screenshot": "screenshots/0.png",
                        }
                    ],
                    "question_type": "procedure",
                    "gold_answer": secret_gold,
                    "judge": {"answer": secret_gold},
                },
                "question": "Which assignment group should be used?",
                "query_image": "question.png",
            }
        ),
        encoding="utf-8",
    )

    validated: list[Path] = []
    fake = FakePowerContext()
    monkeypatch.setattr(harness_bootstrap, "validate_harness_checkout", lambda root: validated.append(root))
    monkeypatch.setattr(PowerContextMemory, "_http_runtime", lambda self: fake)

    harness_bootstrap.run_pinned_harness(harness_root, [str(input_path), str(output_path)])

    assert validated == [harness_root.resolve()]
    assert len(fake.captures) == 1
    assert len(fake.memories) == 1
    assert fake.searches == [
        {"scope_id": "smoke-scope", "query": "Which assignment group should be used?", "limit": 10, "mode": "auto"}
    ]
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert output["context"] == [{"type": "text", "value": "The remembered assignment procedure."}]
    assert output["post_query"]["query_invocation_id"] == "invocation-1"
    assert output["post_query"]["result_count"] == 1
    assert output["post_query"]["citations"][0]["entry_id"] == "entry-1"
    audit_text = audit_path.read_text(encoding="utf-8")
    audit = [json.loads(line) for line in audit_text.splitlines()]
    assert [event["operation"] for event in audit] == ["ingest", "query"]
    assert all(event["schema"] == AUDIT_SCHEMA for event in audit)
    assert audit[1]["query_invocation_id"] == "invocation-1"
    assert secret_gold not in audit_text
    assert "question_type" not in audit_text
    assert "judge" not in audit_text
