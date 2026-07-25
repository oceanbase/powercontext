from __future__ import annotations

import json
from pathlib import Path
from types import ModuleType

import pytest

from .conftest import CODEX_ROOT, PLUGIN_ROOT

EXPECTED_TOOLS = {
    "get_memory_entry",
    "list_memory_entries",
    "remember_memory",
    "retire_memory_entry",
    "revise_memory_entry",
    "search_memory",
}


def test_marketplace_and_manifest_resolve_companions() -> None:
    marketplace = json.loads((CODEX_ROOT / ".agents" / "plugins" / "marketplace.json").read_text())
    manifest = json.loads((PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text())

    assert marketplace["name"] == "powercontext-local"
    assert marketplace["plugins"][0]["source"] == {
        "source": "local",
        "path": "./plugins/powercontext",
    }
    assert marketplace["plugins"][0]["policy"] == {
        "installation": "AVAILABLE",
        "authentication": "ON_INSTALL",
    }
    assert manifest["name"] == PLUGIN_ROOT.name
    assert manifest["version"] == "0.1.0"
    assert "hooks" not in manifest
    assert (PLUGIN_ROOT / manifest["skills"]).is_dir()
    assert (PLUGIN_ROOT / manifest["mcpServers"]).is_file()
    assert (PLUGIN_ROOT / "hooks" / "hooks.json").is_file()


def test_mcp_uses_the_actual_streamable_http_projection() -> None:
    configuration = json.loads((PLUGIN_ROOT / ".mcp.json").read_text())

    assert configuration == {
        "mcpServers": {
            "powercontext": {
                "type": "http",
                "url": "http://127.0.0.1:8000/mcp",
                "required": False,
            }
        }
    }


def test_skill_names_the_current_mcp_contract() -> None:
    skill = (PLUGIN_ROOT / "skills" / "project-context" / "SKILL.md").read_text()

    for tool in EXPECTED_TOOLS:
        assert f"`{tool}`" in skill
    assert "`citation`" in skill
    assert "`memory_revision`" in skill
    assert "untrusted historical data" in skill
    assert "automatically captures user input as a durable Content Source" in skill
    assert "Do not call `remember_memory` merely" in skill


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        ("https://github.com/OceanBase/powercontext.git", "github.com/OceanBase/powercontext"),
        ("ssh://git@github.com/OceanBase/powercontext.git", "github.com/OceanBase/powercontext"),
        ("git@github.com:OceanBase/powercontext.git", "github.com/OceanBase/powercontext"),
    ],
)
def test_scope_normalizes_network_git_remotes(
    scope_module: ModuleType,
    remote: str,
    expected: str,
) -> None:
    assert scope_module.normalize_git_remote(remote) == expected


def test_scope_override_wins(scope_module: ModuleType, tmp_path: Path) -> None:
    assert (
        scope_module.derive_scope_id(
            str(tmp_path),
            environ={"POWERCONTEXT_SCOPE_ID": "project:explicit"},
        )
        == "project:explicit"
    )
