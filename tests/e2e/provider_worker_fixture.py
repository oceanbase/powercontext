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

"""A picklable report producer independent of the parent Server test harness."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LargeProviderReport:
    ready: Path

    def __call__(self, _assignment):
        from powercontext.builtin.inference.observability import ProviderObservation, current_provider_observer
        from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingWorkerCompletion

        observer = current_provider_observer()
        assert observer is not None
        self.ready.touch()
        for code in range(200, 328):
            observer(ProviderObservation("http", "openai", "generation", "success", code / 1000, code, "initial"))
        return ArtifactProcessingWorkerCompletion()
