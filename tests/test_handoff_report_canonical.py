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

import asyncio
from datetime import UTC, datetime

import pytest

from powercontext.builtin.handoff_report.canonical import (
    canonical_json_bytes,
    finalize_digests,
    report_digest,
    selection_digest,
)
from powercontext.builtin.handoff_report.models import ProjectDescriptor, WorkstreamDescriptor
from powercontext.builtin.handoff_report.service import HandoffReportService
from tests.test_handoff_report_service import _Adapter, _handoff


def _project() -> ProjectDescriptor:
    return ProjectDescriptor(
        project_id="prj-canonical",
        project_key="canonical",
        title="Canonical",
        timezone="UTC",
        version=1,
    )


def _workstream() -> WorkstreamDescriptor:
    return WorkstreamDescriptor(
        scope_id="scope-canonical",
        project_id="prj-canonical",
        title="Canonical",
        kind="feature",
        version=1,
    )


def _report():
    async def scenario():
        return await HandoffReportService(_Adapter({"scope-canonical": _handoff()})).generate(
            _project(),
            (_workstream(),),
            include_evidence_checks=False,
            generated_at=datetime(2026, 8, 6, tzinfo=UTC),
        )

    return asyncio.run(scenario())


def test_canonical_json_normalizes_nfc_and_rejects_floats() -> None:
    assert canonical_json_bytes({"text": "Cafe\u0301"}) == canonical_json_bytes({"text": "Café"})

    with pytest.raises(ValueError, match="floating-point"):
        canonical_json_bytes({"value": 1.5})


def test_selection_digest_is_locale_independent_but_report_digest_is_not() -> None:
    report = _report()
    english = report.model_copy(update={"locale": "en"})

    assert report.selection_digest == selection_digest(report)
    assert report.selection_digest == selection_digest(english)
    assert report.report_digest == report_digest(report)
    assert report.report_digest != report_digest(english)
    assert finalize_digests(english).selection_digest == report.selection_digest


def test_digest_fields_are_stable_when_a_report_is_revalidated() -> None:
    report = _report()
    restored = type(report).model_validate(report.model_dump(by_alias=True))

    assert restored.selection_digest == report.selection_digest
    assert restored.report_digest == report.report_digest
