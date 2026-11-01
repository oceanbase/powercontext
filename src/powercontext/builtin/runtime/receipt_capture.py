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

"""Bind optional, request-local receipt evidence across inference boundaries."""

from __future__ import annotations

from contextvars import ContextVar
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from powercontext.builtin.runtime.receipt_builder import ReceiptEvidence

_CAPTURE: ContextVar[ReceiptEvidence | None] = ContextVar("prepared_context_receipt_capture", default=None)


def current_receipt_evidence() -> ReceiptEvidence | None:
    return _CAPTURE.get()


def bind_receipt_evidence(evidence: ReceiptEvidence | None):
    return _CAPTURE.set(evidence)


def unbind_receipt_evidence(token) -> None:
    _CAPTURE.reset(token)
