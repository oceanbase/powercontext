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

"""The two allocation arms over one frozen candidate set.

The baseline arm is the production Builder itself, observed through its internal budget recorder. The candidate
arm, ``waterfill-v1``, exists only here: it reuses the production entry constructors, truncation primitive,
body quota, 64-byte truncation floor, caps and renderer, and changes only how bytes are shared out.

This module is the single adapter onto private production helpers; nothing in production imports it.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import chain
from typing import Literal

from powercontext.artifacts import ArtifactAddress
from powercontext.builtin.runtime.prepared_context import (
    _ELLIPSIS,
    _MIN_TRUNCATED_CONTENT_BYTES,
    PreparedContextBudgetRecord,
    PreparedContextBudgetTrace,
    PreparedContextBuild,
    PreparedContextBuilder,
    PreparedContextOrigin,
    _PreparedContextEntry,
    _render,
    _truncate_utf8,
)
from powercontext.builtin.runtime.prepared_text import (
    _MAX_BODY_BYTES,
    _MIN_TRUNCATED_BODY_BYTES,
    ContextTextItem,
    _body_chunks,
    render_context_text,
)

from .manifest import Case

Disposition = Literal[
    "full",
    "truncated",
    "dropped_below_min_bytes",
    "dropped_no_fitting_truncation",
    "dropped_minimum_infeasible",
    "entry_limit",
    "family_limit",
]
_DELIVERED = frozenset({"full", "truncated"})
_ELLIPSIS_BYTES = len(_ELLIPSIS.encode("utf-8"))


class HarnessError(RuntimeError):
    """The harness could not observe the production arm it compares against."""

    def __init__(self, case_id: str, problem: str) -> None:
        super().__init__(f"prepared-context budget harness ({case_id}): {problem}")


@dataclass(frozen=True)
class Form:
    """One deliverable body for a candidate."""

    content: str
    truncated: bool

    @property
    def size(self) -> int:
        return len(self.content.encode("utf-8"))


class DefaultForms:
    """Bodies the default JSON-envelope path can deliver for one candidate (``_fit_entry`` semantics)."""

    def __init__(self, text: str) -> None:
        self.display = text
        self._source_bytes = len(text.encode("utf-8"))
        self._quota = PreparedContextBuilder.max_entry_content_bytes
        self._upper = min(self._quota, self._source_bytes - 1)

    @property
    def display_bytes(self) -> int:
        return self._source_bytes

    def complete(self) -> Form:
        if self._source_bytes <= self._quota:
            return Form(self.display, truncated=False)
        return Form(_truncate_utf8(self.display, self._quota), truncated=True)

    def largest(self) -> Form:
        return self.complete()

    def at_most(self, limit: int) -> Form | None:
        complete = self.complete()
        if complete.size <= limit:
            return complete
        upper = min(limit, self._upper)
        if upper < _MIN_TRUNCATED_CONTENT_BYTES:
            return None
        form = Form(_truncate_utf8(self.display, upper), truncated=True)
        return form if form.size >= _MIN_TRUNCATED_CONTENT_BYTES else None

    def minimal(self) -> Form:
        complete = self.complete()
        for byte_budget in range(_MIN_TRUNCATED_CONTENT_BYTES, self._upper + 1):
            form = Form(_truncate_utf8(self.display, byte_budget), truncated=True)
            if form.size >= _MIN_TRUNCATED_CONTENT_BYTES:
                return form if form.size < complete.size else complete
        return complete

    def truncation_of_size(self, size: int) -> Form | None:
        if not _MIN_TRUNCATED_CONTENT_BYTES <= size <= self._upper:
            return None
        form = Form(_truncate_utf8(self.display, size), truncated=True)
        return form if form.size == size else None

    def is_production_form(self, form: Form) -> bool:
        if not form.truncated:
            return form.content == self.display and self._source_bytes <= self._quota
        expected = self.truncation_of_size(form.size)
        return expected is not None and expected.content == form.content


class TextForms:
    """Bodies the explicit text-assembly path can deliver for one candidate (``fit_context_text_item`` semantics)."""

    def __init__(self, content: str) -> None:
        self._chunks = _body_chunks(content)
        self.display = "".join(self._chunks)
        self._prefix = [0]
        for chunk in self._chunks:
            self._prefix.append(self._prefix[-1] + len(chunk.encode("utf-8")))

    @property
    def display_bytes(self) -> int:
        return self._prefix[-1]

    def complete(self) -> Form | None:
        return Form(self.display, truncated=False) if self.display_bytes <= _MAX_BODY_BYTES else None

    def largest(self) -> Form | None:
        return self.complete() or self.at_most(_MAX_BODY_BYTES)

    def _truncation(self, count: int) -> Form:
        return Form("".join(self._chunks[:count]) + _ELLIPSIS, truncated=True)

    def at_most(self, limit: int) -> Form | None:
        complete = self.complete()
        if complete is not None and complete.size <= limit:
            return complete
        count = min(
            bisect_right(self._prefix, min(limit, _MAX_BODY_BYTES) - _ELLIPSIS_BYTES) - 1, len(self._chunks) - 1
        )
        if count < 0:
            return None
        form = self._truncation(count)
        return form if form.size >= _MIN_TRUNCATED_BODY_BYTES else None

    def minimal(self) -> Form | None:
        complete = self.complete()
        count = bisect_left(self._prefix, _MIN_TRUNCATED_BODY_BYTES - _ELLIPSIS_BYTES)
        if count > len(self._chunks) - 1:
            return complete
        form = self._truncation(count)
        if complete is not None and complete.size <= form.size:
            return complete
        return form

    def truncation_of_size(self, size: int) -> Form | None:
        count = bisect_left(self._prefix, size - _ELLIPSIS_BYTES)
        if count > len(self._chunks) - 1 or self._prefix[count] != size - _ELLIPSIS_BYTES:
            return None
        return self._truncation(count)

    def is_production_form(self, form: Form) -> bool:
        if not form.truncated:
            return form.content == self.display and form.size <= _MAX_BODY_BYTES
        expected = self.truncation_of_size(form.size)
        return (
            expected is not None
            and expected.content == form.content
            and _MIN_TRUNCATED_BODY_BYTES <= form.size <= _MAX_BODY_BYTES
        )


Forms = DefaultForms | TextForms


@dataclass(frozen=True)
class Candidate:
    """One frozen candidate: a production budget record bound to the production entry it describes."""

    position: int
    record: PreparedContextBudgetRecord
    entry: _PreparedContextEntry
    forms: Forms
    family: str
    artifact_id: str

    @property
    def section(self) -> str:
        return "memory" if self.entry.kind == "atomic-memory" else self.entry.kind


@dataclass(frozen=True)
class Allocation:
    """What one arm delivers for each frozen candidate, in frozen order, and the final rendering."""

    dispositions: tuple[Disposition, ...]
    forms: tuple[Form | None, ...]
    content: str | None

    @property
    def content_bytes(self) -> int:
        return 0 if self.content is None else len(self.content.encode("utf-8"))


@dataclass(frozen=True)
class Baseline:
    build: PreparedContextBuild
    trace: PreparedContextBudgetTrace
    unrecorded: PreparedContextBuild


def run_baseline(case: Case) -> Baseline:
    """Run the production Builder twice: once with the internal recorder, once exactly as production does."""

    traces: list[PreparedContextBudgetTrace] = []
    build = _build(case, PreparedContextBuilder(budget_recorder=traces.append))
    if len(traces) != 1:
        raise HarnessError(case.id, f"expected one budget trace, observed {len(traces)}")
    return Baseline(build=build, trace=traces[0], unrecorded=_build(case, PreparedContextBuilder()))


def frozen_candidates(case: Case, trace: PreparedContextBudgetTrace) -> tuple[Candidate, ...]:
    """Bind each production budget record, in production order, to the production entry with that origin."""

    entries = _production_entries(case)
    candidates: list[Candidate] = []
    for position, record in enumerate(trace.records, start=1):
        entry = entries.get(_key(record.kind, record.origin))
        if entry is None:
            raise HarnessError(case.id, f"budget record {position} has no matching candidate")
        forms: Forms = DefaultForms(entry.content) if case.path == "default" else TextForms(entry.content)
        address = record.origin.artifact if isinstance(record.origin, ArtifactAddress) else record.origin
        candidates.append(
            Candidate(position, record, entry, forms, family=address.family, artifact_id=address.artifact_id)
        )
    return tuple(candidates)


def baseline_allocation(case: Case, candidates: Sequence[Candidate]) -> Allocation:
    """Rebuild the production delivery from the budget records alone and render it with the production renderer."""

    forms: list[Form | None] = []
    for candidate in candidates:
        record = candidate.record
        if record.disposition == "full":
            form = candidate.forms.complete()
            forms.append(form if form is not None and form.size == record.delivered_bytes else None)
        elif record.disposition == "truncated":
            forms.append(candidate.forms.truncation_of_size(record.delivered_bytes))
        else:
            forms.append(None)
    dispositions = tuple(candidate.record.disposition for candidate in candidates)
    return Allocation(dispositions=dispositions, forms=tuple(forms), content=render(case, candidates, forms))


def waterfill(case: Case, candidates: Sequence[Candidate]) -> Allocation:
    """``waterfill-v1``: honest minimums in frozen order, one uniform level, then a frozen-order top-up."""

    max_bytes = case.request.max_bytes
    chosen: dict[int, Form] = {}
    minimal: dict[int, Form] = {}
    dispositions: dict[int, Disposition] = {}

    def fits(forms: dict[int, Form]) -> bool:
        content = render(case, candidates, [forms.get(index) for index in range(len(candidates))])
        return content is None or len(content.encode("utf-8")) <= max_bytes

    for index, candidate in enumerate(candidates):
        cap = _cap_reason(case, candidate, [candidates[admitted] for admitted in chosen])
        form = candidate.forms.minimal()
        if cap is not None:
            dispositions[index] = cap
        elif form is not None and fits({**chosen, index: form}):
            chosen[index] = minimal[index] = form
        else:
            dispositions[index] = "dropped_minimum_infeasible"

    def leveled(level: int) -> dict[int, Form]:
        return {index: _at_least(candidates[index].forms, minimal[index], level) for index in chosen}

    ceiling = max((_largest_size(candidates[index].forms) for index in chosen), default=0)
    chosen = leveled(_largest(lambda level: fits(leveled(level)), 0, ceiling))
    for index in sorted(chosen):
        forms = candidates[index].forms
        limit = _largest(
            lambda size, index=index, forms=forms: fits({**chosen, index: _at_least(forms, minimal[index], size)}),
            chosen[index].size,
            _largest_size(forms),
        )
        chosen[index] = _at_least(forms, minimal[index], limit)
    for index, form in chosen.items():
        dispositions[index] = "truncated" if form.truncated else "full"
    ordered = [chosen.get(index) for index in range(len(candidates))]
    return Allocation(
        dispositions=tuple(dispositions[index] for index in range(len(candidates))),
        forms=tuple(ordered),
        content=render(case, candidates, ordered),
    )


def render(case: Case, candidates: Sequence[Candidate], forms: Sequence[Form | None]) -> str | None:
    """Render the delivered forms, in frozen order, with the production renderer of the case's path."""

    delivered = [(candidate, form) for candidate, form in zip(candidates, forms, strict=True) if form is not None]
    if not delivered:
        return None
    if case.request.assembly is None:
        return _render([
            _PreparedContextEntry(
                origin=candidate.entry.origin,
                kind=candidate.entry.kind,
                citation=candidate.entry.citation,
                content=form.content,
                truncated=form.truncated,
            )
            for candidate, form in delivered
        ])
    items = []
    for candidate, form in delivered:
        origin = candidate.entry.origin
        if not isinstance(origin, ArtifactAddress):
            raise HarnessError(case.id, "text assembly candidates need a scoped origin")
        items.append(
            ContextTextItem(
                artifact=origin, content=form.content, recall_rank=candidate.record.rank, truncated=form.truncated
            )
        )
    return render_context_text(items, case.request.assembly)


