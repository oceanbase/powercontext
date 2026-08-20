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

"""Temporary and durable Handoff domain contracts."""

from powercontext.builtin.artifacts.handoff.errors import (
    HandoffError,
    HandoffEvidenceUnavailableError,
    HandoffGenerationUnavailableError,
    HandoffScopeMismatchError,
    InvalidHandoffGenerationError,
    InvalidHandoffReferenceError,
)
from powercontext.builtin.artifacts.handoff.generation import (
    DefaultHandoffEvidenceProjector,
    HandoffEvidenceProjector,
    HandoffGenerationEvidenceInput,
    HandoffGenerationInput,
    HandoffGenerationOmission,
    HandoffGenerationOutput,
    HandoffGenerationStatement,
    LLMHandoffGenerationPipeline,
)
from powercontext.builtin.artifacts.handoff.models import (
    DEFAULT_HANDOFF_MAX_BYTES,
    MAX_HANDOFF_BYTES,
    MIN_HANDOFF_MAX_BYTES,
    ActivateHandoff,
    Handoff,
    HandoffActivation,
    HandoffActivationStatus,
    HandoffArtifactCitation,
    HandoffArtifactDraft,
    HandoffArtifactEvidence,
    HandoffAudience,
    HandoffCitation,
    HandoffClaim,
    HandoffContent,
    HandoffDisposition,
    HandoffDraft,
    HandoffEvidenceCheck,
    HandoffEvidenceStatus,
    HandoffGenerationEvidence,
    HandoffGenerationRequest,
    HandoffMemoryCitation,
    HandoffMemoryEvidence,
    HandoffOmission,
    HandoffResolution,
    HandoffResolutionSelection,
    HandoffResolutionStatus,
    HandoffSourceCitation,
    HandoffSourceEvidence,
    HandoffStatement,
    PreparedHandoff,
    PrepareHandoff,
)
from powercontext.builtin.artifacts.handoff.prompts import (
    HANDOFF_GENERATION_INSTRUCTIONS,
    HANDOFF_GENERATION_INSTRUCTIONS_VERSION,
)
from powercontext.builtin.artifacts.handoff.protocols import (
    HandoffBackend,
    HandoffEvidenceResolver,
    HandoffGenerationPipeline,
)
from powercontext.builtin.artifacts.handoff.service import HandoffService

__all__ = [
    "DEFAULT_HANDOFF_MAX_BYTES",
    "HANDOFF_GENERATION_INSTRUCTIONS",
    "HANDOFF_GENERATION_INSTRUCTIONS_VERSION",
    "MAX_HANDOFF_BYTES",
    "MIN_HANDOFF_MAX_BYTES",
    "ActivateHandoff",
    "DefaultHandoffEvidenceProjector",
    "Handoff",
    "HandoffActivation",
    "HandoffActivationStatus",
    "HandoffArtifactCitation",
    "HandoffArtifactDraft",
    "HandoffArtifactEvidence",
    "HandoffAudience",
    "HandoffBackend",
    "HandoffCitation",
    "HandoffClaim",
    "HandoffContent",
    "HandoffDisposition",
    "HandoffDraft",
    "HandoffError",
    "HandoffEvidenceCheck",
    "HandoffEvidenceProjector",
    "HandoffEvidenceResolver",
    "HandoffEvidenceStatus",
    "HandoffEvidenceUnavailableError",
    "HandoffGenerationEvidence",
    "HandoffGenerationEvidenceInput",
    "HandoffGenerationInput",
    "HandoffGenerationOmission",
    "HandoffGenerationOutput",
    "HandoffGenerationPipeline",
    "HandoffGenerationRequest",
    "HandoffGenerationStatement",
    "HandoffGenerationUnavailableError",
    "HandoffMemoryCitation",
    "HandoffMemoryEvidence",
    "HandoffOmission",
    "HandoffResolution",
    "HandoffResolutionSelection",
    "HandoffResolutionStatus",
    "HandoffScopeMismatchError",
    "HandoffService",
    "HandoffSourceCitation",
    "HandoffSourceEvidence",
    "HandoffStatement",
    "InvalidHandoffGenerationError",
    "InvalidHandoffReferenceError",
    "LLMHandoffGenerationPipeline",
    "PrepareHandoff",
    "PreparedHandoff",
]
