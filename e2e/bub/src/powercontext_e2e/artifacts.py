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
