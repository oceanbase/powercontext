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

"""Render the human-readable evaluation report through Marko."""

from __future__ import annotations

from marko import Markdown, block
from marko.element import Element
from marko.md_renderer import MarkdownRenderer

from .models import (
    Arm,
    ArmSummary,
    EvaluationReport,
    Interval,
    MetricSummary,
    PairedAgent,
    PairedReport,
    PairedSummary,
    ServerUsageSummary,
    TaskObservation,
)


def render_report(observation: TaskObservation, report: EvaluationReport) -> str:
    markdown = Markdown(renderer=MarkdownRenderer)
    document = block.Document()
    children: list[Element] = []
    children.extend(_nodes(markdown, "# PowerContext end-to-end evaluation"))
    children.append(block.BlankLine(0))
    children.extend(
        _nodes(
            markdown,
            "\n".join((
                f"- Workload: `{observation.task.id}`",
                f"- Execution adapter: `{observation.task.execution.type}`",
                f"- Harbor dataset: `{observation.task.dataset.name or observation.task.dataset.path}`",
                f"- Collection status: `{observation.status}`",
                f"- Native task outcome: `{_task_outcome(report)}` (diagnostic only)",
            )),
        )
    )
    children.append(block.BlankLine(0))
    children.extend(_nodes(markdown, "## Harbor reward"))
    children.append(block.BlankLine(0))
    reward_lines = (
        "\n".join(f"- `{name}`: `{value}`" for name, value in sorted(observation.harbor.rewards.items()))
        or "- No native reward was recorded."
    )
    children.extend(_nodes(markdown, reward_lines))
    children.append(block.BlankLine(0))
    children.extend(_nodes(markdown, "## Evaluation"))
    children.append(block.BlankLine(0))
    children.extend(_nodes(markdown, f"```text\n{_evaluation_text(report)}\n```"))
    document.children = children
    return markdown.render(document)


def render_evaluation_summary(report: EvaluationReport) -> str:
    markdown = Markdown(renderer=MarkdownRenderer)
    document = block.Document()
    document.children = [
        *_nodes(markdown, "# PowerContext end-to-end evaluation"),
        block.BlankLine(0),
        *_nodes(markdown, f"```text\n{_evaluation_text(report)}\n```"),
    ]
    return markdown.render(document)


def render_paired_report(report: PairedReport) -> str:
    markdown = Markdown(renderer=MarkdownRenderer)
    document = block.Document()
    children: list[Element] = [
        *_nodes(markdown, "# PowerContext OFF/ON comparison"),
        block.BlankLine(0),
        *_nodes(
            markdown,
            f"Preliminary: {report.trials} trial(s) per arm. Intervals are 95%: a Wilson score interval for each "
            "arm's success rate and a seeded percentile bootstrap over scored pairs for ON minus OFF. Errors and ON "
            "runs that did not receive PowerContext's treatment are counted but left out of success rates, paired "
            "differences, and step metrics; timed-out runs stay in all three. Step metrics are the host's own usage "
            "figures as Harbor reports them. Each figure is a mean over the runs that reported it: n/a when none did, "
            "and followed by its count, as in `1,000 (1 of 2 runs)`, when only some did.",
        ),
        block.BlankLine(0),
        *_nodes(markdown, _agent_text(report.agent)),
    ]
    for title, summary in (
        *((f"`{task.task_id}`", task) for task in report.tasks),
        ("All workloads", report.total),
    ):
        children.extend((block.BlankLine(0), *_nodes(markdown, f"## {title}"), block.BlankLine(0)))
        children.extend(_nodes(markdown, _paired_summary_text(summary)))
        if table := _step_table(summary):
            children.extend((block.BlankLine(0), *_nodes(markdown, table)))
        children.extend((block.BlankLine(0), *_nodes(markdown, _server_text(summary.on.server))))
    document.children = children
    return markdown.render(document)


def _agent_text(agent: PairedAgent) -> str:
    settings = "".join(f", {name} `{value}`" for name, value in sorted(agent.settings.items()))
    return f"Host `{agent.host}` {agent.version}, model `{agent.model or 'not set'}`{settings}."


