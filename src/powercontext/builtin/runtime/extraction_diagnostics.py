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

"""Current-process extraction observations without active model probes."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from powercontext.builtin.runtime.models import (
    ExtractionBackground,
    ExtractionFailure,
    ExtractionObservation,
    ExtractionStatus,
)
from powercontext.builtin.runtime.processing_contracts import processing_error_code

if TYPE_CHECKING:
    from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingSupervisors


class ExtractionDiagnostics:
    """Combine synchronous extraction and local worker observations."""

    def __init__(
        self,
        *,
        pipeline_configured: bool,
        external_worker: bool,
    ) -> None:
        self._pipeline_configured = pipeline_configured
        self._external_worker = external_worker
        self._since = datetime.now(UTC)
        self._last_failure: ExtractionFailure | None = None
        self._last_success_at: datetime | None = None

    def failed(self, error: Exception) -> None:
        code = processing_error_code(type(error))
        self._last_failure = ExtractionFailure(
            code=code,
            stage="flush" if code == "processing_failed" else "inference",
            occurred_at=datetime.now(UTC),
        )

    def succeeded(self) -> None:
        self._last_success_at = datetime.now(UTC)

    def snapshot(self, supervisors: ArtifactProcessingSupervisors | None) -> ExtractionStatus:
        worker = {} if supervisors is None else supervisors.family_status.get("memory", {})
        last_failure = self._last_failure
        last_success_at = self._last_success_at
        if worker.get("last_error_at"):
            worker_error_at = datetime.fromisoformat(str(worker["last_error_at"]))
            if last_failure is None or worker_error_at > last_failure.occurred_at:
                last_failure = ExtractionFailure.model_validate({
                    "code": worker["last_error"],
                    "stage": worker["last_error_stage"],
                    "occurred_at": worker_error_at,
                })
        if worker.get("last_success_at"):
            worker_success_at = datetime.fromisoformat(str(worker["last_success_at"]))
            if last_success_at is None or worker_success_at > last_success_at:
                last_success_at = worker_success_at
        if worker:
            running = worker["supervisor_running"]
            background = ExtractionBackground.model_validate({
                "location": "local",
                "role": worker["supervisor_role"] if running else None,
                "state": "stopped" if not running else "degraded" if worker["status"] == "degraded" else "running",
                "automatic_processing_enabled": worker["automatic_processing_enabled"],
            })
        elif self._external_worker:
            background = ExtractionBackground(location="external", state="unknown")
        else:
            background = ExtractionBackground(location="none", state="stopped", automatic_processing_enabled=False)
        return ExtractionStatus(
            configuration=(
                "unknown" if self._external_worker else "configured" if self._pipeline_configured else "unconfigured"
            ),
            background=background,
            observation=ExtractionObservation(
                status="unverified" if last_success_at is None and last_failure is None else "observed",
                since=self._since,
                last_success_at=last_success_at,
                last_failure=last_failure,
            ),
        )
