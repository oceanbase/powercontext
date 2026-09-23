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

"""Render shared resources with native tool bindings and configuration formats."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, cast

ASSETS = Path(__file__).with_name("assets")
BASE_PLUGIN = ASSETS.parents[2] / "agent-plugin/powercontext"
TYPESCRIPT_FILES = {
    "client": "client.ts",
    "worker": "worker.ts",
    "errors": "errors.ts",
    "checkpoints": "checkpoints.ts",
    "operations": "operations.generated.ts",
    "writes": "writes.generated.ts",
    "management": "management.ts",
}
WORKFLOWS = {
    "memory": (
        "scope-memory",
        "Search, inventory, save or correct Memory on request or restore missing context.",
    ),
    "handoff": (
        "work-handoff",
        "Transfer or resume work; preview without writes and commit only an explicit milestone.",
    ),
    "review": (
        "review-publication",
        "Inspect artifacts or candidates on request, preserving the host's review authority.",
    ),
}


def _render(template: str, values: dict[str, Any]) -> str:
    text = (ASSETS / template).read_text(encoding="utf-8")
    return re.sub(r"\{\{(\w+)\}\}", lambda match: str(values[match[1]]), text)


def _tool_bindings(settings: dict[str, Any]) -> dict[str, str]:
    from powercontext.client.operations import OPERATIONS

    bindings = json.loads((ASSETS / "tool-bindings.json").read_text())[settings.get("tools", "mcp")]
    names = {name: bindings["prefix"] + name for name in OPERATIONS} if "prefix" in bindings else {}
    return names | bindings.get("aliases", {})


def _render_skills(settings: dict[str, Any]) -> dict[str, bytes]:
    bindings = _tool_bindings(settings)
    root = BASE_PLUGIN / "skills/powercontext-project-context"
    files = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        content = re.sub(r"\b\w+\b", lambda match: bindings.get(match[0], match[0]), content)
        if settings.get("runtime_skills"):
            for workflow, (reference, _) in WORKFLOWS.items():
                content = re.sub(
                    r"\[[^\]]+\]\(references/" + reference + r"\.md\)", f"`powercontext-{workflow}`", content
                )
        files[path.relative_to(root).as_posix()] = content.encode()
    body = files["SKILL.md"].decode().split("---\n", 2)[2].lstrip()
    guidance = {"src/guidance.ts": f"export const GUIDANCE: string = {json.dumps(body)}\n".encode()}
    if settings.get("runtime_skills"):
        skills = [
            {
                "name": f"powercontext-{name}",
                "description": description,
                "source": "runtime",
                "content": files[f"references/{reference}.md"].decode(),
            }
            for name, (reference, description) in WORKFLOWS.items()
        ]
        return {
            **guidance,
            "src/domain-skills.ts": f"export const DOMAIN_SKILLS = {json.dumps(skills, indent=2)}\n".encode(),
        }
    if settings.get("agent_metadata"):
        files["agents/openai.yaml"] = (ASSETS / "skills/agents/openai.yaml").read_bytes()
    return {f"skills/powercontext-project-context/{name}": content for name, content in files.items()} | guidance


def render_mcp(host: str, *, server_url: str | None = None) -> dict[str, Any]:
    """Apply native configuration fields to the shared MCP server template."""

    settings = json.loads((ASSETS / "resources.json").read_text())[host]["mcp"]
    config = json.loads((BASE_PLUGIN / "mcp.json").read_text())
    metadata = {key: value for key, value in config.items() if key != "mcpServers"}
    entry = config["mcpServers"]["powercontext"]
    if "path" in settings:
        entry["url"] = entry["url"].removesuffix("/mcp") + settings["path"]
    entry.update(settings.get("server", {}))
    if server_url is not None:
        entry["url"] = server_url.rstrip("/") + settings.get("path", "/mcp")
    if not settings.get("wrapped", True):
        config = config["mcpServers"]
    return settings.get("metadata", metadata) | (
        {"mcpServers": config["mcpServers"]} if settings.get("wrapped", True) else config
    )


def render_resources(host: str, directory: Path | None = None, *, server_url: str | None = None) -> dict[str, bytes]:
    profile = json.loads((ASSETS / "resources.json").read_text(encoding="utf-8")).get(host, {})
    files = _render_skills(profile["skills"]) if "skills" in profile else {}
    if profile.get("runtime") != "typescript":
        files.pop("src/guidance.ts", None)
    if "mcp" in profile:
        files[profile["mcp"]["file"]] = (json.dumps(render_mcp(host, server_url=server_url), indent=2) + "\n").encode()
    if "scope_settings" in profile or host == "codex":
        files["hooks/scope_context.py"] = (ASSETS / "python/scope_context.py.tmpl").read_bytes()
    if "scope_settings" in profile:
        variables = {f"settings_{key}": value for key, value in profile["scope_settings"].items()}
        files["scripts/workspace_scope.py"] = _render(
            "python/scope.py.tmpl", {**variables, "integration": host}
        ).encode()
    if profile.get("runtime") in {"python", "typescript"}:
        definitions, schemas = _tool_definitions(profile["skills"])
        files["tools.generated.json"] = (
            json.dumps({"definitions": schemas, "tools": definitions}, indent=2) + "\n"
        ).encode()
    if profile.get("runtime") == "python":
        from powercontext.client import checkpoints
        from powercontext.client.integration import config
        from powercontext.client.operations import OPERATIONS, WRITE_OPERATIONS

        files["powercontext_client_config.py"] = Path(config.__file__).read_bytes()
        files["checkpoints.py"] = Path(checkpoints.__file__).read_bytes()
        variables = {
            "paths": repr({name: op.path for name, op in OPERATIONS.items()}),
            "writes": repr(sorted(WRITE_OPERATIONS)),
        }
        files["runtime_operations.py"] = _render("python/operations.py.tmpl", variables).encode()
        files["standard_tools.py"] = (ASSETS / "python/tools.py.tmpl").read_bytes()
    if profile.get("runtime") == "typescript":
        from powercontext.client.operations import OPERATIONS, WRITE_OPERATIONS

        manifest = json.loads((directory / "package.json").read_text()) if directory is not None else {}
        variables = {
            "extension": "js" if host == "openclaw" else "ts",
            "package_name": manifest.get("name", "powercontext"),
            "package_version": manifest.get("version", "0.0.0"),
            "client_class": "class CoreClient" if host == "dsh" else "export class PowerContextClient",
            "discovery": (ASSETS / "typescript/discovery.ts.tmpl").read_text() if host == "dsh" else "",
            "operations": "\n".join(
                f"  {name}: {json.dumps({'method': op.method, 'path': op.path, 'scopeMode': op.scope_mode})},"
                for name, op in OPERATIONS.items()
            ),
            "writes": json.dumps(sorted(WRITE_OPERATIONS)),
        }
        files.update({
            f"src/{name}": _render(f"typescript/{template}.ts.tmpl", variables).encode()
            for template, name in TYPESCRIPT_FILES.items()
        })
        files["src/tools.generated.ts"] = _render(
            "typescript/tools.ts.tmpl",
            {
                "extension": variables["extension"],
                "zod_import": "import { tool } from '@opencode-ai/plugin'" if host == "opencode" else "",
                "zod_adapter": (ASSETS / "typescript/zod.ts.tmpl").read_text() if host == "opencode" else "",
            },
        ).encode()
        if host == "openclaw":
            router = files["skills/powercontext-project-context/SKILL.md"].decode().split("---\n", 2)[2].lstrip()
            guidance = "\n".join(line for line in router.splitlines() if not line.startswith("|"))
            files["src/guidance.ts"] = _render(
                "typescript/guidance.ts.tmpl", {"guidance": json.dumps(guidance)}
            ).encode()

    return files


def _tool_definitions(settings: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from powercontext.client.operations import OPERATIONS, WRITE_OPERATIONS
    from powercontext.http._generated.schema import OPENAPI_SCHEMA

    aliases = _tool_bindings(settings)
    definitions = []
    schemas = {}
    paths = cast(dict[str, Any], OPENAPI_SCHEMA["paths"])
    for name in json.loads((BASE_PLUGIN.parent / "operations.json").read_text()):
        operation = OPERATIONS[name]
        schema = operation.request_type.model_json_schema(by_alias=True)
        schemas.update(schema.pop("$defs", {}))
        schema["properties"].pop("scope_id", None)
        schema["required"] = [key for key in schema.get("required", []) if key != "scope_id"]
        route = paths[operation.path][operation.method.lower()]
        description = route.get("description", operation.summary)
        description = re.sub(r"\b\w+\b", lambda match: aliases.get(match[0], match[0]), description)
        definitions.append({
            "name": aliases[name],
            "operation": name,
            "description": description,
            "summary": operation.summary,
            "parameters": schema,
            "mutates": name in WRITE_OPERATIONS,
        })
    return definitions, schemas


def install_resources(host: str, directory: Path, *, server_url: str | None = None, preserve_mcp: bool = True) -> None:
    """Materialize only this plugin's resources; host settings and credentials stay separate."""

    for name, content in render_resources(host, directory, server_url=server_url).items():
        path = directory / name
        if preserve_mcp and name.endswith("mcp.json") and path.exists():
            if server_url is None:
                continue
            config = json.loads(path.read_text(encoding="utf-8"))
            generated = json.loads(content)
            config.get("mcpServers", config)["powercontext"]["url"] = generated.get("mcpServers", generated)[
                "powercontext"
            ]["url"]
            content = (json.dumps(config, indent=2) + "\n").encode()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
