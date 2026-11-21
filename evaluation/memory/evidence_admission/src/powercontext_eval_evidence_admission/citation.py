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

"""Deterministic evaluation-only fragment selection; no access to fixture labels."""

from __future__ import annotations

from dataclasses import replace

from .admission import Admission, token_count
from .bounded_v3 import cost_components
from .manifests import EvaluationError
from .models import digest


def render_citations(admission: Admission, instructions: str, selection_instructions: str, budget: int) -> Admission:
    """Render selected evidence without allowing envelope savings to change the selected ranges."""
    inputs = []
    identities = {}
    for value in admission.inputs:
        evidence = []
        for item in value.evidence:
            if item.evidence_id.startswith("fragment-bundle:"):
                content = item.content
                if not isinstance(content, dict) or not isinstance(content.get("ranges"), list):
                    raise EvaluationError("invalid_selected_bundle")
                ranges = content["ranges"]
                metadata = item.source_metadata
                if not isinstance(metadata, dict) or metadata.get("admission_policy") not in {
                    "bounded-fragments-v2",
                    "bounded-fragments-v3",
                }:
                    raise EvaluationError("invalid_selected_bundle")
                if any(not isinstance(r, dict) or not isinstance(r.get("id"), str) for r in ranges):
                    raise EvaluationError("invalid_selected_bundle")
                identity = digest(
                    {
                        "policy": metadata["admission_policy"],
                        "fragments": [r["id"] for r in ranges],
                        "encoding": "source-range-bundle-citation-v2",
                    }
                )
                new_id = f"fragment-bundle:{identity}"
                identities[item.evidence_id] = new_id
                item = item.model_copy(
                    update={
                        "evidence_id": new_id,
                        "content": {
                            **content,
                            "ranges": [{k: v for k, v in r.items() if k != "id"} for r in ranges],
                        },
                    }
                )
            evidence.append(item)
        inputs.append(value.model_copy(update={"evidence": tuple(evidence)}))
    old_costs = [token_count(value, selection_instructions) for value in admission.inputs]
    costs = [token_count(value, instructions) for value in inputs]
    if any(new > old or new > budget for new, old in zip(costs, old_costs, strict=True)):
        raise EvaluationError("citation_render_increases_selection_cost")
    metrics = admission.metrics.model_copy(
        update={
            "prepared_tokens": max(costs, default=0),
            "prepared_tokens_total": sum(costs),
            "selection_prepared_tokens": max(old_costs, default=0),
            "selection_prepared_tokens_total": sum(old_costs),
            "serialized_input_bytes": sum(len(value.model_dump_json().encode()) for value in inputs),
        }
    )
    if metrics.input_estimator_units_by_family:
        metrics = cost_components(metrics, tuple(inputs), admission.admitted, instructions)
    return replace(
        admission,
        inputs=tuple(inputs),
        metrics=metrics,
        supports=tuple(
            support.model_copy(update={"evidence_id": identities.get(support.evidence_id, support.evidence_id)})
            for support in admission.supports
        ),
    )
