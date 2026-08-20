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
