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

"""Side-effect-free evaluation path layout."""

from dataclasses import dataclass
from pathlib import Path
from re import fullmatch

from powercontext_eval.models import Arm


@dataclass(frozen=True)
class EvaluationPaths:
    """Compute ephemeral and retained paths for one evaluation run."""

    root: Path
    run_id: str

    def __post_init__(self) -> None:
        if (
            not self.run_id
            or self.run_id in {".", ".."}
            or ".." in self.run_id
            or fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", self.run_id) is None
        ):
            raise ValueError(f"Unsafe run ID: {self.run_id!r}")

    @property
    def run_artifacts(self) -> Path:
        """Return the retained artifact directory for the run."""

        return self.root / "runs" / self.run_id

    def arm_work(self, arm: Arm) -> Path:
        """Return an arm's ephemeral work directory."""

        return self.root / "work" / self.run_id / arm.value

    def arm_artifacts(self, arm: Arm) -> Path:
        """Return an arm's retained artifact directory."""

        return self.run_artifacts / "arms" / arm.value
