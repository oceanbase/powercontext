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

import ast
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "generate_api",
        REPO_ROOT / "scripts" / "generate_api.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_path_and_header_parameters_do_not_create_a_query_model_for_no_content_success() -> None:
    generator = _load_generator()
    contract = generator.OpenAPI.model_validate({
        "openapi": "3.0.3",
        "info": {"title": "Test API", "version": "1.0.0"},
        "paths": {
            "/widgets/{widget_id}": {
                "delete": {
                    "summary": "Delete a widget",
                    "operationId": "delete_widget",
                    "parameters": [
                        {
                            "name": "widget_id",
                            "in": "path",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "If-Match",
                            "in": "header",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                    ],
                    "responses": {"204": {"description": "Deleted."}},
                }
            }
        },
    })

    source = generator._generate_operations(contract, {})

    assert "DELETE_WIDGET = Operation[None, None](" in source
    assert "request_type=None" in source
    assert "request_location=None" in source
    assert "response_type=None" in source
    assert "success_status=204" in source


@pytest.fixture
def feature_contract():
    return {
        "openapi": "3.0.3",
        "info": {"title": "Test API", "version": "1.0.0"},
        "x-powercontext-feature-contracts": {
            "scope.selection": {"major": 1, "minor": 0},
            "memory.explicit": {"major": 1, "minor": 0},
        },
        "paths": {
            path: {
                "get": {
                    "summary": operation_id,
                    "operationId": operation_id,
                    "responses": {"204": {"description": "Done."}},
                    "x-powercontext-feature-contracts": [feature],
                }
            }
            for path, operation_id, feature in (
                ("/scopes", "list_scopes", "scope.selection"),
                ("/memory", "search_memory", "memory.explicit"),
            )
        },
    }


def test_generation_projects_explicit_feature_versions_and_operation_membership(feature_contract) -> None:
    generator = _load_generator()
    feature_contract["x-powercontext-feature-contracts"]["scope.selection"]["minor"] = 2
    feature_contract["paths"]["/memory"]["get"]["x-powercontext-feature-contracts"] = [
        "scope.selection",
        "memory.explicit",
    ]
    source = generator._generate_operations(generator.OpenAPI.model_validate(feature_contract), {})
    assignment = next(
        node
        for node in ast.parse(source).body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "FEATURE_CONTRACTS"
    )
    assert assignment.value is not None
    assert ast.literal_eval(assignment.value) == {
        "scope.selection": {"version": {"major": 1, "minor": 2}, "operations": ["list_scopes", "search_memory"]},
        "memory.explicit": {"version": {"major": 1, "minor": 0}, "operations": ["search_memory"]},
    }


@pytest.mark.parametrize(
    ("invalid", "value"),
    [
        ("major", 0),
        ("major", -1),
        ("major", True),
        ("minor", -1),
        ("minor", True),
        ("membership", ["undefined.feature"]),
        ("membership", "scope.selection"),
        ("membership", ["scope.selection", "scope.selection"]),
        ("unused", {"major": 1, "minor": 0}),
    ],
)
def test_generation_rejects_invalid_feature_contract_declarations(feature_contract, invalid, value) -> None:
    generator = _load_generator()
    if invalid in {"major", "minor"}:
        feature_contract["x-powercontext-feature-contracts"]["scope.selection"][invalid] = value
    elif invalid == "membership":
        feature_contract["paths"]["/scopes"]["get"]["x-powercontext-feature-contracts"] = value
    else:
        feature_contract["x-powercontext-feature-contracts"]["unused"] = value

    with pytest.raises(generator.ContractGenerationError, match="feature"):
        generator._generate_operations(generator.OpenAPI.model_validate(feature_contract), {})
