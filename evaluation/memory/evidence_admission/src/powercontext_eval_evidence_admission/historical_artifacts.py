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

import errno
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .manifests import EvaluationError

RESULTS_ENV = "POWERCONTEXT_EVIDENCE_ADMISSION_RESULTS"
REVIEWS_ENV = "POWERCONTEXT_EVIDENCE_ADMISSION_REVIEWS"
HUMAN_SAMPLE_DECLARATION_SHA256 = "0febe93341dd28294cca698f009891badacdd2cff9fb50be8cccfa99d44e9dbf"


def historical_results_root() -> Path:
    """Return the extracted results directory supplied by an opted-in caller."""

    configured = os.environ.get(RESULTS_ENV)
    if not configured:
        raise EvaluationError(f"historical_results_bundle_required:{RESULTS_ENV}")
    root = Path(configured).expanduser()
    if not root.is_dir():
        raise EvaluationError(f"historical_results_bundle_unavailable:{RESULTS_ENV}")
    return root


def read_review_receipt(name: str, expected_sha256: str, *, path: Path | None = None) -> dict[str, Any]:
    """Read and verify the same receipt bytes, without downloading or substituting evidence."""
    if len(expected_sha256) != 64 or any(c not in "0123456789abcdef" for c in expected_sha256):
        raise EvaluationError("historical_review_receipt_invalid_checksum")
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise EvaluationError("historical_review_receipt_invalid_path")
    try:
        if path is None:
            configured = os.environ.get(REVIEWS_ENV)
            if not configured:
                raise EvaluationError(f"historical_review_receipts_required:{REVIEWS_ENV}")
            root = Path(configured).expanduser().resolve()
            path = root / relative
            if not path.resolve().is_relative_to(root):
                raise EvaluationError("historical_review_receipt_invalid_path")
        raw = path.read_bytes()
    except OSError as error:
        reason = "invalid_path" if error.errno == errno.ELOOP else "unavailable"
        raise EvaluationError("historical_review_receipt_" + reason) from None
    except RuntimeError:
        # pathlib on Python 3.11 reports symlink loops as RuntimeError, including the path.
        raise EvaluationError("historical_review_receipt_invalid_path") from None
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise EvaluationError("historical_review_receipt_hash_mismatch")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError):
        raise EvaluationError("historical_review_receipt_invalid_json") from None
    if not isinstance(value, dict):
        raise EvaluationError("historical_review_receipt_invalid_json")
    return value
