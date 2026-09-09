# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Project contract-shaped read responses into small template contexts."""

import json
from typing import Any


def source_view(source: dict[str, Any] | None) -> dict[str, Any] | None:
    if source is None:
        return None
    content = source["content"]
    return {
        **source,
        "text": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, indent=2),
    }


def memory_view(response: dict[str, Any]) -> list[dict[str, Any]]:
    return [{**entry, **entry["citation"]} for entry in response["entries"]]


def usage_view(usage: dict[str, Any], recall: dict[str, Any]) -> dict[str, Any]:
    """Keep reported totals authoritative and separate missing estimates from inactivity."""
    totals = recall["totals"]
    generation = usage["totals"]["generation"]
    embedding = usage["totals"]["embedding"]
    daily = [{**day, "label": f"{int(day['date'][5:7])}/{int(day['date'][8:10])}"} for day in recall["daily"]]
    rows = [
        {"purpose": row["purpose"], "operation": operation, **row[operation]}
        for row in usage["by_purpose"]
        for operation in ("generation", "embedding")
        if row[operation]["requests"] > 0
    ]
    return {
        **totals,
        "estimator_available": recall["estimator"] is not None,
        "empty": recall["estimator"] is not None
        and totals["preparations"] == 0
        and generation["requests"] == embedding["requests"] == 0,
        "model_usage": rows,
        "generation": generation,
        "embedding": embedding,
        "unknown_usage": any(
            value[key] is None for value in (generation, embedding) for key in ("input_tokens", "output_tokens")
        ),
        "daily": daily,
        "percent": round(abs(totals["token_reduction"]) / totals["baseline_tokens"] * 100)
        if totals["baseline_tokens"] and totals["comparable_preparations"]
        else None,
        "scale": max([1, *(max(day["baseline_tokens"], day["recalled_tokens"]) for day in daily)]),
        "start": usage["period"]["start_date"],
        "end": usage["period"]["end_date"],
        "days": len(daily),
    }
