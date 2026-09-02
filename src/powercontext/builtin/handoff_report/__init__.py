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

"""Scope-based Handoff Report projection."""

from powercontext.builtin.handoff_report.adapters import RuntimeHandoffReadAdapter
from powercontext.builtin.handoff_report.application import HandoffReportApplication
from powercontext.builtin.handoff_report.canonical import (
    ReportCanonicalizationError,
    canonical_json_bytes,
    finalize_digests,
    report_digest,
    selection_digest,
    selection_envelope,
)
from powercontext.builtin.handoff_report.errors import (
    HandoffReportError,
    HandoffReportInconsistentError,
    HandoffReportTooLargeError,
)
from powercontext.builtin.handoff_report.rendering import render_markdown
from powercontext.builtin.handoff_report.report import (
    HandoffReport,
    HandoffReportStatus,
    HandoffReportSummary,
    ScopeHandoffReport,
)

__all__ = [
    "HandoffReport",
    "HandoffReportApplication",
    "HandoffReportError",
    "HandoffReportInconsistentError",
    "HandoffReportStatus",
    "HandoffReportSummary",
    "HandoffReportTooLargeError",
    "ReportCanonicalizationError",
    "RuntimeHandoffReadAdapter",
    "ScopeHandoffReport",
    "canonical_json_bytes",
    "finalize_digests",
    "render_markdown",
    "report_digest",
    "selection_digest",
    "selection_envelope",
]
