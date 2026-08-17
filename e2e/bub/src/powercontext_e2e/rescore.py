"""Offline replay rescoring without an execution-adapter dependency."""

from __future__ import annotations

from pathlib import Path

from .artifacts import write_artifacts
from .evaluation import MemoryEvaluator
from .models import TaskObservation
from .settings import HarnessSettings


def rescore_replay(replay_path: Path, output_dir: Path, settings: HarnessSettings) -> bool:
    observation = TaskObservation.model_validate_json(replay_path.read_text(encoding="utf-8"))
    report = MemoryEvaluator.evaluate(observation, experiment=f"offline:{observation.task.id}")
    write_artifacts(observation, report, output_dir, settings=settings)
    return report.accepted
