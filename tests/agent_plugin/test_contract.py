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

import json
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[2] / "integrations" / "agent-plugin" / "powercontext"
REPOSITORY_ROOT = PLUGIN_ROOT.parents[2]


def test_agent_plugin_manifest_uses_portable_schema_fields() -> None:
    manifest = json.loads((PLUGIN_ROOT / "plugin.json").read_text(encoding="utf-8"))

    assert manifest["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert manifest["name"] == "powercontext"
    assert manifest["description"]
    assert manifest["license"] == "Apache-2.0"
    assert "mcp" in manifest["keywords"]

    allowed_fields = {
        "$schema",
        "name",
        "version",
        "description",
        "author",
        "homepage",
        "repository",
        "license",
        "keywords",
        "extensions",
    }
    assert set(manifest) <= allowed_fields
    assert "skills" not in manifest
    assert "mcpServers" not in manifest


def test_agent_plugin_mcp_configuration_is_portable_and_secret_free() -> None:
    configuration = json.loads((PLUGIN_ROOT / "mcp.json").read_text(encoding="utf-8"))

    assert configuration == {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {
            "powercontext": {
                "type": "streamable-http",
                "url": "http://127.0.0.1:8000/mcp",
            }
        },
    }
    assert "headers" not in configuration["mcpServers"]["powercontext"]
    assert "env_http_headers" not in configuration["mcpServers"]["powercontext"]
    assert "POWERCONTEXT" not in json.dumps(configuration)


def test_project_context_skill_is_reusable_and_preserves_powercontext_workflows() -> None:
    content = (PLUGIN_ROOT / "skills" / "project-context" / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(content.split("---", 2)[1])

    assert frontmatter == {
        "name": "project-context",
        "description": (
            "Use PowerContext project memory and handoff tools through MCP when continuing prior work, "
            "recalling decisions, maintaining durable memory, or transferring work across tasks, sessions, or agents."
        ),
    }
    for required in (
        "search_memory",
        "list_memory_entries",
        "get_memory_entry",
        "remember_memory",
        "revise_memory_entry",
        "retire_memory_entry",
        "handoff_current_work",
        'selection: "prepared"',
        "continue_handoff",
        "acknowledge_handoff",
        "record_task_outcome",
        "Degrade Safely",
    ):
        assert required in content

    forbidden_fragments = (
        "Codex",
        "OpenCode",
        "UserPromptSubmit",
        "prompt capture",
        "POWERCONTEXT_CODEX",
        "additionalContext",
    )
    for forbidden in forbidden_fragments:
        assert forbidden not in content


def test_project_context_skill_uses_default_model_free_handoff_flow() -> None:
    content = (PLUGIN_ROOT / "skills" / "project-context" / "SKILL.md").read_text(encoding="utf-8")

    assert "without invoking a generation model" in content
    assert "handoff_current_work" in content
    assert "activate_handoff" not in content
    assert "finalize_handoff" not in content
    assert "`boundary_source`" not in content


def test_agent_plugin_readme_documents_server_and_auth_boundaries() -> None:
    content = (PLUGIN_ROOT / "README.md").read_text(encoding="utf-8")

    assert "git clone https://github.com/oceanbase/powercontext.git" in content
    assert "uv run powercontext server run" in content
    assert "http://127.0.0.1:8000/mcp" in content
    assert "chat.pluginLocations" in content
    assert "static credentials" in content
    assert "does not embed" in content
    assert "storage" in content


def test_agent_plugin_docs_include_verified_host_loading_procedure() -> None:
    for relative_path in (
        "docs/en/docs/how-to/configure-agent-plugin.md",
        "docs/zh/docs/how-to/configure-agent-plugin.md",
    ):
        content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")

        assert "VS Code" in content
        assert "git clone https://github.com/oceanbase/powercontext.git" in content
        assert "uv run powercontext server run" in content
        assert "chat.plugins.enabled" in content
        assert "chat.pluginLocations" in content
        assert "/absolute/path/to/cloned/powercontext/integrations/agent-plugin/powercontext" in content


def test_docs_overview_keeps_existing_cards_and_agent_plugin_card_aligned() -> None:
    expected_cards = {
        "en": (
            "Continue in OpenClaw",
            "Continue in OpenCode",
            "Load an Agent Plugin",
            "en/docs/how-to/configure-agent-plugin/",
        ),
        "zh": (
            "在 OpenClaw 中继续",
            "在 OpenCode 中继续",
            "加载 Agent Plugin",
            "zh/docs/how-to/configure-agent-plugin/",
        ),
    }

    for locale, fragments in expected_cards.items():
        content = (REPOSITORY_ROOT / f"docs/{locale}/docs/index.md").read_text(encoding="utf-8")

        assert "template: docs-overview.html" in content
        for fragment in fragments:
            assert fragment in content
