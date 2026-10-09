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

"""Replay pinned daemon entities and Dify models/helpers; never patch their declarations."""

from __future__ import annotations

import ast
import json
import os
import subprocess
from copy import deepcopy
from datetime import date
from enum import StrEnum, auto
from functools import cache
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Union

from pydantic import BaseModel, Field, field_validator, model_validator


@cache
def host_namespace():
    root = Path(os.environ["POWERCONTEXT_DIFY_SOURCE"])
    namespace: dict[str, Any] = {
        "__name__": __name__,
        "StrEnum": StrEnum,
        "auto": auto,
        "json": json,
        "date": date,
        "Any": Any,
        "Union": Union,
        "deepcopy": deepcopy,
        "BaseModel": BaseModel,
        "Field": Field,
        "field_validator": field_validator,
        "model_validator": model_validator,
    }
    files = {
        "api/core/entities/parameter_entities.py": {"CommonParameterType"},
        "api/core/tools/entities/common_entities.py": {"I18nObject"},
        "api/core/plugin/entities/parameters.py": {
            "PluginParameterOption",
            "PluginParameterType",
            "MCPServerParameterType",
            "PluginParameterAutoGenerateType",
            "PluginParameterAutoGenerate",
            "PluginParameterTemplate",
            "PluginParameter",
            "as_normal_type",
            "_validate_date",
            "cast_parameter_value",
            "init_frontend_parameter",
        },
        "api/core/tools/entities/tool_entities.py": {"ToolParameter"},
    }
    for filename, names in files.items():
        path = root / filename
        tree = ast.parse(path.read_text(encoding="utf-8"))
        selected = [
            node for node in tree.body if isinstance(node, ast.ClassDef | ast.FunctionDef) and node.name in names
        ]
        assert {node.name for node in selected} == names
        body: list[ast.stmt] = [*selected]
        module = ast.Module(body=body, type_ignores=[])
        exec(compile(module, str(path), "exec"), namespace)
    namespace["ToolParameter"].model_rebuild(_types_namespace=namespace)
    path = root / "api/core/tools/__base/tool.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tool = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Tool")
    method = next(
        node
        for node in tool.body
        if isinstance(node, ast.FunctionDef) and node.name == "get_llm_parameters_json_schema"
    )
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(path), "exec"), namespace)
    return namespace


@cache
def daemon_tools(registry):
    root = Path(os.environ["POWERCONTEXT_DIFY_DAEMON_SOURCE"])
    _, _, loaded = registry.tools_mapping["powercontext"]
    result = subprocess.run(
        [
            os.environ.get("POWERCONTEXT_DIFY_GO", "go"),
            "run",
            str(Path(__file__).with_name("daemon_discovery_probe.go")),
        ],
        cwd=root,
        input=json.dumps([declaration.model_dump(mode="json") for declaration, _ in loaded.values()]),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=300,
    )
    return {declaration["identity"]["name"]: declaration for declaration in json.loads(result.stdout)}


def host_parameters(declaration):
    model = host_namespace()["ToolParameter"]
    parameters = declaration["parameters"] if isinstance(declaration, dict) else declaration.parameters
    return [model.model_validate(value if isinstance(value, dict) else value.model_dump()) for value in parameters]


def cast_parameters(declaration, parameters):
    types = {parameter.name: parameter.type for parameter in host_parameters(declaration)}
    return {name: types[name].cast_value(value) if name in types else value for name, value in parameters.items()}


def model_schema(declaration):
    parameters = host_parameters(declaration)
    return host_namespace()["get_llm_parameters_json_schema"](
        SimpleNamespace(get_merged_runtime_parameters=lambda **_: parameters)
    )
