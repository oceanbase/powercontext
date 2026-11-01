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

"""Internal channel loss remains observable after removing the public reason field."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from powercontext.builtin.runtime.application import _degradation
from powercontext.builtin.runtime.models import PrepareContextRequest, PreparedContext
from powercontext.server.mapping import prepared_context_response


@pytest.mark.parametrize(
    ("current", "observed", "expected"),
    [
        (None, None, None),
        (None, "inference_unavailable", "inference_unavailable"),
        (None, "inference_timeout", "inference_timeout"),
        ("inference_timeout", None, "inference_timeout"),
        ("inference_timeout", "inference_unavailable", "inference_timeout"),
        ("inference_unavailable", "inference_timeout", "inference_timeout"),
        ("inference_unavailable", "inference_unavailable", "inference_unavailable"),
    ],
)
def test_a_timeout_outranks_an_unavailable_provider(
    current: str | None, observed: str | None, expected: str | None
) -> None:
    """Raising ``recall_embedding_timeout_seconds`` fixes a timeout; nothing fixes a broken provider.

    Folding in arrival order would report whichever cause came last, so an operator acting on
    ``inference_unavailable`` would turn a knob that cannot help.
    """

    assert _degradation(current, observed) == expected


def test_default_preparation_has_no_receipt() -> None:
    prepared = PreparedContext(status="ready", content="alpha", content_bytes=5)

    assert prepared.receipt is None
    assert "reason" not in prepared.model_dump(mode="json", by_alias=True)


def test_the_transport_response_keeps_the_four_fields_a_host_validates() -> None:
    """Host validators compare the key set exactly, so an unexplained preparation must not grow one.

    ``content`` is required and stays ``null`` on an empty preparation: dropping the key would break
    the same validators from the other side.
    """

    published = prepared_context_response(PreparedContext(status="empty", content=None, content_bytes=0))

    assert published.model_dump(mode="json", by_alias=True, exclude_unset=True) == {
        "schema": "powercontext.prepared-context.v1",
        "status": "empty",
        "content": None,
        "content_bytes": 0,
    }


@pytest.mark.parametrize("reason", ["timeout", "", "inference_degraded", "inference_timeout"])
def test_legacy_public_reason_is_rejected(reason: str) -> None:
    with pytest.raises(ValidationError):
        PreparedContext.model_validate({"status": "ready", "content": "alpha", "content_bytes": 5, "reason": reason})


def test_legacy_public_explain_is_rejected() -> None:
    with pytest.raises(ValidationError):
        PrepareContextRequest.model_validate({"query": "alpha", "explain": True})
