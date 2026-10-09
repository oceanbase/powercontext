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

"""Load packaged Skill resources and expose controlled, observable evaluation reads.

The generation CLI also checks packaged guidance without model calls. Native
discovery and installation are qualified separately from generated-file checks.
"""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

from powercontext.cli.guidance import (
    END,
    HOST_GUIDANCE,
    RESOURCE_MANIFEST,
    START,
    YAML_START,
    GuidanceError,
    merge_guidance,
    write_if_changed,
)

ROOT = Path(__file__).resolve().parents[1]
ENTRY_NAME = "powercontext-project-context"
FILE_HOSTS = {host: descriptor.skill.as_posix() for host, descriptor in HOST_GUIDANCE.items() if host != "dsh"}


def _guidance_capabilities(host: str, paths: list[str], manifest: dict[str, Any], root: Path) -> set[str]:
    """Reject guidance that exceeds the manifest's declared capability contract."""
    descriptor = HOST_GUIDANCE[host]
    declarations = {item["id"]: item for item in manifest["integrations"]}
    server_capabilities = {
        capability
        for toolset in manifest["toolsets"]
        if toolset["id"] == "server-mcp"
        for tool in toolset["tools"]
        for capability in tool.get("capabilities", [])
    }
    # These two portable MCP packages are not qualified native-host declarations.
    if host in {"agent-plugin", "minimax"}:
        capabilities = server_capabilities
    else:
        capabilities = set(declarations[host]["capabilities"])
    required = {"memory_read", "memory_write"}
    if "references/review-publication.md" in paths:
        required.add("candidate_review")
    if not required <= capabilities:
        message = f"{host}: guidance requires undeclared capabilities: {sorted(required - capabilities)}"
        raise GuidanceError(message)
    for relative in descriptor.script_evidence:
        if not (root / descriptor.plugin / relative).is_file():
            message = f"{host}: missing packaged resolver: {relative}"
            raise GuidanceError(message)
    return capabilities


def generated_guidance(root: Path = ROOT) -> dict[Path, bytes]:
    """Render all resources, checking capability inputs and packaged dependencies first."""
    from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

    templates = root / "scripts/guidance_templates"
    environment = Environment(
        loader=FileSystemLoader(templates),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        autoescape=select_autoescape(enabled_extensions=("html", "xml"), default_for_string=False, default=False),
    )
    resources = json.loads((templates / "resources.json").read_text(encoding="utf-8"))
    legacy = json.loads((templates / "legacy.json").read_text(encoding="utf-8"))
    manifest = tomllib.loads((root / "integrations/capabilities.toml").read_text(encoding="utf-8"))
    discovered = {
        path.parent.relative_to(root).as_posix() for path in (root / "integrations").glob(f"**/{ENTRY_NAME}/SKILL.md")
    }
    expected = {FILE_HOSTS[host] for host in resources}
    if discovered - expected or set(resources) != set(FILE_HOSTS):
        message = f"guidance catalog drift: {sorted(discovered - expected)}"
        raise GuidanceError(message)
    output: dict[Path, bytes] = {}
    for host, paths in resources.items():
        descriptor = HOST_GUIDANCE[host]
        capabilities = _guidance_capabilities(host, paths, manifest, root)
        for relative in paths:
            overlay = f"hosts/{host}/{relative}.j2"
            template = overlay if (templates / overlay).is_file() else f"base/{relative}.j2"
            content = environment.get_template(template).render(capabilities=capabilities)
            unknown = set(re.findall(r"\$\{([^}]+)\}", content)) - set(descriptor.variables)
            if unknown:
                message = f"{host}/{relative}: unknown variables: {sorted(unknown)}"
                raise GuidanceError(message)
            if relative == "SKILL.md":
                content = content.rstrip() + "\n" + environment.get_template("embedding-cost.md.j2").render()
            if relative.endswith(".md"):
                metadata = re.match(r"\A---\n.*?\n---\n", content, re.DOTALL)
                if metadata:
                    content = "---\n" + YAML_START + "\n" + content[4:] + END + "\n"
                else:
                    content = START + "\n" + content + END + "\n"
                destination = root / descriptor.skill / relative
                if destination.is_file():
                    content = merge_guidance(
                        destination.read_bytes().decode("utf-8"),
                        content,
                        legacy_sha256=legacy[host].get(relative),
                    )
            output[root / descriptor.skill / relative] = content.encode("utf-8")
        output[root / descriptor.skill / RESOURCE_MANIFEST] = (
            json.dumps(legacy[host], sort_keys=True, indent=2) + "\n"
        ).encode("utf-8")
    for relative in FILE_HOSTS.values():
        file_skill(root / relative, generated=output)
    return output


