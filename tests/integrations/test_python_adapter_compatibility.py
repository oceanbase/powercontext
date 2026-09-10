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

"""Standalone adapters keep legacy setup usable without the optional assembly API."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_ADAPTERS = [
    ("langchain", "powercontext_langchain", "PowerContextLangChainSettings"),
    ("langgraph", "powercontext_langgraph", "PowerContextLangGraphSettings"),
    ("pydantic-ai", "powercontext_pydantic_ai", "PowerContextSettings"),
]


@pytest.mark.parametrize(("adapter", "module", "settings_class"), _ADAPTERS)
@pytest.mark.parametrize("assembly_available", [False, True])
def test_adapter_setup_with_optional_core_assembly_api(adapter, module, settings_class, assembly_available):
    script = f"""
import importlib
import sys

sys.path.insert(0, {str(_ROOT / "integrations" / adapter / "src")!r})

from pydantic import ValidationError
import powercontext.http as http

if not {assembly_available!r}:
    # Model the public API exposed by core releases before text assembly.
    del http.ContextAssembly

settings_type = getattr(importlib.import_module({module + ".settings"!r}), {settings_class!r})
assert settings_type().context_assembly is None
assert settings_type(context_assembly=None).context_assembly is None

for value in [{{}}, {{"sections": []}}, {{"sections": [{{"family": "memory", "limit": 3}}]}}]:
    if {assembly_available!r}:
        assembly = settings_type(context_assembly=value).context_assembly
        assert isinstance(assembly, http.ContextAssembly)
        request = http.PrepareContextRequest(scope_id="test", query="test", assembly=assembly)
        assert request.assembly == assembly
    else:
        try:
            settings_type(context_assembly=value)
        except ValidationError as error:
            assert "context_assembly requires a PowerContext core with text assembly support" in str(error)
        else:
            raise AssertionError("Assembly must fail clearly when the core API is unavailable")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
