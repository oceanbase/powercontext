"""Deterministic candidate adapters for integration-owned task outcomes."""

from __future__ import annotations

from dataclasses import dataclass

from powercontext.memory.models import MemoryEntryInput
from powercontext.memory.protocols import MemoryCandidateRequest
from powercontext.sources import Source


@dataclass(frozen=True, slots=True)
class TaskOutcomeReport:
    """A structured final report supplied explicitly by an agent integration."""

    final_report: str = ""
    changed_paths: tuple[str, ...] = ()
    git_head: str | None = None
    verification: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class TaskOutcomeSource(Source):
    """A persisted task event whose report may support a working note."""

    report: TaskOutcomeReport


class WorkingNoteCandidatePipeline:
    """Mechanically render useful task outcomes without semantic inference."""

    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        candidates: list[MemoryEntryInput] = []
        for source in request.sources:
            if not isinstance(source, TaskOutcomeSource):
                continue
            text = _render_report(source.report)
            if text:
                candidates.append(MemoryEntryInput(kind="working_note", text=text, sources=(source,)))
        return tuple(candidates)


def _render_report(report: TaskOutcomeReport) -> str:
    lines: list[str] = []
    final_report = report.final_report.strip()
    changed_paths = tuple(value.strip() for value in report.changed_paths if value.strip())
    git_head = None if report.git_head is None else report.git_head.strip()
    verification = tuple(value.strip() for value in report.verification if value.strip())
    if final_report:
        lines.append(final_report)
    if changed_paths:
        lines.append(f"Changed paths: {', '.join(changed_paths)}")
    if git_head:
        lines.append(f"Git head: {git_head}")
    if verification:
        lines.append(f"Verification: {'; '.join(verification)}")
    return "\n".join(lines)
