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

import asyncio
import json
from pathlib import Path

import httpx
from powercontext.client import PowerContextClient

from powercontext_e2e.models import MemorySnapshot
from powercontext_e2e.runner import _load_capture_records, memory_snapshot


def test_memory_snapshot_retains_exact_atomic_lineage_across_pages() -> None:
    async def scenario() -> None:
        references = [
            {"family": "atomic-memory", "artifact_id": "memory-a", "revision": 2},
            {"family": "atomic-memory", "artifact_id": "memory-b", "revision": 3},
        ]
        dependency = {"family": "atomic-memory", "artifact_id": "merge-input", "revision": 1}
        sources = [{"source_type": "content", "source_id": "captured-source"}]

        def respond(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/v1/memory/entries/list":
                payload = json.loads(request.content)
                assert payload["scope_id"] == "scope-a"
                index = 0 if payload.get("cursor") is None else 1
                assert index == 0 or payload["cursor"] == "page-two"
                return httpx.Response(
                    200,
                    json={
                        "entries": [
                            {
                                "artifact": references[index],
                                "kind": "decision",
                                "text": "A grounded decision.",
                                "state": "active",
                                "state_version": index + 4,
                                "merged_into_id": None,
                            }
                        ],
                        "next_cursor": "page-two" if index == 0 else None,
                    },
                )
            index = next(
                index
                for index, reference in enumerate(references)
                if request.url.path
                == f"/v1/scopes/scope-a/artifacts/atomic-memory/{reference['artifact_id']}/revisions/{reference['revision']}"
            )
            return httpx.Response(
                200,
                json={
                    "scope_id": "scope-a",
                    **references[index],
                    "content": {
                        "schema": "powercontext.atomic-memory.v1",
                        "kind": "decision",
                        "text": "A grounded decision.",
                    },
                    "sources": sources,
                    "artifacts": [dependency],
                    "content_digest": "sha256:" + "a" * 64,
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
            client = PowerContextClient("https://memory.example", http_client=http_client)
            snapshot = await memory_snapshot(client, "scope-a")

        assert len(snapshot.entries) == 2
        assert snapshot.model_dump(mode="json") == {
            "entries": [
                {
                    "artifact": reference,
                    "kind": "decision",
                    "text": "A grounded decision.",
                    "state": "active",
                    "state_version": index + 4,
                    "merged_into_id": None,
                    "sources": [{"name": "content", "source_id": "captured-source"}],
                    "artifacts": [dependency],
                }
                for index, reference in enumerate(references)
            ]
        }
        assert MemorySnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot

    asyncio.run(scenario())


def test_memory_snapshot_keeps_legacy_entry_evidence_without_inventing_artifact_refs() -> None:
    payload = {
        "entries": [
            {
                "entry_id": "entry-a",
                "entry_version_id": "entry-version-a",
                "version": 7,
                "kind": "decision",
                "text": "Historical evidence.",
                "state": "inactive",
                "source_refs": [{"name": "content", "source_id": "source-a"}],
            }
        ]
    }

    snapshot = MemorySnapshot.model_validate(payload)

    assert snapshot.model_dump(mode="json") == payload


def test_capture_records_accept_current_atomic_checkpoint_fields(tmp_path: Path) -> None:
    checkpoint = {
        "schema": "powercontext.bub-capture-event/v1",
        "recorded_at": "2026-10-07T00:00:00Z",
        "event": "checkpoint",
        "status": "advanced",
        "final": True,
        "target_position": 2,
        "previous_cursor": 0,
        "current_cursor": 2,
        "high_watermark": 2,
        "processed_source_count": 2,
        "cursor_advanced": True,
        "remaining_work": False,
    }
    (tmp_path / "powercontext-capture.jsonl").write_text(json.dumps(checkpoint) + "\n", encoding="utf-8")

    records = _load_capture_records(tmp_path)

    assert len(records) == 1
    assert records[0].model_dump(mode="json", by_alias=True, exclude_none=True) == checkpoint
