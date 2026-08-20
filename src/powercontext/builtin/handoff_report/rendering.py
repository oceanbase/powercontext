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

"""Deterministic Markdown rendering for canonical Handoff Reports."""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from unicodedata import category

from powercontext.builtin.handoff_report.canonical import finalize_digests
from powercontext.builtin.handoff_report.models import ExternalReference, ReportActivityEvent
from powercontext.builtin.handoff_report.report import HandoffReport, WorkstreamReport

_LABELS = {
    "zh-CN": {
        "title": "PowerContext 项目交接报告",
        "overview": "项目概览",
        "blockers": "阻塞事项",
        "workstreams": "Workstream 状态",
        "details": "Workstream 详情",
        "objective": "目标",
        "progress": "当前进度",
        "next": "下一步",
        "omissions": "缺失信息",
        "activities": "观察到的 Activity",
        "unassigned_activities": "未分配 Activity",
        "event": "事件",
        "schema": "Schema",
        "event_id": "事件 ID",
        "project_id": "Project ID",
        "source": "来源",
        "source_event_id": "来源事件 ID",
        "scope": "Scope",
        "time_basis": "时间依据",
        "occurred_at": "发生时间",
        "observed_at": "观察时间",
        "event_title": "标题",
        "event_summary": "摘要",
        "source_ref": "来源引用",
        "agent": "Agent",
        "session": "Session",
        "vcs": "VCS 上下文",
        "evidence": "证据引用",
        "evidence_checks": "Evidence 检查",
        "revision_history": "Handoff Revision 历史",
        "revision_history_summary": "共 {total} 个 Revision，显示最近 {shown} 个。",  # noqa: RUF001
        "revision_state_count": "状态条目",
        "revision_omission_count": "缺失条目",
        "continuity": "连续性时间线",
        "transfer_state": "交接状态",
        "outcome_state": "结果状态",
        "journal_order_notice": "按 Source journal 的稳定位置排序；位置表示先后顺序，不代表时间戳。",  # noqa: RUF001
        "invalid_work_records": "无法读取的 Work 记录",
        "metadata": "报告元数据",
        "selection_digest": "Selection Digest",
        "report_digest": "Report Digest",
        "report_kind": "报告类型",
        "period": "报告周期",
        "period_comparison": "与前一周期对比",
        "current_activity_count": "本周期 Activity 数",
        "previous_activity_count": "前一周期 Activity 数",
        "activity_delta": "Activity 变化",
        "handoff_boundary_coverage": "Handoff 时间边界覆盖",
        "format": "格式",
        "trust": "信任标记",
        "none": "无",
        "activity_notice": "Activity Adapter 未配置；此处不能解释为没有活动。",  # noqa: RUF001
    },
    "en": {
        "title": "PowerContext Project Handoff Report",
        "overview": "Project Overview",
        "blockers": "Blockers",
        "workstreams": "Workstream Status",
        "details": "Workstream Details",
        "objective": "Objective",
        "progress": "Current Progress",
        "next": "Next Action",
        "omissions": "Omissions",
        "activities": "Observed Activity",
        "unassigned_activities": "Unassigned Activity",
        "event": "Event",
        "schema": "Schema",
        "event_id": "Event ID",
        "project_id": "Project ID",
        "source": "Source",
        "source_event_id": "Source Event ID",
        "scope": "Scope",
        "time_basis": "Time Basis",
        "occurred_at": "Occurred At",
        "observed_at": "Observed At",
        "event_title": "Title",
        "event_summary": "Summary",
        "source_ref": "Source Reference",
        "agent": "Agent",
        "session": "Session",
        "vcs": "VCS Context",
        "evidence": "Evidence References",
        "evidence_checks": "Evidence Checks",
        "revision_history": "Handoff Revision History",
        "revision_history_summary": "{total} Revisions total. Showing the latest {shown}.",
        "revision_state_count": "State Items",
        "revision_omission_count": "Omissions",
        "continuity": "Continuity Timeline",
        "transfer_state": "Transfer State",
        "outcome_state": "Outcome State",
        "journal_order_notice": "Ordered by stable Source journal position; positions show sequence, not timestamps.",
        "invalid_work_records": "Unreadable Work Records",
        "metadata": "Report Metadata",
        "selection_digest": "Selection Digest",
        "report_digest": "Report Digest",
        "report_kind": "Report Kind",
        "period": "Report Period",
        "period_comparison": "Previous Period Comparison",
        "current_activity_count": "Current Activity Count",
        "previous_activity_count": "Previous Activity Count",
        "activity_delta": "Activity Delta",
        "handoff_boundary_coverage": "Handoff Boundary Coverage",
        "format": "Format",
        "trust": "Trust",
        "none": "None",
        "activity_notice": "Activity adapters are not configured; this does not mean that no activity occurred.",
    },
}


