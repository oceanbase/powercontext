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

from .models import EvaluationReport, TaskObservation


def render_report(observation: TaskObservation, report: EvaluationReport) -> str:
    markdown = Markdown(renderer=MarkdownRenderer)
    document = block.Document()
    children: list[Element] = []
    children.extend(_nodes(markdown, "# PowerContext end-to-end Memory evaluation"))
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
    children.extend(_nodes(markdown, "## Memory evaluation"))
    children.append(block.BlankLine(0))
    children.extend(_nodes(markdown, f"```text\n{_evaluation_text(report)}\n```"))
    document.children = children
    return markdown.render(document)


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