def allocation_checks(case: Case, candidates: Sequence[Candidate], allocation: Allocation) -> dict[str, bool]:
    """Validity checks every arm must pass: budget, caps, honest production forms and a faithful rendering."""

    delivered = [
        (candidate, form) for candidate, form in zip(candidates, allocation.forms, strict=True) if form is not None
    ]
    return {
        "within_max_bytes": allocation.content_bytes <= case.request.max_bytes,
        "within_caps": all(
            _cap_reason(case, candidate, [other for other, _ in delivered[:index]]) is None
            for index, (candidate, _) in enumerate(delivered)
        ),
        "production_forms": all(candidate.forms.is_production_form(form) for candidate, form in delivered),
        "dispositions_match_forms": all(
            (disposition in _DELIVERED) == (form is not None)
            and (form is None or (disposition == "truncated") == form.truncated)
            for disposition, form in zip(allocation.dispositions, allocation.forms, strict=True)
        ),
        "rendering_matches_forms": allocation.content == render(case, candidates, allocation.forms),
    }


def baseline_checks(
    case: Case, baseline: Baseline, candidates: Sequence[Candidate], allocation: Allocation
) -> dict[str, bool]:
    """Reconcile the production budget trace with everything else the same build reported."""

    build, trace = baseline.build, baseline.trace
    records = trace.records
    delivered = [record for record in records if record.disposition in _DELIVERED]
    omitted = {(kind, reason): total for kind, reason, total in build.omitted}
    attributable = {"below_min_bytes", "no_fitting_truncation", "entry_limit"}
    if case.path == "assembly":
        attributable.add("family_limit")
    by_reason = {
        "dropped_below_min_bytes": "below_min_bytes",
        "dropped_no_fitting_truncation": "no_fitting_truncation",
        "entry_limit": "entry_limit",
        "family_limit": "family_limit",
    }
    counted: dict[tuple[str, str], int] = {}
    for record in records:
        if record.disposition in by_reason:
            key = (record.kind, by_reason[record.disposition])
            counted[key] = counted.get(key, 0) + 1
    return {
        "recording_leaves_output_unchanged": build == baseline.unrecorded,
        "trace_describes_this_build": (
            trace.path == case.path
            and trace.max_bytes == case.request.max_bytes
            and trace.content_bytes == build.context.content_bytes
        ),
        "trace_rerenders_production_content": allocation.content == build.context.content,
        "delivered_origins_match": tuple(record.origin for record in delivered) == build.origins,
        "omission_counters_reconcile": (
            sum(record.disposition == "truncated" for record in records) == build.omissions.truncated_items
            and sum(record.disposition == "dropped_below_min_bytes" for record in records)
            == build.omissions.dropped_below_min_bytes
            and sum(record.disposition == "dropped_no_fitting_truncation" for record in records)
            == build.omissions.dropped_no_fitting_truncation
        ),
        "receipt_counters_reconcile": counted
        == {key: total for key, total in omitted.items() if key[1] in attributable},
        "fragments_reconcile": [
            (record.origin, record.kind, record.fragment_bytes, record.disposition == "truncated")
            for record in delivered
        ]
        == [
            (fragment.origin, fragment.kind, fragment.rendered_bytes, fragment.truncated)
            for fragment in build.fragments
        ],
        "record_bytes_reconcile": all(
            candidate.record.source_bytes == len(candidate.entry.content.encode("utf-8"))
            and candidate.record.display_bytes == candidate.forms.display_bytes
            and candidate.record.delivered_bytes == (0 if form is None else form.size)
            for candidate, form in zip(candidates, allocation.forms, strict=True)
        ),
    }