def render_markdown(report: HandoffReport, /) -> str:
    """Render one stable human projection without invoking a model or parsing Markdown input."""

    projection = finalize_digests(report.model_copy(update={"format": "markdown", "renderer_version": "markdown-v1"}))
    labels = _LABELS[projection.locale]
    lines = _front_matter_lines(projection)
    lines.extend([
        "---",
        "",
        f"# {labels['title']}",
        "",
        f"## {labels['overview']}",
        "",
        f"- Project: {_text(projection.project.title)}",
        f"- Workstreams: {projection.coverage.selected_workstreams}",
        f"- Missing Handoff: {projection.coverage.missing_handoff_workstreams}",
        f"- Continuable: {projection.summary.continuable_count}",
        f"- Blocked: {projection.summary.blocked_count}",
        f"- Complete: {projection.summary.complete_count}",
        f"- No Handoff: {projection.summary.no_handoff_count}",
    ])
    if projection.coverage.activity_coverage == "not_configured":
        lines.extend((f"- {_text(labels['activity_notice'])}", ""))
    else:
        lines.append("")
    if projection.normalized_period is not None:
        lines.extend((
            f"## {labels['period']}",
            "",
            f"- Start: {_code_span(str(projection.normalized_period['start']))}",
            f"- End: {_code_span(str(projection.normalized_period['end']))}",
            f"- Timezone: {_code_span(str(projection.normalized_period['timezone']))}",
            "",
        ))
    if projection.period_comparison is not None:
        comparison = projection.period_comparison
        lines.extend((
            f"## {labels['period_comparison']}",
            "",
            f"- {labels['current_activity_count']}: {comparison.current_activity_count}",
            f"- {labels['previous_activity_count']}: {comparison.previous_activity_count}",
            f"- {labels['activity_delta']}: {comparison.activity_delta:+d}",
            f"- {labels['handoff_boundary_coverage']}: {_code_span(comparison.handoff_boundary_coverage)}",
            "",
        ))
    lines.extend((f"## {labels['blockers']}", ""))
    blockers = tuple(item for item in projection.workstreams if item.work_status == "blocked")
    if blockers:
        lines.extend(
            f"- {_text(item.workstream.title)} ({_code_span(item.workstream.scope_id)}): "
            f"{_code_span(item.reporting_status)}"
            for item in blockers
        )
    else:
        lines.extend((labels["none"], ""))
    lines.extend((f"## {labels['workstreams']}", ""))
    lines.extend(
        f"- {_text(item.workstream.title)} ({_code_span(item.workstream.scope_id)}): "
        f"{_code_span(item.work_status)} / {_code_span(item.reporting_status)}"
        for item in projection.workstreams
    )
    lines.extend(("", f"## {labels['details']}", ""))
    for item in projection.workstreams:
        lines.extend(_render_workstream(item, labels))
    lines.extend((f"## {labels['unassigned_activities']}", ""))
    if projection.unassigned_activity:
        for event in projection.unassigned_activity:
            lines.extend(_render_activity(event, labels))
    else:
        lines.extend((labels["none"], ""))
    lines.extend((f"## {labels['metadata']}", ""))
    lines.extend((
        f"- {labels['selection_digest']}: {_code_span(projection.selection_digest or labels['none'])}",
        f"- {labels['report_digest']}: {_code_span(projection.report_digest or labels['none'])}",
        f"- {labels['report_kind']}: {_code_span(projection.report_kind)}",
        f"- {labels['format']}: {_code_span(projection.format)}",
        f"- {labels['trust']}: {_code_span(projection.trust)}",
    ))
    return "\n".join(lines).rstrip() + "\n"