def refresh_generated_guidance(root: Path = ROOT, *, check: bool = False) -> tuple[Path, ...]:
    """Plan all updates before writing; check mode never changes bytes or mtimes."""
    expected = generated_guidance(root)
    changed = tuple(path for path, content in expected.items() if not path.is_file() or path.read_bytes() != content)
    if not check:
        for path in changed:
            write_if_changed(path, expected[path])
    return changed


def file_skill(directory: Path, *, generated: dict[Path, bytes] | None = None) -> dict[str, Any]:
    """Read one entry and its reachable local references, diagnosing broken links."""
    directory = directory.resolve()
    resources: dict[str, str] = {}

    def read(relative: str, origin: str) -> None:
        path = (directory / relative).resolve()
        planned = (generated or {}).get(path)
        if not path.is_relative_to(directory) or (planned is None and not path.is_file()):
            message = f"Skill {directory.name}: {origin} links to missing or out-of-package resource {relative}"
            raise ValueError(message)
        key = path.relative_to(directory).as_posix()
        if key in resources:
            return
        resources[key] = planned.decode("utf-8") if planned is not None else path.read_text(encoding="utf-8")
        if path.suffix != ".md":
            return
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", resources[key]):
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            child = (Path(key).parent / unquote(parsed.path)).as_posix()
            read(child, key)

    read("SKILL.md", "entry")
    content = resources["SKILL.md"]
    frontmatter = re.match(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)", content, re.DOTALL)
    try:
        metadata = yaml.safe_load(frontmatter.group(1)) if frontmatter else None
    except yaml.YAMLError as error:
        message = f"Skill {directory.name}: SKILL.md has invalid YAML frontmatter: {error}"
        raise ValueError(message) from error
    if not isinstance(metadata, dict) or metadata.get("name") != directory.name or not metadata.get("description"):
        message = f"Skill {directory.name}: SKILL.md requires its installed name and a discoverable description"
        raise ValueError(message)
    return {**metadata, "content": content, "resources": resources}


def with_skill_resources(catalog: dict[str, Any]) -> dict[str, Any]:
    if catalog["host"] == "dsh":
        skills = catalog.get("skills")
        if not skills:
            message = "DSH catalog lacks runtime Skills; export it from the current SDK runtime test"
            raise ValueError(message)
        resources = {skill["name"]: skill["content"] for skill in skills}
        entry = next((skill for skill in skills if skill["name"] == ENTRY_NAME), None)
        if entry is None:
            message = f"DSH catalog lacks router Skill {ENTRY_NAME}; export it from the current SDK runtime test"
            raise ValueError(message)
        return {**catalog, "skill_resources": resources, "skill": entry}
    skill = file_skill(ROOT / FILE_HOSTS[catalog["host"]])
    if catalog["host"] == "hermes":
        native = catalog.get("skill", {})
        if native.get("name") != f"powercontext:{ENTRY_NAME}" or not isinstance(native.get("description"), str):
            message = "Hermes catalog lacks native Skill metadata; export it with tests/e2e/test_hermes_skills.py"
            raise ValueError(message)
        # Discovery must use what the host exposes, even when it is empty or stale.
        skill.update(name=native["name"], description=native["description"])
    resources = {
        skill["name"] if path == "SKILL.md" else f"{skill['name']}/{path}": content
        for path, content in skill.pop("resources").items()
    }
    return {**catalog, "skill": skill, "skill_resources": resources}


