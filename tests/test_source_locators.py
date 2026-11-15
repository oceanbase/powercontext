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

import pytest
from pydantic import ValidationError

from powercontext.sources import SourceRef


@pytest.mark.parametrize(
    "locator",
    [
        {"kind": "sequence", "sequence": 0},
        {"kind": "sequence", "sequence": 12},
        {"kind": "line_range", "start_line": 40, "end_line": 55, "heading_path": "设计 / 引用"},
        {"kind": "line_range", "start_line": 1, "end_line": 200},
    ],
)
def test_source_locator_round_trip(locator) -> None:
    reference = {"source_type": "document", "source_id": "doc", "locator": locator}
    value = SourceRef.model_validate(reference)
    assert value.model_dump(mode="json", exclude_none=True) == reference
    assert SourceRef.model_validate_json(value.model_dump_json()) == value


def test_absent_source_locator_preserves_legacy_encoding() -> None:
    reference = {"source_type": "content", "source_id": "doc"}
    value = SourceRef.model_validate(reference)
    assert value.locator is None
    assert value.model_dump(mode="json") == reference
    origin = SourceRef.model_validate(reference | {"locator": {"kind": "sequence", "sequence": 0}})
    assert origin != value
    assert origin.model_dump(mode="json")["locator"]["sequence"] == 0


@pytest.mark.parametrize(
    "locator",
    [
        {"kind": "sequence", "sequence": -1},
        {"kind": "sequence", "sequence": True},
        {"kind": "sequence", "sequence": "1"},
        {"kind": "sequence", "sequence": 1, "start_line": 1},
        {"kind": "line_range", "start_line": 0, "end_line": 1},
        {"kind": "line_range", "start_line": 2, "end_line": 1},
        {"kind": "line_range", "start_line": 1, "end_line": 201},
        {"kind": "line_range", "start_line": 1.0, "end_line": 1},
        {"kind": "line_range", "start_line": 1, "end_line": 1, "heading_path": "x" * 1025},
        {"kind": "unknown", "sequence": 0},
    ],
)
def test_invalid_source_locator_is_rejected(locator) -> None:
    with pytest.raises(ValidationError):
        SourceRef.model_validate({"source_type": "document", "source_id": "doc", "locator": locator})


def test_source_locator_round_trips_both_transport_spellings() -> None:
    from powercontext.server.mapping import (
        runtime_source_reference,
        runtime_source_type_reference,
        source_reference,
        source_type_reference,
    )

    reference = SourceRef.model_validate({
        "source_type": "document",
        "source_id": "doc",
        "locator": {"kind": "line_range", "start_line": 1, "end_line": 200},
    })
    assert runtime_source_reference(source_reference(reference)) == reference
    assert runtime_source_type_reference(source_type_reference(reference)) == reference
    bare = SourceRef(source_type="content", source_id="old")
    assert "locator" not in source_reference(bare).model_dump(mode="json")
    assert "locator" not in source_type_reference(bare).model_dump(mode="json")


def test_transport_rejects_excessive_source_line_span() -> None:
    from powercontext.http import SourceReference

    with pytest.raises(ValidationError):
        SourceReference.model_validate({
            "name": "document",
            "source_id": "doc",
            "locator": {"kind": "line_range", "start_line": 1, "end_line": 201},
        })
