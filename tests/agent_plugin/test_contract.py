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
import re
from pathlib import Path

import yaml

from powercontext.http._generated import operations

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


def test_skill_name_and_relative_references_are_portable() -> None:
    skill = PLUGIN_ROOT / "skills" / "powercontext-project-context"
    content = (skill / "SKILL.md").read_text(encoding="utf-8")
    frontmatter = yaml.safe_load(content.split("---", 2)[1])
    assert frontmatter["name"] == skill.name
    assert frontmatter["description"].strip()
    for document in skill.rglob("*.md"):
        for target in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
            if "://" in target:
                continue
            resolved = (document.parent / target).resolve()
            assert resolved.is_relative_to(skill.resolve())
            assert resolved.is_file()


def test_skill_examples_match_current_operation_requests() -> None:
    examples = json.loads(
        (PLUGIN_ROOT / "skills/powercontext-project-context/references/examples.json").read_text(encoding="utf-8")
    )
    for name, payload in examples.items():
        operation = getattr(operations, name.upper())
        operation.request_type.model_validate(payload)


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
        "docs/en/docs/integrations/agent-plugin.md",
        "docs/zh/docs/integrations/agent-plugin.md",
    ):
        content = (REPOSITORY_ROOT / relative_path).read_text(encoding="utf-8")

        assert "VS Code" in content
        assert "git clone https://github.com/oceanbase/powercontext.git" in content
        assert "uv run powercontext server run" in content
        assert "chat.plugins.enabled" in content
        assert "chat.pluginLocations" in content
        assert "/absolute/path/to/cloned/powercontext/integrations/agent-plugin/powercontext" in content
