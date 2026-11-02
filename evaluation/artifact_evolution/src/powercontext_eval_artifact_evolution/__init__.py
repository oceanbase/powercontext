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

"""Evaluation-owned recording and faithful replay for Artifact evolution."""

from powercontext_eval_artifact_evolution.bundle import (
    BundleIOError,
    BundleValidationError,
    load_bundle,
    record_attempt,
    write_bundle,
)
from powercontext_eval_artifact_evolution.models import (
    ArtifactEvolutionBundle,
    CandidateOutput,
    DecisionRequest,
    DreamRunRef,
    ProposalAttempt,
)
from powercontext_eval_artifact_evolution.recording import (
    DreamRecordingError,
    DreamRunSnapshot,
    decision_for_dream_run,
    record_dream_attempt,
    snapshot_from_dream_run,
)
from powercontext_eval_artifact_evolution.replay import (
    ReplayEngine,
    ReplayResult,
)

__all__ = [
    "ArtifactEvolutionBundle",
    "BundleIOError",
    "BundleValidationError",
    "CandidateOutput",
    "DecisionRequest",
    "DreamRecordingError",
    "DreamRunRef",
    "DreamRunSnapshot",
    "ProposalAttempt",
    "ReplayEngine",
    "ReplayResult",
    "decision_for_dream_run",
    "load_bundle",
    "record_attempt",
    "record_dream_attempt",
    "snapshot_from_dream_run",
    "write_bundle",
]
