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

"""v14 wire payload: evidence plus a cell table; the model references cell ids only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionInput
from powercontext.builtin.inference import character_token_estimator

from ..manifests import EvaluationError
from ..models import Support, canonical, digest
from ..v8.admission import range_units
from .codec import INSTRUCTIONS, Cell, TemplateOutput, build_cells


@dataclass(frozen=True)
class Request:
    value: AtomicMemoryExtractionInput
    supports: tuple[Support, ...]
    payload: dict[str, Any]
    cells: tuple[Cell, ...]
    binding_hash: str
    cost: dict[str, Any]


def evidence_tree(value, evidence_index, pointer=""):
    """Encode one evidence value as a native tree; leaves stay typed, no inline copy ids."""
    if isinstance(value, dict) and value:
        return {
            key: evidence_tree(value[key], evidence_index, pointer + "/" + key.replace("~", "~0").replace("/", "~1"))
            for key in sorted(value)
        }
    if isinstance(value, list) and value:
        return [evidence_tree(child, evidence_index, f"{pointer}/{i}") for i, child in enumerate(value)]
    return value


def prepare(value: AtomicMemoryExtractionInput, supports: tuple[Support, ...]) -> Request:
    """Encode supplied evidence plus a cell table; full identity stays in the internal binding."""
    evidence_ids = {e.evidence_id for e in value.evidence}
    if len(evidence_ids) != len(value.evidence):
        raise EvaluationError("v14_duplicate_evidence_id")
    supports = tuple(s for s in supports if s.evidence_id in evidence_ids)
    cells = build_cells(value, supports)
    payload: dict[str, Any] = {"fields": [], "evidence": [], "cells": [c.index() for c in cells]}
    for ei, evidence in enumerate(value.evidence):
        encoded = {
            "evidence_id": f"e{ei}",
            "source_ref": evidence.source_ref.model_dump(mode="json"),
            "journal_position": evidence.journal_position,
        }
        if evidence.via_artifact is not None:
            encoded["via_artifact"] = evidence.via_artifact.model_dump(mode="json")
        is_bundle = evidence.evidence_id.startswith("fragment-bundle:")
        is_fragment = evidence.evidence_id.startswith("fragment:")
        if is_bundle or is_fragment:
            metadata = evidence.source_metadata
            allowed = {"materialization_hash", "projection_hash", "admission_policy", "source_declaration_changed"}
            if (
                not isinstance(evidence.content, dict)
                or not isinstance(metadata, dict)
                or set(metadata) - allowed
                or metadata.get("source_declaration_changed") is not False
            ):
                raise EvaluationError("v14_unrecognized_fragment_envelope")
            encoded["incomplete"] = evidence.content.get("incomplete", True)
            encoded["ranges"] = []
            for _, row, _, _ in range_units(evidence, supports):
                field = row["field"]
                if field not in payload["fields"]:
                    payload["fields"].append(field)
                encoded["ranges"].append({"f": payload["fields"].index(field), "b": row["bytes"], "v": row["value"]})
        else:
            encoded["data"] = evidence_tree(
                {"content": evidence.content, "source_metadata": evidence.source_metadata}, ei
            )
        payload["evidence"].append(encoded)
    binding = digest(
        {
            "input": value.model_dump(mode="json"),
            "supports": [s.model_dump(mode="json") for s in supports],
            "payload": payload,
            "cells": [c.identity() for c in cells],
            "instructions": INSTRUCTIONS,
            "schema": TemplateOutput.model_json_schema(),
        }
    )
    return Request(value, supports, payload, cells, binding, ledger(payload, cells))


def ledger(payload: dict[str, Any], cells: tuple[Cell, ...]) -> dict[str, Any]:
    """Charge instructions/evidence/cell-table/schema on the same canonical basis as v7/v8/v9."""
    estimator = character_token_estimator()
    schema = TemplateOutput.model_json_schema()
    encoded = canonical({"instructions": INSTRUCTIONS, "input": payload, "output_schema": schema})
    total = estimator.estimate(encoded.decode())
    instructions = estimator.estimate(INSTRUCTIONS)
    schema_tokens = estimator.estimate(canonical(schema).decode())
    cell_tokens = estimator.estimate(canonical(payload["cells"]).decode())
    evidence_tokens = estimator.estimate(
        canonical({"evidence": payload["evidence"], "fields": payload["fields"]}).decode()
    )
    context = max(0, total - instructions - schema_tokens - cell_tokens - evidence_tokens)
    return {
        "estimator": "character:weighted@1",
        "single_estimated_input_tokens": total,
        "serialized_request_bytes": len(encoded),
        "input_hash": digest(payload),
        "schema_hash": digest(schema),
        "components": {
            "instructions": instructions,
            "evidence_body": evidence_tokens,
            "cell_table": cell_tokens,
            "output_schema": schema_tokens,
            "context_and_framing": context,
        },
        "cell_count": len(cells),
        "provider_wire_overhead_unknown": True,
    }


def restore(request: Request, payload: dict[str, Any], *, request_hash: str) -> AtomicMemoryExtractionInput:
    """Roundtrip: authenticate the whole request, then restore each selected Source value."""
    if (
        digest(
            {
                "input": request.value.model_dump(mode="json"),
                "supports": [s.model_dump(mode="json") for s in request.supports],
                "payload": payload,
                "cells": [c.identity() for c in request.cells],
                "instructions": INSTRUCTIONS,
                "schema": TemplateOutput.model_json_schema(),
            }
        )
        != request_hash
    ):
        raise EvaluationError("v14_request_binding_mismatch")
    if digest(payload["cells"]) != digest([c.index() for c in request.cells]):
        raise EvaluationError("v14_cell_table_mismatch")
    data = request.value.model_dump(mode="json")
    for ei, source in enumerate(payload["evidence"]):
        if source["evidence_id"] != f"e{ei}":
            raise EvaluationError("v14_evidence_alias_mismatch")
        target = data["evidence"][ei]
        target["source_ref"] = source["source_ref"]
        target["journal_position"] = source["journal_position"]
        target["via_artifact"] = source.get("via_artifact")
    return AtomicMemoryExtractionInput.model_validate(data)
