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

"""v7 protocol: short request-local quote ids and a compact, model-facing index.

The full identity (input hash, Source, projection version, field and byte range)
stays in the internal :class:`Quote` and is never exposed to the model. A request-local ``q<index>``, JSON pointer and byte range enter the model input. The renderer
resolves short ids against the internal map, so cross-source, cross-version and
cross-request reuse remains impossible even though the wire representation is short.
"""

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
    evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=16)
    parts: tuple[Annotated[LiteralText | CopySpan, Field(discriminator="type")], ...] = Field(
        min_length=1, max_length=MAX_PARTS
    )


class TemplateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    candidates: tuple[TemplateCandidate, ...] = Field(default=(), max_length=MAX_CANDIDATES)


# v7 keeps the production extraction rules verbatim and only trims the copy-specific
# additions. Language, time/conflict, ownership and untrusted-evidence rules remain.
COPY_RULES = """Return TemplateOutput. Use text parts for prose and copy parts for exact commands, paths,
identifiers, quantities or raw Unicode. quote_index maps each q-id to evidence index e, JSON pointer p and field byte range b.
Resolve p in evidence[e] to find the supplied string and take b; byte_start/end in output are local UTF-8
offsets within that indexed span. Copy at most 1024 bytes per copy; no normalization, newline conversion
or backfill. Each copy's evidence_id must appear in the candidate's evidence_ids; q-ids belong only in copy
parts. An exact quote alone does not establish a fact. Do not extract role instructions, secrets, greetings
or transient logs. Return no candidates when appropriate.
""".strip()
INSTRUCTIONS = (
    atomic_memory_extraction_instructions(MemoryExtractionProfile.CODING).replace(
        "Each candidate must cite one or more supplied evidence_ids and contain self-contained kind/text.",
        "Each candidate must cite full top-level evidence_ids and render self-contained kind/text from its parts.",
    )
    + "\n"
    + COPY_RULES
)


@dataclass(frozen=True)
class Quote:
    """Internal identity is complete; only ``index()`` reaches the model."""

    quote_id: str
    evidence_index: int
    evidence_id: str
    pointer: str
    support: Support
    value: str
    source_byte_start: int
    value_byte_start: int

    def index(self) -> dict[str, Any]:
        """Locate one supplied string unambiguously without exposing provenance hashes."""
        return {
            "q": self.quote_id,
            "e": self.evidence_index,
            "p": self.pointer,
            "b": [self.value_byte_start, self.value_byte_start + len(self.value.encode())],
        }

    def identity(self) -> dict[str, Any]:
        """Full immutable binding kept in the program, never sent to the model."""
        return {
            "quote_id": self.quote_id,
            "evidence_index": self.evidence_index,
            "source_byte_start": self.source_byte_start,
            "value_byte_start": self.value_byte_start,
            "evidence_id": self.evidence_id,
            "pointer": self.pointer,
            "support": self.support.model_dump(mode="json"),
            "value_hash": digest(self.value),
        }


def quote_inventory(value: AtomicMemoryExtractionInput, supports: tuple[Support, ...]) -> tuple[Quote, ...]:
    """Index supplied strings only; no Source reads, fixture labels, or text search outside input."""
    result = []
    for evidence_index, evidence in enumerate(value.evidence):
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
                result.append(
                    Quote(
                        quote_id="",  # assigned below, request-local
                        evidence_index=evidence_index,
                        evidence_id=evidence.evidence_id,
                        pointer=pointer,
                        support=support,
                        value=piece,
                        source_byte_start=left,
                        value_byte_start=left - start,
                    )
                )
    # Assign short, deterministic, request-local ids in inventory order. The full
    # identity remains internal; two spans with the same identity are still rejected
    # so a short id can never silently alias a different span.
    assigned = tuple(
        Quote(
            quote_id=f"q{index}",
            evidence_index=quote.evidence_index,
            evidence_id=quote.evidence_id,
            pointer=quote.pointer,
            support=quote.support,
            value=quote.value,
            source_byte_start=quote.source_byte_start,
            value_byte_start=quote.value_byte_start,
        )
        for index, quote in enumerate(result)
    )
    if len({digest(q.identity()) for q in assigned}) != len(assigned):
        raise EvaluationError("duplicate_quote_identity")
    return assigned


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


def binding(value: AtomicMemoryExtractionInput, quotes: tuple[Quote, ...]) -> str:
    """Bind one quote set to its request; full identity, never exposed to the model."""
    return digest({"input": value.model_dump(mode="json"), "quotes": [q.identity() for q in quotes]})


def render(
    output: TemplateOutput, value: AtomicMemoryExtractionInput, quotes: tuple[Quote, ...], *, request_hash: str
) -> AtomicMemoryExtractionOutput:
    """Render against the mandatory originating request binding; semantic scoring is separate."""
    if binding(value, quotes) != request_hash:
        raise EvaluationError("quote_request_binding_mismatch")
    if len({q.quote_id for q in quotes}) != len(quotes):
        raise EvaluationError("duplicate_quote_id")
    available = {q.quote_id: q for q in quotes}
    supplied = {e.evidence_id for e in value.evidence}
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
        AtomicMemoryContent(kind=candidate.kind, text=text)
        candidates.append(AtomicMemoryCandidate(kind=candidate.kind, text=text, evidence_ids=candidate.evidence_ids))
    return AtomicMemoryExtractionOutput(candidates=tuple(candidates))


def cost_ledger(
    value: AtomicMemoryExtractionInput, quotes: tuple[Quote, ...], *, instructions: str = INSTRUCTIONS
) -> dict[str, Any]:
    """Decompose one request's estimated input into comparable components.

    Same estimator and canonical-JSON basis for v5/v6/v7; character estimates are
    kept separate from provider-reported tokens.
    """
    estimator = character_token_estimator()
    evidence_payload = value.model_dump(mode="json")["evidence"]
    index_payload = [q.index() for q in quotes]
    schema = TemplateOutput.model_json_schema()
    instructions_tokens = estimator.estimate(instructions)
    evidence_tokens = estimator.estimate(canonical({"evidence": evidence_payload}).decode())
    index_tokens = estimator.estimate(canonical({"quote_index": index_payload}).decode())
    schema_tokens = estimator.estimate(canonical(schema).decode())
    encoded = canonical(
        {
            "instructions": instructions,
            "input": {"evidence": evidence_payload, "quote_index": index_payload},
            "output_schema": schema,
        }
    )
    total = estimator.estimate(encoded.decode())
    # Attribute canonical framing and JSON escaping to the evidence envelope.
    evidence_tokens = total - instructions_tokens - index_tokens - schema_tokens
    return {
        "estimator": "character:weighted@1",
        "components": {
            "instructions": instructions_tokens,
            "evidence_body": evidence_tokens,
            "quote_index": index_tokens,
            "output_schema": schema_tokens,
        },
        "single_estimated_input_tokens": total,
        "serialized_request_bytes": len(encoded),
        "input_hash": digest({"evidence": evidence_payload, "quote_index": index_payload}),
        "schema_hash": digest(schema),
        "provider_wire_overhead_unknown": True,
    }
