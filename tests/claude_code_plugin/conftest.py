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
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_CODE_ROOT = REPOSITORY_ROOT / "integrations" / "claude-code"
PLUGIN_ROOT = CLAUDE_CODE_ROOT / "plugins" / "powercontext"
_PLUGIN_MODULE_NAMES = (
    "claude_code_settings",
    "hooks",
    "hooks.prepared_context",
    "scripts",
    "scripts.project_scope",
)


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return module


@pytest.fixture
def plugin_imports() -> Iterator[None]:
    previous_path = list(sys.path)
    previous_modules = {name: sys.modules.get(name) for name in _PLUGIN_MODULE_NAMES}
    for name in _PLUGIN_MODULE_NAMES:
        sys.modules.pop(name, None)
    sys.path.insert(0, str(PLUGIN_ROOT))
    try:
        yield
    finally:
        sys.path[:] = previous_path
        for name in _PLUGIN_MODULE_NAMES:
            sys.modules.pop(name, None)
        for name, module in previous_modules.items():
            if module is not None:
                sys.modules[name] = module


@pytest.fixture
def hook_module(plugin_imports: None) -> ModuleType:
    return _load_module(
        "powercontext_claude_code_hook",
        PLUGIN_ROOT / "hooks" / "user_prompt_submit.py",
    )


@pytest.fixture(autouse=True)
def isolated_diagnostic_state(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("POWERCONTEXT_DIAGNOSTIC_STATE_FILE", str(tmp_path / "claude-code-diagnostics.json"))


@pytest.fixture
def scope_module(plugin_imports: None) -> ModuleType:
    return _load_module(
        "powercontext_claude_code_scope",
        PLUGIN_ROOT / "scripts" / "project_scope.py",
    )


@pytest.fixture
def settings_module(plugin_imports: None) -> ModuleType:
    return _load_module(
        "claude_code_settings",
        PLUGIN_ROOT / "claude_code_settings.py",
    )
