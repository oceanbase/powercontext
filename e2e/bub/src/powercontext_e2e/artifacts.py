"""Final redacted artifact sinks for workload observations and evaluations."""

from __future__ import annotations

from pathlib import Path

from .evidence import write_evaluation_report, write_evidence
from .models import EvaluationReport, TaskObservation
from .report import render_report
from .settings import HarnessSettings


def write_artifacts(
    observation: TaskObservation,
    report: EvaluationReport,
    output_dir: Path,
    *,
    settings: HarnessSettings,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    write_evidence(output_dir / "replay.json", observation.model_dump_json(by_alias=True, indent=2) + "\n", settings)
    write_evaluation_report(
        output_dir / "eval-report.json",
        report=report,
        settings=settings,
    )
    write_evidence(
        output_dir / "report.md",
        render_report(observation, report),
        settings,
    )
