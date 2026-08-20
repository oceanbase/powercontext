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

"""Framework-neutral model inference contracts.

Pydantic AI remains optional and is imported only from
``powercontext.builtin.inference.pydantic_ai``.
"""

from powercontext.builtin.inference.errors import (
    InferenceConfigurationError,
    InferenceError,
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)
from powercontext.builtin.inference.models import (
    EmbeddingResult,
    EmbeddingVector,
    GenerationResult,
    InferenceUsage,
)
from powercontext.builtin.inference.protocols import EmbeddingModel, StructuredGenerator
from powercontext.builtin.inference.tokens import TokenEstimator, TokenEstimatorProfile, character_token_estimator

__all__ = [
    "EmbeddingModel",
    "EmbeddingResult",
    "EmbeddingVector",
    "GenerationResult",
    "InferenceConfigurationError",
    "InferenceError",
    "InferenceTimeoutError",
    "InferenceUnavailableError",
    "InferenceUsage",
    "InvalidInferenceOutputError",
    "StructuredGenerator",
    "TokenEstimator",
    "TokenEstimatorProfile",
    "character_token_estimator",
]
