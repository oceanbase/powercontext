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

"""Pure selection and rendering for bounded lifecycle bootstrap context."""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace

from powercontext.builtin.runtime.models import BootstrapContextItem
from powercontext.builtin.runtime.prepared_text import TRUST_POLICY

_BEGIN_MARKER = "BEGIN_POWERCONTEXT_BOOTSTRAP_CONTEXT_V1"
_END_MARKER = "END_POWERCONTEXT_BOOTSTRAP_CONTEXT_V1"
_MAX_ITEM_BODY_BYTES = 2000
_MIN_TRUNCATED_BODY_BYTES = 64
_TRUNCATION_MARKER = "…"


@dataclass(frozen=True)
class BootstrapCandidate:
    """Exact public metadata plus untrusted text used only for the current build."""

    item: BootstrapContextItem
    content: str


@dataclass(frozen=True)
class BootstrapBuild:
    content: str | None
    items: tuple[BootstrapContextItem, ...] = ()
    truncated: bool = False


def build_bootstrap_context(candidates: Sequence[BootstrapCandidate], max_bytes: int) -> BootstrapBuild:
    """Fit complete citations and the longest safe body prefixes into one UTF-8 budget."""

    included: list[BootstrapCandidate] = []
    omitted = False
    for candidate in candidates[:7]:
        fitted = _fit_candidate(included, candidate, max_bytes)
        if fitted is None:
            omitted = True
            continue
        included.append(fitted)
    if not included:
        return BootstrapBuild(content=None, truncated=omitted)
    content = _render(included)
    if len(content.encode("utf-8")) > max_bytes:
        raise RuntimeError("bootstrap renderer exceeded its byte budget")  # noqa: TRY003
    items = tuple(candidate.item for candidate in included)
    return BootstrapBuild(
        content=content,
        items=items,
        truncated=omitted or any(item.truncated for item in items),
    )


def _fit_candidate(
    included: Sequence[BootstrapCandidate],
    candidate: BootstrapCandidate,
    max_bytes: int,
) -> BootstrapCandidate | None:
    chunks = _body_chunks(candidate.content)
    capped = _longest_prefix(chunks, _MAX_ITEM_BODY_BYTES)
    complete_truncated = len(capped) < len(chunks)
    if complete_truncated:
        capped = _longest_prefix(chunks, _MAX_ITEM_BODY_BYTES - len(_TRUNCATION_MARKER.encode("utf-8")))
    complete = replace(
        candidate,
        item=candidate.item.model_copy(update={"truncated": complete_truncated}),
        content="".join(capped) + (_TRUNCATION_MARKER if complete_truncated else ""),
    )
    if _fits((*included, complete), max_bytes):
        return complete

    lower, upper = 0, len(capped)
    best: BootstrapCandidate | None = None
    while lower <= upper:
        middle = (lower + upper) // 2
        shortened = replace(
            candidate,
            item=candidate.item.model_copy(update={"truncated": True}),
            content="".join(capped[:middle]) + _TRUNCATION_MARKER,
        )
        if _fits((*included, shortened), max_bytes):
            best = shortened
            lower = middle + 1
        else:
            upper = middle - 1
    if best is None or len(best.content.encode("utf-8")) < _MIN_TRUNCATED_BODY_BYTES:
        return None
    return best


def _longest_prefix(chunks: Sequence[str], max_bytes: int) -> tuple[str, ...]:
    used = 0
    selected: list[str] = []
    for chunk in chunks:
        size = len(chunk.encode("utf-8"))
        if used + size > max_bytes:
            break
        selected.append(chunk)
        used += size
    return tuple(selected)


def _body_chunks(content: str) -> tuple[str, ...]:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").replace("\u2028", "\n").replace("\u2029", "\n")
    return tuple(
        _visible_escape(character)
        if character != "\n" and unicodedata.category(character) in {"Cc", "Cf"}
        else character
        for character in normalized
    )


def _visible_escape(character: str) -> str:
    codepoint = ord(character)
    return f"\\u{codepoint:04x}" if codepoint <= 0xFFFF else f"\\U{codepoint:08x}"


def _fits(candidates: Sequence[BootstrapCandidate], max_bytes: int) -> bool:
    return len(_render(candidates).encode("utf-8")) <= max_bytes


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=True)


def _render(candidates: Sequence[BootstrapCandidate]) -> str:
    parts = ["# PowerContext bootstrap context", TRUST_POLICY, _BEGIN_MARKER]
    for number, candidate in enumerate(candidates, start=1):
        item = candidate.item
        artifact = item.artifact
        title = "Committed Handoff" if item.kind == "handoff" else "Curated Memory"
        metadata = [
            f"Scope: {_quoted(item.scope_id)}",
            f"Artifact: family={_quoted(artifact.family)}, id={_quoted(artifact.artifact_id)}, revision={artifact.revision}",
        ]
        if item.entry_id is not None and item.entry_version_id is not None:
            metadata.append(f"Entry: id={_quoted(item.entry_id)}, version={_quoted(item.entry_version_id)}")
        metadata.extend((
            f"Content digest: {_quoted(item.content_digest)}",
            f"Truncated: {'yes' if item.truncated else 'no'}",
        ))
        parts.extend((
            f"## {title} {number}",
            "\n".join(f"    {line}" for line in metadata),
            "\n".join(f">     {line}" for line in candidate.content.split("\n")),
        ))
    parts.append(_END_MARKER)
    return "\n\n".join(parts)
