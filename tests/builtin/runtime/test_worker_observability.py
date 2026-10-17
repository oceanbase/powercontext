# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from powercontext.builtin.runtime.processing_contracts import ArtifactProcessingModelUsageDelta
from powercontext.builtin.runtime.worker_observability import WorkerModelUsageCollector


def test_worker_model_usage_collector_leaves_missing_recorder_unobserved() -> None:
    assert WorkerModelUsageCollector().snapshot() is None


def test_worker_model_usage_collector_sums_independent_recorder_deltas_once() -> None:
    collector = WorkerModelUsageCollector()
    first, second = collector.observer(), collector.observer()

    first(2, 4, 1, 0)
    first(0, 4, 1, 2)
    second(0, 4, 3, 4)
    second(0, 4, 3, 4)

    assert collector.snapshot() == ArtifactProcessingModelUsageDelta(dropped_records=4, write_failures=6)
