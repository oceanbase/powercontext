# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""HTTP clients and the runtime reject the same unsafe or ambiguous code queries."""

import pytest
from pydantic import ValidationError

from powercontext.builtin.code import CodeQueryRequest as RuntimeRequest
from powercontext.http import CodeQueryRequest as HttpRequest

FINGERPRINT = "a" * 64


@pytest.mark.parametrize(
    "payload",
    [
        {"operation": {"kind": "callers", "symbol_id": "target"}},
        {"operation": {"kind": "affected_tests", "paths": ["a.py"]}},
        {"operation": {"kind": "impact_changes", "paths": ["a.py"]}, "expected_fingerprint": FINGERPRINT},
        {"operation": {"kind": "status"}, "before_fingerprint": FINGERPRINT},
        {"operation": {"kind": "symbols", "query": "  \n"}},
        {"operation": {"kind": "map", "path_prefix": "../outside"}},
        {"operation": {"kind": "symbols", "query": "name", "path_prefix": "/outside"}},
        {
            "operation": {"kind": "callees", "symbol_id": "target", "path_prefix": "a//b"},
            "expected_fingerprint": FINGERPRINT,
        },
        {"operation": {"kind": "affected_tests", "paths": ["a.py", "a.py"]}, "expected_fingerprint": FINGERPRINT},
        {"operation": {"kind": "affected_tests", "paths": ["./a.py"]}, "expected_fingerprint": FINGERPRINT},
    ],
)
def test_code_request_semantics_agree(payload):
    for model in (HttpRequest, RuntimeRequest):
        with pytest.raises(ValidationError):
            model.model_validate(payload)


@pytest.mark.parametrize(
    ("path", "start", "end", "valid"),
    [
        ("src/预算.py", 1, 200, True),
        ("src/预算.py", 10, 10, True),
        ("src/file.py", 1, 201, False),
        ("src/file.py", 10, 9, False),
        ("../file.py", 1, 1, False),
        ("C:/file.py", 1, 1, False),
        ("src\\file.py", 1, 1, False),
    ],
)
def test_code_read_boundaries_agree(path, start, end, valid):
    payload = {
        "operation": {"kind": "read", "path": path, "file_sha256": FINGERPRINT, "start_line": start, "end_line": end},
        "expected_fingerprint": FINGERPRINT,
    }
    for model in (HttpRequest, RuntimeRequest):
        if valid:
            assert model.model_validate(payload).operation.model_dump()["path"] == path
        else:
            with pytest.raises(ValidationError):
                model.model_validate(payload)


@pytest.mark.parametrize(
    "operation",
    [
        {"kind": "status"},
        {"kind": "changes"},
        {"kind": "map"},
        {"kind": "symbols", "query": "prepare"},
        {"kind": "explore", "query": "预算"},
        {"kind": "callers", "symbol_id": "target"},
        {"kind": "callees", "symbol_id": "target"},
        {"kind": "impact", "symbol_id": "target"},
        {"kind": "affected_tests", "paths": ["src/预算.py"]},
        {"kind": "impact_changes", "paths": ["src/预算.py"]},
    ],
)
def test_valid_code_operations_agree(operation):
    request = {"operation": operation, "expected_fingerprint": FINGERPRINT}
    if operation["kind"] == "impact_changes":
        request["before_fingerprint"] = "b" * 64
    assert HttpRequest.model_validate(request).model_dump(mode="json") == RuntimeRequest.model_validate(
        request
    ).model_dump(mode="json")
