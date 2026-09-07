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

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_js_operations",
        REPO_ROOT / "scripts" / "generate_js_operations.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_js_operations_cover_every_openapi_operation() -> None:
    generator = _load_generator()
    doc = yaml.safe_load(generator.CONTRACT_PATH.read_text(encoding="utf-8"))
    rows = generator.parse_operations(doc)
    assert rows
    assert {row["operationId"] for row in rows} == {
        operation["operationId"]
        for path_item in (doc.get("paths") or {}).values()
        if isinstance(path_item, dict)
        for operation in path_item.values()
        if isinstance(operation, dict) and operation.get("operationId")
    }


def test_js_operations_record_method_path_location_and_scope() -> None:
    generator = _load_generator()
    doc = yaml.safe_load(generator.CONTRACT_PATH.read_text(encoding="utf-8"))
    by_id = {row["operationId"]: row for row in generator.parse_operations(doc)}
    expected_keys = (
        "operationId",
        "method",
        "path",
        "location",
        "scopeMode",
        "pathParameters",
        "queryParams",
        "headerParams",
        "successStatuses",
        "emptyStatuses",
    )
    assert {key: by_id["get_liveness"][key] for key in expected_keys} == {
        "operationId": "get_liveness",
        "method": "GET",
        "path": "/health/live",
        "location": None,
        "scopeMode": "none",
        "pathParameters": [],
        "queryParams": [],
        "headerParams": [],
        "successStatuses": [200],
        "emptyStatuses": [],
    }
    assert by_id["get_stats"]["location"] == "body"
    assert by_id["get_stats"]["scopeMode"] == "selection"
    assert by_id["remember_memory"]["location"] == "body"
    assert by_id["remember_memory"]["scopeMode"] == "current"
    assert by_id["create_source"]["location"] == "body"
    assert by_id["get_artifact"]["location"] is None
    assert by_id["get_artifact"]["pathParameters"] == ["scope_id", "family", "artifact_id"]
    assert by_id["get_artifact"]["headerParams"] == ["If-None-Match"]
    assert by_id["get_artifact"]["successStatuses"] == [200, 304]
    assert by_id["get_artifact"]["emptyStatuses"] == [304]
    assert by_id["replace_artifact"]["headerParams"] == ["If-Match"]
    assert "delete_artifact" not in by_id
    assert "list_sources" not in by_id
    assert by_id["set_scope_binding"]["scopeMode"] == "none"


def test_committed_js_operations_match_openapi() -> None:
    generator = _load_generator()
    doc = yaml.safe_load(generator.CONTRACT_PATH.read_text(encoding="utf-8"))
    for path in generator.GENERATED_PATHS:
        assert path.is_file()
        committed = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        assert committed == generator.render_operations_source(doc)
