from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CODEX_ROOT = REPOSITORY_ROOT / "integrations" / "codex"
PLUGIN_ROOT = CODEX_ROOT / "plugins" / "powercontext"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def scope_module() -> ModuleType:
    return _load_module("powercontext_codex_scope", PLUGIN_ROOT / "scripts" / "project_scope.py")


@pytest.fixture
def recall_module() -> ModuleType:
    return _load_module("powercontext_codex_recall", PLUGIN_ROOT / "hooks" / "recall.py")


@pytest.fixture
def settings_module() -> ModuleType:
    return _load_module("powercontext_codex_settings", PLUGIN_ROOT / "settings.py")
