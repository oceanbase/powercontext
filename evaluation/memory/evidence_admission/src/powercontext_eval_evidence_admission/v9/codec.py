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

"""Compact wire encoding; original values and complete provenance stay independently verifiable."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryExtractionInput,
    atomic_memory_extraction_instructions,
)
from powercontext.builtin.artifacts.memory.prompts import MemoryExtractionProfile
from powercontext.builtin.inference import character_token_estimator

from ..manifests import EvaluationError, field_value
from ..models import Support, canonical, digest
from ..v7.fidelity import (
    Quote,
    TemplateOutput,
    quote_inventory,
)
from ..v7.fidelity import (
    binding as legacy_binding,
)
from ..v7.fidelity import (
    render as legacy_render,
)
from ..v8.admission import range_units

BASE_INSTRUCTIONS = atomic_memory_extraction_instructions(MemoryExtractionProfile.CODING)
COPY_RULES = """Full evidence uses data with its original object/array structure and each leaf boxed as {v,...}.
Fragment fields[f] names an original JSON path; ranges contain typed v at original UTF-8 byte positions b.
Evidence may be incomplete. Inline q names string v; copies entries instead name local byte slices b of v.
Return TemplateOutput with text/copy parts. Cite supplied evidence_id aliases e0, e1 etc; each copy must
belong to cited evidence. q ids are not evidence_ids. Copy offsets are local UTF-8 offsets within the named
string, at most 1024 bytes per copy. No normalization, newline conversion or backfill. Quoting alone is not a fact.
""".strip()

INSTRUCTIONS = (
    BASE_INSTRUCTIONS.replace(
        "Each candidate must cite one or more supplied evidence_ids and contain self-contained kind/text.",
        "Each candidate must cite supplied evidence_ids and render self-contained kind/text from its parts.",
    )
    + "\n"
    + COPY_RULES
)


@dataclass(frozen=True)
class RangeLocation:
    evidence_index: int
    row_index: int
    pointer: str
    field: str
    source_bytes: tuple[int, int]


@dataclass(frozen=True)
class Request:
    value: AtomicMemoryExtractionInput
    supports: tuple[Support, ...]
    payload: dict[str, Any]
    quotes: tuple[Quote, ...]
    locations: tuple[RangeLocation, ...]
    aliases: tuple[str, ...]
    binding_hash: str
    cost: dict[str, Any]


def request_binding(value, supports, payload, quotes, locations, aliases):
    return digest(
        {
            "input": value.model_dump(mode="json"),
            "supports": [s.model_dump(mode="json") for s in supports],
            "payload": payload,
            "quotes": [q.identity() for q in quotes],
            "locations": [vars(p) for p in locations],
            "aliases": aliases,
            "instructions": INSTRUCTIONS,
            "schema": TemplateOutput.model_json_schema(),
        }
    )


def wire_row(payload, location):
    evidence = payload["evidence"][location.evidence_index]
    return (
        evidence["ranges"][location.row_index]
        if location.row_index >= 0
        else field_value(evidence["data"], location.pointer)
    )


def copy_value(value, quotes, evidence_index, pointer):
    part = {"v": value}
    related = [q for q in quotes if q.evidence_index == evidence_index and q.pointer == pointer]
    if len(related) == 1 and related[0].value == value and related[0].value_byte_start == 0:
        part["q"] = related[0].quote_id
    elif related:
        part["copies"] = [
            {"q": q.quote_id, "b": [q.value_byte_start, q.value_byte_start + len(q.value.encode())]} for q in related
        ]
    return part


def source_tree(value, quotes, evidence_index, locations, pointer=""):
    if isinstance(value, dict) and value:
        return {
            key: source_tree(
                value[key], quotes, evidence_index, locations, pointer + "/" + key.replace("~", "~0").replace("/", "~1")
            )
            for key in sorted(value)
        }
    if isinstance(value, list) and value:
        return [
            source_tree(child, quotes, evidence_index, locations, f"{pointer}/{i}") for i, child in enumerate(value)
        ]
    size = len(value.encode()) if isinstance(value, str) else len(canonical(value))
    locations.append(RangeLocation(evidence_index, -1, pointer, pointer, (0, size)))
    return copy_value(value, quotes, evidence_index, pointer)


def ledger(payload, locations):
    """Charge the same full canonical instructions/input/schema envelope used by v7/v8."""
    estimator = character_token_estimator()
    schema = TemplateOutput.model_json_schema()
    encoded = canonical({"instructions": INSTRUCTIONS, "input": payload, "output_schema": schema})
    total = estimator.estimate(encoded.decode())
    instructions = estimator.estimate(INSTRUCTIONS)
    schema_tokens = estimator.estimate(canonical(schema).decode())
    values = sum(estimator.estimate(canonical(wire_row(payload, p)["v"]).decode()) for p in locations)
    fields = estimator.estimate(canonical(payload["fields"]).decode())
    context = total - instructions - schema_tokens - values - fields
    return {
        "estimator": "character:weighted@1",
        "single_estimated_input_tokens": total,
        "serialized_request_bytes": len(encoded),
        "input_hash": digest(payload),
        "schema_hash": digest(schema),
        "components": {
            "instructions": instructions,
            "selected_values": values,
            "field_dictionary": fields,
            "context_copy_ids_and_framing": context,
            "output_schema": schema_tokens,
        },
        "provider_wire_overhead_unknown": True,
    }


def prepare(value: AtomicMemoryExtractionInput, supports: tuple[Support, ...]) -> Request:
    """Encode supplied evidence only; fixture claims and omitted Sources are not available here."""
    evidence_ids = {e.evidence_id for e in value.evidence}
    if len(evidence_ids) != len(value.evidence):
        raise EvaluationError("v9_duplicate_evidence_id")
    supports = tuple(s for s in supports if s.evidence_id in evidence_ids)
    quotes = quote_inventory(value, supports)
    payload = {"fields": [], "evidence": []}
    locations = []
    aliases = []
    for ei, evidence in enumerate(value.evidence):
        aliases.append(evidence.evidence_id)
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
                or metadata.get("admission_policy")
                not in {"bounded-fragments-v1", "bounded-fragments-v2", "bounded-fragments-v3", "request-budget-v8"}
            ):
                raise EvaluationError("v9_unrecognized_fragment_envelope")
            relevant = [support for support in supports if support.evidence_id == evidence.evidence_id]
            for key in ("materialization_hash", "projection_hash"):
                if key in metadata and any(metadata[key] != getattr(support, key) for support in relevant):
                    raise EvaluationError("v9_fragment_provenance_mismatch")
            encoded["incomplete"] = evidence.content.get("incomplete", True)
            encoded["ranges"] = []
            for ri, (_, row, _, _) in enumerate(range_units(evidence, supports)):
                field = row["field"]
                if field not in payload["fields"]:
                    payload["fields"].append(field)
                pointer = f"/content/ranges/{ri}/value" if is_bundle else "/content/value"
                part = {
                    "f": payload["fields"].index(field),
                    "b": row["bytes"],
                    **copy_value(row["value"], quotes, ei, pointer),
                }
                encoded["ranges"].append(part)
                locations.append(RangeLocation(ei, ri, pointer, field, tuple(row["bytes"])))
        else:
            encoded["data"] = source_tree(
                {"content": evidence.content, "source_metadata": evidence.source_metadata}, quotes, ei, locations
            )
        payload["evidence"].append(encoded)
    locations, aliases = tuple(locations), tuple(aliases)
    request_hash = request_binding(value, supports, payload, quotes, locations, aliases)
    return Request(value, supports, payload, quotes, locations, aliases, request_hash, ledger(payload, locations))


def verify_copy_index(request, payload):
    available = {q.quote_id: q for q in request.quotes}
    seen = []
    for location in request.locations:
        row = wire_row(payload, location)
        entries = [{"q": row["q"], "b": [0, len(row["v"].encode())]}] if "q" in row else row.get("copies", [])
        for entry in entries:
            q = available.get(entry["q"])
            if q is None or q.evidence_index != location.evidence_index or q.pointer != location.pointer:
                raise EvaluationError("v9_invalid_inline_copy")
            if not isinstance(row["v"], str):
                raise EvaluationError("v9_copy_requires_string")
            left, right = entry["b"]
            if not 0 <= left < right <= len(row["v"].encode()):
                raise EvaluationError("v9_invalid_inline_copy")
            if left != q.value_byte_start or row["v"].encode()[left:right] != q.value.encode():
                raise EvaluationError("v9_invalid_inline_copy")
            seen.append(q.quote_id)
    if len(seen) != len(set(seen)) or set(seen) != set(available):
        raise EvaluationError("v9_incomplete_inline_copy_map")


def restore(request: Request, payload: dict, *, request_hash: str) -> AtomicMemoryExtractionInput:
    """Restore actual wire values into the original projection, after authenticating the whole request.

    Only program-generated envelopes are taken from the internal input; each selected
    Source value is read from the serialized payload, preserving its JSON type.
    """
    if (
        request_binding(request.value, request.supports, payload, request.quotes, request.locations, request.aliases)
        != request_hash
    ):
        raise EvaluationError("v9_request_binding_mismatch")
    verify_copy_index(request, payload)
    data = request.value.model_dump(mode="json")
    for location in request.locations:
        source = payload["evidence"][location.evidence_index]
        row = wire_row(payload, location)
        if (
            source["evidence_id"] != f"e{location.evidence_index}"
            or location.row_index >= 0
            and (payload["fields"][row["f"]] != location.field or tuple(row["b"]) != location.source_bytes)
        ):
            raise EvaluationError("v9_range_location_mismatch")
        target = data["evidence"][location.evidence_index]
        parent, _, final = location.pointer.rpartition("/")
        container = field_value(target, parent) if parent else target
        key = final.replace("~1", "/").replace("~0", "~")
        container[int(key) if isinstance(container, list) else key] = row["v"]
        target["source_ref"] = source["source_ref"]
        target["journal_position"] = source["journal_position"]
        target["via_artifact"] = source.get("via_artifact")
    return AtomicMemoryExtractionInput.model_validate(data)


def render(output: TemplateOutput, request: Request, *, request_hash: str):
    value = restore(request, request.payload, request_hash=request_hash)
    aliases = {f"e{i}": original for i, original in enumerate(request.aliases)}
    candidates = []
    for candidate in output.candidates:
        if any(e not in aliases for e in candidate.evidence_ids):
            raise EvaluationError("invalid_evidence_reference")
        candidates.append(
            candidate.model_copy(update={"evidence_ids": tuple(aliases[e] for e in candidate.evidence_ids)})
        )
    translated = output.model_copy(update={"candidates": tuple(candidates)})
    return legacy_render(translated, value, request.quotes, request_hash=legacy_binding(value, request.quotes))