def _build(case: Case, builder: PreparedContextBuilder) -> PreparedContextBuild:
    return builder.build_scopes_result(
        request=case.request,
        current_scope_id=case.current_scope_id,
        memory_candidates=case.memory,
        topic_memory_hits=case.topic_memory,
        experience_candidates=case.experience,
        profile_candidates=case.profile,
    )


def _production_entries(case: Case) -> dict[tuple[str, str], _PreparedContextEntry]:
    builder = PreparedContextBuilder()
    assembly = case.request.assembly
    if assembly is None:

        def foreign(scope_id: str) -> str | None:
            return None if scope_id == case.current_scope_id else scope_id or None

        groups: list[Sequence[_PreparedContextEntry]] = [
            *(builder._memory_entries(group.hits, scope_id=foreign(group.scope_id)) for group in case.memory),
            builder._topic_memory_entries(case.topic_memory),
            *(builder._experience_entries(group.hits, scope_id=foreign(group.scope_id)) for group in case.experience),
        ]
    else:
        groups = [
            builder._section_entries(
                section,
                case.current_scope_id,
                case.memory,
                case.experience,
                case.profile,
                case.topic_memory,
                None,
            )
            for section in assembly.sections
        ]
    entries: dict[tuple[str, str], _PreparedContextEntry] = {}
    for entry in chain.from_iterable(groups):
        key = _key(entry.kind, entry.origin)
        if entries.setdefault(key, entry) != entry:
            raise HarnessError(case.id, "two candidates share one origin")
    return entries


def _key(kind: str, origin: PreparedContextOrigin) -> tuple[str, str]:
    return kind, origin.model_dump_json()


def _cap_reason(case: Case, candidate: Candidate, admitted: Sequence[Candidate]) -> Disposition | None:
    assembly = case.request.assembly
    if assembly is None:
        return "entry_limit" if len(admitted) >= PreparedContextBuilder.entry_limit else None
    limit = next(section.limit for section in assembly.sections if section.family == candidate.section)
    selected = sum(other.section == candidate.section for other in admitted)
    return "family_limit" if selected >= limit else None


def _at_least(forms: Forms, minimal: Form, level: int) -> Form:
    form = forms.at_most(max(minimal.size, level))
    return minimal if form is None else form


def _largest_size(forms: Forms) -> int:
    form = forms.largest()
    return 0 if form is None else form.size


def _largest(predicate: Callable[[int], bool], lower: int, upper: int) -> int:
    """Largest value in ``[lower, upper]`` satisfying a monotone predicate that holds at ``lower``."""

    best = lower
    while lower <= upper:
        middle = (lower + upper) // 2
        if predicate(middle):
            best, lower = middle, middle + 1
        else:
            upper = middle - 1
    return best
