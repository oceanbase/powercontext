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

from powercontext.builtin.runtime.models import ExtractionStatus
from powercontext.builtin.runtime.processing_contracts import processing_error_code

if TYPE_CHECKING:
    from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingSupervisors


class ExtractionDiagnostics:
    """Combine synchronous extraction and local worker observations."""

    def __init__(
        self,
        *,
        model_configured: bool,
        external_worker: bool,
    ) -> None:
        self._model_configured = model_configured
        self._external_worker = external_worker
        self._last_error: str | None = None
        self._last_error_at: datetime | None = None
        self._last_success_at: datetime | None = None

    def failed(self, error: Exception) -> None:
        self._last_error = processing_error_code(type(error).__name__)
        self._last_error_at = datetime.now(UTC)

    def succeeded(self) -> None:
        self._last_success_at = datetime.now(UTC)

    def snapshot(self, supervisors: ArtifactProcessingSupervisors | None) -> ExtractionStatus:
        worker = {} if supervisors is None else supervisors.family_status.get("memory", {})
        last_error, last_error_at = self._last_error, self._last_error_at
        last_success_at = self._last_success_at
        if worker.get("last_error_at"):
            worker_error_at = datetime.fromisoformat(str(worker["last_error_at"]))
            if last_error_at is None or worker_error_at > last_error_at:
                last_error, last_error_at = str(worker["last_error"]), worker_error_at
        if worker.get("last_success_at"):
            worker_success_at = datetime.fromisoformat(str(worker["last_success_at"]))
            if last_success_at is None or worker_success_at > last_success_at:
                last_success_at = worker_success_at
        last_result = "unverified"
        if last_error_at is not None:
            last_result = "failed"
        if last_success_at is not None and (last_error_at is None or last_success_at > last_error_at):
            last_result = "succeeded"
        worker_status = worker.get("status", "external" if self._external_worker else "disabled")
        if worker and not worker["supervisor_running"]:
            worker_status = "stopped"
        return ExtractionStatus.model_validate({
            "model_configured": self._model_configured,
            "worker_status": worker_status,
            "automatic_processing_enabled": worker.get(
                "automatic_processing_enabled", None if self._external_worker else False
            ),
            "last_result": last_result,
            "last_error": last_error,
            "last_error_at": last_error_at,
            "last_success_at": last_success_at,
        })
