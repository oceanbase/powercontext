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

"""Failed requests name the public field or reason an agent can act on."""

from __future__ import annotations

from powercontext.builtin.review.errors import InvalidCandidateError
from powercontext.builtin.runtime.errors import InvalidRuntimeRequestError
from powercontext.server.app import _invalid_request_details, _map_candidate_error


def test_candidate_errors_name_the_public_request_field() -> None:
    mapped = _map_candidate_error(
        InvalidCandidateError("artifacts", "must include the exact target experience Artifact")
    )

    assert mapped == (
        422,
        "invalid_request",
        "The request is invalid.",
        {"field": "artifact_refs", "reason": "target_not_in_artifact_refs"},
    )


def test_candidate_errors_name_the_target_field() -> None:
    mapped = _map_candidate_error(InvalidCandidateError("target", "must identify an Experience"))

    assert mapped is not None
    assert mapped[3] == {"field": "target", "reason": "target_family_mismatch"}


def test_candidate_lineage_errors_name_artifact_refs() -> None:
    for detail, reason in (
        ("managed Skill lineage must include its exact target", "target_not_in_artifact_refs"),
        (
            "new managed Skill lineage may reference only Experience Artifacts and its generation Prompt",
            "skill_lineage_not_permitted",
        ),
    ):
        mapped = _map_candidate_error(InvalidCandidateError("artifacts", detail))

        assert mapped is not None
        assert mapped[3] == {"field": "artifact_refs", "reason": reason}


def test_candidate_errors_do_not_echo_internal_details() -> None:
    mapped = _map_candidate_error(InvalidCandidateError("audit", "Dream audit must be assigned at proposal creation"))

    assert mapped is not None
    assert mapped[3] is None


def test_working_note_expiry_details_name_expires_at() -> None:
    assert _invalid_request_details(InvalidRuntimeRequestError("working-note-expiry-timezone")) == {
        "field": "expires_at",
        "reason": "missing_time_zone",
    }
    assert _invalid_request_details(InvalidRuntimeRequestError("working-note-expiry-past")) == {
        "field": "expires_at",
        "reason": "not_in_future",
    }
