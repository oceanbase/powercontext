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

"""Locate separately published historical evaluation results."""

from __future__ import annotations

import os
from pathlib import Path

from .manifests import EvaluationError

RESULTS_ENV = "POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS"


def historical_results_root() -> Path:
    """Return the extracted results directory supplied by an opted-in caller."""

    configured = os.environ.get(RESULTS_ENV)
    if not configured:
        raise EvaluationError(f"historical_results_bundle_required:{RESULTS_ENV}")
    root = Path(configured).expanduser()
    if not root.is_dir():
        raise EvaluationError(f"historical_results_bundle_unavailable:{RESULTS_ENV}")
    return root
