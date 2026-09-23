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

"""Public response fixtures for native plugin lifecycle tests."""

from powercontext.http import ScopedStats


def _empty_inventory() -> dict[str, object]:
    return {
        "sources": {"total": 0, "memory_processed": 0, "memory_pending": 0},
        "artifacts": {"total": 0, "by_family": []},
        "candidates": {"total": 0, "pending": 0, "approved": 0, "rejected": 0, "by_family": []},
        "memory": {"entries": {"total": 0, "active": 0, "inactive": 0, "by_kind": []}},
    }


def _stats_response() -> ScopedStats:
    inventory = _empty_inventory()
    usage = {
        "period": {
            "preset": "today",
            "start_date": "2026-08-04",
            "end_date": "2026-08-04",
            "timezone": "UTC",
        },
        "totals": {
            "generation": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
            "embedding": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
        },
        "by_purpose": [],
        "daily": [
            {
                "date": "2026-08-04",
                "generation": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
                "embedding": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
                "by_purpose": [],
            }
        ],
    }
    recall = {
        "period": {
            "preset": "today",
            "start_date": "2026-08-04",
            "end_date": "2026-08-04",
            "timezone": "UTC",
        },
        "estimator": {"estimator_id": "character:weighted", "version": "1"},
        "totals": {
            "preparations": 3,
            "ready_preparations": 2,
            "comparable_preparations": 1,
            "baseline_tokens": 100,
            "recalled_tokens": 40,
            "token_reduction": 60,
        },
        "daily": [
            {
                "date": "2026-08-04",
                "preparations": 3,
                "ready_preparations": 2,
                "comparable_preparations": 1,
                "baseline_tokens": 100,
                "recalled_tokens": 40,
                "token_reduction": 60,
            }
        ],
    }
    recurrence = {
        "selected": 0,
        "recurred": 0,
        "avoided": 0,
        "unknown": 0,
        "unlinked_handoff_citations": 0,
        "needing_review": 0,
        "top_revisions": [],
    }
    return ScopedStats.model_validate({
        "selection": {"mode": "exact", "scope_ids": ["project"]},
        "scope_ids": ["project"],
        "as_of": "2026-08-04T12:00:00Z",
        "inventory": inventory,
        "usage": usage,
        "recall": recall,
        "by_scope": [
            {
                "scope_id": "project",
                "inventory": inventory,
                "usage": usage,
                "recall": recall,
                "recurrence": recurrence,
            }
        ],
    })


def stats(reduction: int) -> dict[str, object]:
    value = _stats_response().model_dump(mode="json")
    value["recall"]["totals"]["token_reduction"] = reduction
    return value


def scope(scope_id: str = "project:test") -> dict[str, object]:
    return {
        "scope_id": scope_id,
        "title": "Project",
        "summary": "Project context",
        "context_references": [],
        "external_references": [],
        "version": 1,
    }