def _render_workstream(item: WorkstreamReport, labels: dict[str, str]) -> list[str]:
    lines = [f"### {_text(item.workstream.title)} ({_code_span(item.workstream.scope_id)})", ""]
    if item.content is None:
        lines.extend((f"#### {labels['objective']}", "", labels["none"], ""))
    else:
        lines.extend((f"#### {labels['objective']}", "", _text(item.content.objective), ""))
        lines.extend((f"#### {labels['progress']}", ""))
        lines.extend(f"- {_text(statement.text)}" for statement in item.content.state)
        lines.extend(("", f"#### {labels['next']}", ""))
        lines.append(labels["none"] if item.content.next_action is None else _text(item.content.next_action.text))
        lines.extend(("", f"#### {labels['omissions']}", ""))
        if item.content.omissions:
            lines.extend(f"- {_text(omission.text)}" for omission in item.content.omissions)
        else:
            lines.append(labels["none"])
        lines.append("")
        lines.extend((f"#### {labels['evidence_checks']}", ""))
        if item.evidence_checks == "not_checked":
            evidence_state = "not_checked"
            if item.evidence_unavailable:
                evidence_state = "not_checked (adapter unavailable)"
            lines.extend((_code_span(evidence_state), ""))
        elif item.evidence_checks:
            lines.extend(f"- {_code_span(check.claim)}: {_code_span(check.status)}" for check in item.evidence_checks)
            lines.append("")
    lines.extend(_render_revision_history(item, labels))
    continuity = item.continuity
    lines.extend((f"#### {labels['continuity']}", ""))
    lines.extend((
        f"- {labels['transfer_state']}: {_code_span(continuity.coverage.transfer_state)}",
        f"- {labels['outcome_state']}: {_code_span(continuity.coverage.outcome_state)}",
        f"- {labels['invalid_work_records']}: {continuity.invalid_record_count}",
        f"- {_text(labels['journal_order_notice'])}",
    ))
    if continuity.events:
        for event in continuity.events:
            detail = event.summary or event.actor or labels["none"]
            lines.append(
                f"- {_code_span(f'#{event.position}')} {_code_span(event.kind)} / "
                f"{_code_span(event.status)}: {_text(detail)}"
            )
    else:
        lines.append(labels["none"])
    lines.append("")
    lines.extend((f"#### {labels['activities']}", ""))
    if item.activities:
        for event in item.activities:
            lines.extend(_render_activity(event, labels))
    else:
        lines.extend((labels["none"], ""))
    return lines


def _render_revision_history(item: WorkstreamReport, labels: dict[str, str]) -> list[str]:
    lines = [f"#### {labels['revision_history']}", ""]
    if item.handoff_history:
        lines.extend((
            _text(
                labels["revision_history_summary"].format(
                    total=item.handoff_revision_count,
                    shown=len(item.handoff_history),
                )
            ),
            "",
        ))
        for revision in reversed(item.handoff_history):
            reference = revision.reference
            lines.append(
                f"- {_code_span(f'@{reference.revision}')} {_code_span(revision.disposition)}: "
                f"{_text(revision.objective_excerpt)}"
            )
            lines.append(
                f"  - {labels['revision_state_count']}: {revision.state_count}; "
                f"{labels['revision_omission_count']}: {revision.omission_count}"
            )
            if revision.next_action_excerpt is not None:
                lines.append(f"  - {labels['next']}: {_text(revision.next_action_excerpt)}")
    else:
        lines.append(labels["none"])
    lines.append("")
    return lines


def _render_activity(event: ReportActivityEvent, labels: dict[str, str]) -> list[str]:
    lines = [f"- **{labels['event']}** {_code_span(event.event_id)}"]
    lines.extend((
        f"  - {labels['schema']}: {_code_span(event.schema_version)}",
        f"  - {labels['event_id']}: {_code_span(event.event_id)}",
        f"  - {labels['project_id']}: {_code_span(event.project_id)}",
        f"  - {labels['source']}: {_code_span(event.source)}",
        f"  - {labels['source_event_id']}: {_code_span(event.source_event_id)}",
        f"  - {labels['scope']}: {_optional_code(event.scope_id, labels)}",
        f"  - {labels['time_basis']}: {_code_span(event.time_basis)}",
        f"  - {labels['occurred_at']}: {_optional_timestamp(event.occurred_at, labels)}",
        f"  - {labels['observed_at']}: {_code_span(event.observed_at.isoformat())}",
        f"  - {labels['event_title']}: {_optional_text(event.title, labels)}",
        f"  - {labels['event_summary']}: {_optional_text(event.summary, labels)}",
        f"  - {labels['source_ref']}: {_optional_reference(event.source_ref, labels)}",
        f"  - {labels['agent']}: {_agent(event, labels)}",
        f"  - {labels['session']}: {_optional_code(event.session_id, labels)}",
        f"  - {labels['vcs']}: {_vcs(event, labels)}",
        f"  - {labels['trust']}: {_code_span(event.trust)}",
        f"  - {labels['evidence']}:",
    ))
    if event.evidence_refs:
        lines.extend(f"    - {_reference(reference)}" for reference in event.evidence_refs)
    else:
        lines.append(f"    - {labels['none']}")
    lines.append("")
    return lines