def _paired_summary_text(summary: PairedSummary) -> str:
    delta = "n/a" if summary.mean_delta is None else f"{summary.mean_delta:+.2f}{_interval(summary.delta_interval)}"
    return "\n".join((
        f"- OFF: {_arm_text(summary.off)}",
        f"- ON: {_arm_text(summary.on)}",
        f"- Scored pairs: {summary.pairs}; ON minus OFF: {delta}; ON better in {summary.on_better}, "
        f"OFF better in {summary.off_better}, tied in {summary.tied}",
    ))


def _arm_text(arm: ArmSummary) -> str:
    rate = "" if arm.success_rate is None else f", {arm.success_rate:.0%}{_interval(arm.success_rate_interval, '.0%')}"
    return (
        f"{arm.passed}/{arm.scored} passed{rate} ({arm.timeouts} timed out); "
        f"{arm.errors} error(s), {arm.integration_failures} integration failure(s) not scored"
    )


def _interval(interval: Interval | None, spec: str = "+.2f") -> str:
    return "" if interval is None else f" [{interval.low:{spec}}, {interval.high:{spec}}]"


def _step_table(summary: PairedSummary) -> str:
    arms: tuple[tuple[Arm, ArmSummary], ...] = (("off", summary.off), ("on", summary.on))
    names = dict.fromkeys(name for _, arm in arms for name in arm.steps)
    if not names:
        return ""
    rows = [
        "| Step | Arm | Runs | Seconds | Input tokens | Cached input tokens | Output tokens | Cost USD |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name in names:
        for arm, arm_summary in arms:
            if (step := arm_summary.steps.get(name)) is None:
                continue
            cells = (
                _figure(step.seconds, step.runs, ".1f", spread=True),
                _figure(step.input_tokens, step.runs, ",.0f"),
                _figure(step.cache_tokens, step.runs, ",.0f"),
                _figure(step.output_tokens, step.runs, ",.0f"),
                _figure(step.cost_usd, step.runs, ".4f"),
            )
            rows.append(f"| {name} | {arm.upper()} | {step.runs} | {' | '.join(cells)} |")
    return "\n".join(rows)


def _figure(metric: MetricSummary | None, runs: int, spec: str, *, spread: bool = False) -> str:
    if metric is None:
        return "n/a"
    notes = [f"{metric.min:{spec}}-{metric.max:{spec}}"] if spread else []
    if metric.runs < runs:
        notes.append(f"{metric.runs} of {runs} runs")
    return f"{metric.mean:{spec}}" + (f" ({'; '.join(notes)})" if notes else "")


def _server_text(server: ServerUsageSummary | None) -> str:
    if server is None:
        return "Server usage: no scored ON run has a Scope snapshot."
    return (
        f"Server usage, mean over {server.runs} scored ON run(s): generation {server.generation_requests:.1f} "
        f"request(s), input tokens {_figure(server.generation_input_tokens, server.runs, ',.0f')}, output tokens "
        f"{_figure(server.generation_output_tokens, server.runs, ',.0f')}; embedding {server.embedding_requests:.1f} "
        f"request(s), input tokens {_figure(server.embedding_input_tokens, server.runs, ',.0f')}; "
        f"{server.recalled_tokens:,.0f} estimated tokens of context returned."
    )


def _nodes(markdown: Markdown, source: str) -> list[Element]:
    return list(markdown.parse(source).children)


def _task_outcome(report: EvaluationReport) -> str:
    value = report.cases[0].labels.get("task_outcome")
    return str(value.value) if value is not None else "unscored"


def _evaluation_text(report: EvaluationReport) -> str:
    lines: list[str] = []
    for case in report.cases:
        lines.append(case.name)
        for name, result in case.assertions.items():
            status = "PASS" if result.value else "FAIL"
            reason = f" — {result.reason}" if result.reason else ""
            lines.append(f"  [{status}] {name}{reason}")
        for name, result in case.scores.items():
            lines.append(f"  [SCORE] {name}: {result.value}")
        for name, result in case.labels.items():
            lines.append(f"  [LABEL] {name}: {result.value}")
    return "\n".join(lines)
