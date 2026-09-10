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
    assert "success_response_types={204: None}" in source
