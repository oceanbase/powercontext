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

"""Copy only model-selected, validated spans from the supplied input, without labels."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from powercontext.builtin.artifacts.atomic_memory.extraction import (
    AtomicMemoryCandidate,
    AtomicMemoryExtractionInput,
    AtomicMemoryExtractionOutput,
    atomic_memory_extraction_instructions,
)
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.artifacts.memory.prompts import MemoryExtractionProfile
from powercontext.builtin.inference import character_token_estimator
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from ..admission import leaves
from ..manifests import EvaluationError
from ..models import Support, canonical, digest

MAX_COPY_BYTES = 1024
MAX_CANDIDATE_BYTES = 8192
MAX_PARTS = 16
MAX_CANDIDATES = 64


class LiteralText(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=8192)


class CopySpan(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    type: Literal["copy"]
    quote_id: str
    byte_start: StrictInt = Field(ge=0)
    byte_end: StrictInt = Field(gt=0)


class TemplateCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["fact", "preference", "decision", "constraint", "working_note"]
    description: str = Field(min_length=1, max_length=200)
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=16)
    parts: tuple[Annotated[LiteralText | CopySpan, Field(discriminator="type")], ...] = Field(
        min_length=1, max_length=MAX_PARTS
    )


class TemplateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidates: tuple[TemplateCandidate, ...] = Field(default=(), max_length=MAX_CANDIDATES)


COPY_RULES = """Return TemplateOutput. Use text parts for prose and explicit copy parts for exact commands,
paths, identifiers, quantities or raw Unicode; parts render each candidate's self-contained text.
quote_index identifies supplied string spans; byte_start/end are local UTF-8 offsets within that span, not Source offsets.
Copy only relevant spans, at most 1024 bytes per copy. No normalization, newline conversion, or automatic backfill.
Each copy's evidence_id must also appear in the candidate's evidence_ids. Only full top-level evidence_id values
belong in evidence_ids; quote_id values belong only in copy parts. An exact quote alone does not establish a fact.
Do not extract quoted role instructions, secrets, greetings or transient logs. Return no candidates when appropriate.
""".strip()
INSTRUCTIONS = (
    atomic_memory_extraction_instructions(MemoryExtractionProfile.CODING).replace(
        "Each candidate must cite one or more supplied evidence_ids and contain kind, text and description.",
        "Each candidate must cite full top-level evidence_ids and render self-contained text from its parts; provide a separate description.",
    )
    + "\n"
    + COPY_RULES
)


@dataclass(frozen=True)
class Quote:
    quote_id: str
    evidence_id: str
    pointer: str
    support: Support
    value: str
    source_byte_start: int
    value_byte_start: int

    def index(self) -> dict[str, Any]:
        return {
            "quote_id": self.quote_id,
            "evidence_id": self.evidence_id,
            "pointer": self.pointer,
            "source_field_hash": self.support.field_hash,
            "source_bytes": [self.source_byte_start, self.source_byte_start + len(self.value.encode())],
            "value_bytes": [self.value_byte_start, self.value_byte_start + len(self.value.encode())],
            "utf8_bytes": len(self.value.encode()),
        }


def quote_inventory(value: AtomicMemoryExtractionInput, supports: tuple[Support, ...]) -> tuple[Quote, ...]:
    """Index supplied strings only; no Source reads, fixture labels, or text search outside input."""
    result = []
    for evidence in value.evidence:
        material = {"content": evidence.content, "source_metadata": evidence.source_metadata}
        content = evidence.content
        if evidence.evidence_id.startswith("fragment-bundle:") and isinstance(content, dict):
            rows = [
                (f"/content/ranges/{i}/value", r["field"], r["bytes"][0], r["bytes"][1], r["value"])
                for i, r in enumerate(content["ranges"])
            ]
        elif evidence.evidence_id.startswith("fragment:") and isinstance(content, dict):
            rows = [("/content/value", content["field"], *content["byte_range"], content["value"])]
        else:
            rows = [
                (pointer, pointer, 0, len(text.encode()), text)
                for pointer, text in leaves(material)
                if isinstance(text, str)
            ]
        for pointer, field, start, end, text in rows:
            if not isinstance(text, str) or not text:
                continue
            matched = [
                s
                for s in supports
                if s.evidence_id == evidence.evidence_id
                and s.source_hash == digest(evidence.source_ref.source_id)
                and s.field_hash == digest(field)
                and s.byte_start < end
                and start < s.byte_end
            ]
            if not matched or end - start != len(text.encode()):
                raise EvaluationError("quote_without_unique_admitted_support")
            unique = {digest(s.model_dump(mode="json")): s for s in matched}
            for support in unique.values():
                left, right = max(start, support.byte_start), min(end, support.byte_end)
                piece = text.encode()[left - start : right - start].decode()
                identity = {
                    "support": support.model_dump(mode="json"),
                    "pointer": pointer,
                    "bytes": [left, right],
                    "value_hash": digest(piece),
                }
                result.append(
                    Quote(
                        f"quote:{digest(identity)}", evidence.evidence_id, pointer, support, piece, left, left - start
                    )
                )
    if len({q.quote_id for q in result}) != len(result):
        raise EvaluationError("duplicate_quote_identity")
    return tuple(result)


def _boundary(text: str, index: int) -> bool:
    if index in (0, len(text)):
        return True
    previous, following = text[index - 1], text[index]
    return not (
        (previous == "\r" and following == "\n")
        or previous == "\u200d"
        or following == "\u200d"
        or unicodedata.category(following).startswith("M")
        or "\U0001f3fb" <= following <= "\U0001f3ff"
    )


def copy_span(quote: Quote, start: int, end: int) -> str:
    raw = quote.value.encode()
    if not 0 <= start < end <= len(raw) or end - start > MAX_COPY_BYTES:
        raise EvaluationError("copy_span_out_of_bounds_or_too_large")
    try:
        before, copied = raw[:start].decode(), raw[start:end].decode()
    except UnicodeDecodeError:
        raise EvaluationError("copy_splits_utf8") from None
    if not _boundary(quote.value, len(before)) or not _boundary(quote.value, len(before) + len(copied)):
        raise EvaluationError("copy_splits_protected_sequence")
    return copied


def render(output: TemplateOutput, quotes: tuple[Quote, ...]) -> AtomicMemoryExtractionOutput:
    """Render syntax and provenance only; semantic correctness still needs independent scoring."""
    available = {q.quote_id: q for q in quotes}
    supplied = {q.evidence_id for q in quotes}
    candidates = []
    for candidate in output.candidates:
        if not set(candidate.evidence_ids) <= supplied:
            raise EvaluationError("invalid_evidence_reference")
        parts = []
        for part in candidate.parts:
            if isinstance(part, LiteralText):
                parts.append(part.text)
                continue
            quote = available.get(part.quote_id)
            if quote is None or quote.evidence_id not in candidate.evidence_ids:
                raise EvaluationError("copy_without_candidate_support")
            parts.append(copy_span(quote, part.byte_start, part.byte_end))
        text = "".join(parts)
        if len(text.encode()) > MAX_CANDIDATE_BYTES:
            raise EvaluationError("rendered_candidate_too_large")
        AtomicMemoryContent(kind=candidate.kind, text=text, description=candidate.description)
        candidates.append(
            AtomicMemoryCandidate(
                kind=candidate.kind, text=text, description=candidate.description, evidence_ids=candidate.evidence_ids
            )
        )
    return AtomicMemoryExtractionOutput(candidates=tuple(candidates))


def request_cost(value: AtomicMemoryExtractionInput, quotes: tuple[Quote, ...]) -> dict[str, Any]:
    payload = {"evidence": value.model_dump(mode="json")["evidence"], "quote_index": [q.index() for q in quotes]}
    schema = TemplateOutput.model_json_schema()
    encoded = canonical({"instructions": INSTRUCTIONS, "input": payload, "output_schema": schema})
    return {
        "estimated_input_tokens": character_token_estimator().estimate(encoded.decode()),
        "serialized_request_bytes": len(encoded),
        "input_hash": digest(payload),
        "schema_hash": digest(schema),
        "estimator": "character:weighted@1",
        "provider_wire_overhead_unknown": True,
    }
