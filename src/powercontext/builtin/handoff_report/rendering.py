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

"""Markdown rendering for Scope-based Handoff Reports."""

from __future__ import annotations

from powercontext.builtin.handoff_report.report import HandoffReport, ScopeHandoffReport


def render_markdown(report: HandoffReport, /) -> str:
    lines = [
        "# Handoff Report",
        "",
        f"Selection: `{report.selection.mode}`",
        f"Scopes: {len(report.scopes)}",
        "",
        "| Scope | Parent | Status | Handoff |",
        "| --- | --- | --- | --- |",
    ]
    for entry in report.scopes:
        parent = entry.scope.parent_scope_id or "—"
        reference = "—" if entry.handoff is None else _format_address(entry)
        lines.append(f"| {_cell(entry.scope.title)} | `{_cell(parent)}` | {entry.status} | {reference} |")

    for entry in report.scopes:
        lines.extend(_scope_section(entry))
    lines.extend(["", f"Selection digest: `{report.selection_digest}`", f"Report digest: `{report.report_digest}`", ""])
    return "\n".join(lines)


def _scope_section(entry: ScopeHandoffReport) -> list[str]:
    lines = ["", f"## {entry.scope.title}", "", entry.scope.summary]
    if entry.content is None:
        return [*lines, "", "No committed Handoff."]
    lines.extend(["", f"Status: **{entry.status}**", "", f"Objective: {entry.content.objective}", "", "Current state:"])
    lines.extend(f"- {statement.text}" for statement in entry.content.state)
    if entry.content.next_action is not None:
        lines.extend(["", f"Next action: {entry.content.next_action.text}"])
    if entry.content.omissions:
        lines.extend(["", "Known omissions:"])
        lines.extend(f"- {omission.text}" for omission in entry.content.omissions)
    return lines


def _format_address(entry: ScopeHandoffReport) -> str:
    handoff = entry.handoff
    if handoff is None:
        return "—"
    artifact = handoff.artifact
    return f"`{handoff.scope_id}/{artifact.family}/{artifact.artifact_id}@{artifact.revision}`"


def _cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")


__all__ = ["render_markdown"]