class SkillReadingModel:
    """Let the model choose bounded reads from actual shipped text, without forced loads."""

    def __init__(self, model: Any, resources: dict[str, str], *, available: bool) -> None:
        self.model = model
        self.resources = resources
        self.available = available
        self.loaded: dict[str, str] = {}
        self.steps: list[dict[str, Any]] = []
        self.attempts: list[dict[str, Any]] = []
        self.reads_before_operation: list[str] | None = None

    def record_into(self, record: dict[str, Any], case: str) -> None:
        record["skill_reads"] = self.steps
        record["skill_read_attempts"] = self.attempts
        workflow = {"skill_search": ("memory", "scope-memory.md"), "skill_handoff": ("handoff", "work-handoff.md")}
        if case in workflow and self.available:
            domain, filename = workflow[case]
            entry = f"powercontext:{ENTRY_NAME}" if record["host"] == "hermes" else ENTRY_NAME
            expected = f"powercontext-{domain}" if record["host"] == "dsh" else f"{entry}/references/{filename}"
            read = self.reads_before_operation if self.reads_before_operation is not None else list(self.loaded)
            record["skill_workflow"] = {"expected": expected, "read_before_operation": read, "passed": expected in read}
            if expected not in read:
                record["routing_passed"] = False
                record.setdefault(
                    "error",
                    f"host={record['host']}, case={case}: required {domain} workflow {expected!r} was not read "
                    f"before the data operation; successfully read resources: {read!r}",
                )
        if case in {"ordinary", "sufficient_context", "preview"} and self.steps:
            record["routing_passed"] = False
            record.setdefault("error", "Unnecessary Skill detour for current-context work")

    async def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        tool = {
            "name": "read_skill_resource",
            "description": "Evaluation-only Skill reader. Read a visible Skill by its exact name, or a reference as "
            "<skill-name>/references/<file> after discovering its link. Runtime domain Skills use their own names. "
            "Read only relevant detail when needed; ordinary coding needs no Skill detour. "
            "Read requested guidance before calling a data operation; do not batch these together.",
            "parameters": {
                "type": "object",
                "properties": {"resource": {"type": "string"}},
                "required": ["resource"],
                "additionalProperties": False,
            },
        }
        messages = list(messages)
        if self.loaded:
            messages.append({
                "role": "system",
                "content": "Previously read Skill resources:\n" + json.dumps(self.loaded),
            })
        tools = [*tools, tool] if self.available else tools
        while True:
            response = await self.model.complete(messages, tools)
            calls = response.get("tool_calls") or []
            reads = [call for call in calls if call["function"]["name"] == tool["name"]]
            if not reads:
                if self.reads_before_operation is None and any(
                    call["function"]["name"] not in {"resolve_scope_binding", "powercontext_resolve_scope_binding"}
                    for call in calls
                ):
                    self.reads_before_operation = list(self.loaded)
                return response
            self.attempts.append({"content": response.get("content"), "calls": calls})
            if not self.available:
                message = "read_skill_resource: reader is unavailable in this scenario"
                raise ValueError(message)
            if len(reads) != len(calls):
                names = [call["function"]["name"] for call in calls if call not in reads]
                message = f"read_skill_resource: batched with {names}; read requested guidance before data operations"
                raise ValueError(message)
            if len(self.steps) + len(reads) > 4:
                message = (
                    f"read_skill_resource: {len(reads)} requested reads after {len(self.steps)} completed; maximum 4"
                )
                raise ValueError(message)
            messages.append(response)
            for call in reads:
                arguments = json.loads(call["function"]["arguments"])
                resource = arguments.get("resource") if isinstance(arguments, dict) else None
                if not isinstance(resource, str) or resource not in self.resources:
                    message = f"Skill read {resource!r}: no such packaged or registered resource"
                    raise ValueError(message)
                self.loaded[resource] = self.resources[resource]
                result = {"role": "tool", "tool_call_id": call["id"], "content": self.resources[resource]}
                self.steps.append({"call": call, "result": result})
                messages.append(result)


def main() -> None:
    """Generate or check guidance locally, without running model evaluation."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="refresh managed regions only")
    mode.add_argument("--check-generated", action="store_true", help="reject drift without modifying files")
    arguments = parser.parse_args()
    changed = refresh_generated_guidance(check=arguments.check_generated)
    if arguments.check_generated and changed:
        raise SystemExit(
            "Generated integration guidance is stale:\n" + "\n".join(str(path.relative_to(ROOT)) for path in changed)
        )
    for directory in FILE_HOSTS.values():
        file_skill(ROOT / directory)


if __name__ == "__main__":
    main()
