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

"""Lossless inline cell wire: each evidence string is represented only once."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from powercontext.builtin.artifacts.atomic_memory.extraction import AtomicMemoryExtractionInput

from ..manifests import EvaluationError
from ..models import Support, digest
from ..v15.wire import decode_tree, encode_tree, ledger
from .codec import INSTRUCTIONS, Cell, TemplateOutput, build_cells


@dataclass(frozen=True)
class Request:
    value: AtomicMemoryExtractionInput
    supports: tuple[Support, ...]
    payload: dict[str, Any]
    cells: tuple[Cell, ...]
    aliases: tuple[str, ...]
    binding_hash: str
    cost: dict[str, Any]


def binding(value, supports, payload, cells):
    return digest(
        {
            "protocol": "v16-inline-cell",
            "input": value.model_dump(mode="json"),
            "supports": [s.model_dump(mode="json") for s in supports],
            "payload": payload,
            "cells": [c.identity() for c in cells],
            "instructions": INSTRUCTIONS,
            "schema": TemplateOutput.model_json_schema(),
        }
    )


def prepare(value: AtomicMemoryExtractionInput, supports: tuple[Support, ...]) -> Request:
    aliases = tuple(e.evidence_id for e in value.evidence)
    if len(set(aliases)) != len(aliases):
        raise EvaluationError("v16_duplicate_evidence_id")
    supports = tuple(s for s in supports if s.evidence_id in aliases)
    cells = build_cells(value, supports)
    inventory = defaultdict(list)
    for cell in cells:
        inventory[cell.evidence_index, cell.pointer].append(cell)
    payload: dict[str, Any] = {"fields": [], "evidence": []}
    for index, evidence in enumerate(value.evidence):
        encoded = {
            "evidence_id": f"e{index}",
            "source_ref": evidence.source_ref.model_dump(mode="json"),
            "journal_position": evidence.journal_position,
        }
        if evidence.via_artifact is not None:
            encoded["via_artifact"] = evidence.via_artifact.model_dump(mode="json")
        if evidence.evidence_id.startswith(("fragment-bundle:", "fragment:")):
            metadata = evidence.source_metadata
            allowed = {"materialization_hash", "projection_hash", "admission_policy", "source_declaration_changed"}
            if (
                not isinstance(evidence.content, dict)
                or not isinstance(metadata, dict)
                or set(metadata) - allowed
                or metadata.get("source_declaration_changed") is not False
            ):
                raise EvaluationError("v16_unrecognized_fragment_envelope")
            bundle = evidence.evidence_id.startswith("fragment-bundle:")
            content = evidence.content
            rows = (
                content["ranges"]
                if bundle
                else [{"field": content["field"], "bytes": content["byte_range"], "value": content["value"]}]
            )
            encoded["incomplete"] = content.get("incomplete", True)
            encoded["ranges"] = []
            for row_index, row in enumerate(rows):
                field = row["field"]
                if field not in payload["fields"]:
                    payload["fields"].append(field)
                pointer = f"/content/ranges/{row_index}/value" if bundle else "/content/value"
                encoded["ranges"].append(
                    {
                        "f": payload["fields"].index(field),
                        "b": row["bytes"],
                        "v": encode_tree(row["value"], index, inventory, pointer),
                    }
                )
        else:
            encoded["data"] = encode_tree(
                {"content": evidence.content, "source_metadata": evidence.source_metadata}, index, inventory
            )
        payload["evidence"].append(encoded)
    cost = ledger(payload, cells)
    return Request(value, supports, payload, cells, aliases, binding(value, supports, payload, cells), cost)


def restore(request: Request, payload, *, request_hash: str) -> AtomicMemoryExtractionInput:
    if binding(request.value, request.supports, payload, request.cells) != request_hash or request.aliases != tuple(
        e.evidence_id for e in request.value.evidence
    ):
        raise EvaluationError("v16_request_binding_mismatch")
    data = request.value.model_dump(mode="json")
    for index, encoded in enumerate(payload["evidence"]):
        target = data["evidence"][index]
        if encoded["evidence_id"] != f"e{index}":
            raise EvaluationError("v16_evidence_alias_mismatch")
        if "data" in encoded:
            decoded = decode_tree(
                {"content": target["content"], "source_metadata": target["source_metadata"]}, encoded["data"]
            )
            target.update(decoded)
        else:
            bundle = target["evidence_id"].startswith("fragment-bundle:")
            for row_index, row in enumerate(encoded["ranges"]):
                location = target["content"]["ranges"][row_index] if bundle else target["content"]
                location["value"] = decode_tree(location["value"], row["v"])
    restored = AtomicMemoryExtractionInput.model_validate(data)
    if restored != request.value:
        raise EvaluationError("v16_wire_roundtrip_changed_input")
    return restored