def _front_matter_lines(report: HandoffReport) -> list[str]:
    lines = [
        "---",
        "schema: powercontext.handoff-report.v1",
        f"locale: {report.locale}",
        "format: markdown",
        f"project_id: {_yaml_string(report.project.project_id)}",
        f"project_key: {_yaml_string(report.project.project_key)}",
        f"project_version: {report.project.version}",
        f"report_kind: {report.report_kind}",
        f"selection_digest: {_yaml_string(report.selection_digest or '')}",
        f"report_digest: {_yaml_string(report.report_digest or '')}",
        f"generated_at: {_yaml_string(report.generated_at.isoformat())}",
        f"trust: {report.trust}",
        f"selection_consistency: {report.selection_consistency}",
        f"activity_cursor: {report.activity_cursor}",
    ]
    if report.end_selection:
        lines.append("end_selection:")
        for entry in report.end_selection:
            lines.extend((
                f"  - scope_id: {_yaml_string(entry.scope_id)}",
                f"    workstream_revision: {entry.workstream_revision}",
                f"    status: {entry.status}",
            ))
            if entry.handoff_ref is None:
                lines.append("    handoff_ref: null")
            else:
                lines.extend((
                    "    handoff_ref:",
                    f"      family: {_yaml_string(entry.handoff_ref.family)}",
                    f"      artifact_id: {_yaml_string(entry.handoff_ref.artifact_id)}",
                    f"      revision: {entry.handoff_ref.revision}",
                ))
    else:
        lines.append("end_selection: []")
    if report.activity_selection:
        lines.append("activity_selection:")
        lines.extend(f"  - {_yaml_string(event_id)}" for event_id in report.activity_selection)
    else:
        lines.append("activity_selection: []")
    return lines


def _agent(event: ReportActivityEvent, labels: dict[str, str]) -> str:
    if event.agent is None:
        return labels["none"]
    values = tuple(value for value in (event.agent.provider, event.agent.label) if value is not None)
    return " / ".join(_code_span(value) for value in values)


def _vcs(event: ReportActivityEvent, labels: dict[str, str]) -> str:
    if event.vcs_context is None:
        return labels["none"]
    values = tuple(value for value in (event.vcs_context.branch, event.vcs_context.head_revision) if value is not None)
    return " / ".join(_code_span(value) for value in values)


def _optional_reference(reference: ExternalReference | None, labels: dict[str, str]) -> str:
    return labels["none"] if reference is None else _reference(reference)


def _reference(reference: ExternalReference) -> str:
    values = [_code_span(reference.kind), _code_span(reference.provider), _code_span(reference.external_id)]
    if reference.url is not None:
        values.append(_code_span(reference.url))
    return " / ".join(values)


def _optional_text(value: str | None, labels: dict[str, str]) -> str:
    return labels["none"] if value is None else _text(value)


def _optional_code(value: str | None, labels: dict[str, str]) -> str:
    return labels["none"] if value is None else _code_span(value)


def _optional_timestamp(value: datetime | None, labels: dict[str, str]) -> str:
    return labels["none"] if value is None else _code_span(value.isoformat())


def _collapse_lines(value: str) -> str:
    flattened = " ".join(value.splitlines())
    return "".join(" " if category(character) == "Cc" else character for character in flattened)


def _text(value: str) -> str:
    escaped_html = html.escape(_collapse_lines(value), quote=True)
    return re.sub(r"([\\`*_{}\[\]()#+\-.!|>~])", r"\\\1", escaped_html)


def _code_span(value: str) -> str:
    escaped_html = html.escape(_collapse_lines(value), quote=True)
    runs = tuple(len(match.group(0)) for match in re.finditer(r"`+", escaped_html))
    delimiter = "`" * (max(runs, default=0) + 1)
    if runs:
        return f"{delimiter} {escaped_html} {delimiter}"
    return f"{delimiter}{escaped_html}{delimiter}"


def _yaml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


__all__ = ["render_markdown"]
