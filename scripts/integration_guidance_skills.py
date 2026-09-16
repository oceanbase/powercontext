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

This is evaluation infrastructure, not a distribution generator or native host
Skill loader. Native discovery and installation are qualified separately.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
ENTRY_NAME = "powercontext-project-context"
FILE_HOSTS = {
    host: f"integrations/{host}/plugins/powercontext/skills/powercontext-project-context"
    for host in ("codex", "claude-code", "workbuddy", "pi", "opencode")
} | {
    "agent-plugin": "integrations/agent-plugin/powercontext/skills/powercontext-project-context",
    "hermes": "integrations/hermes/plugins/powercontext/skills/powercontext-project-context",
    "minimax": "integrations/minimax/plugins/powercontext/skills/powercontext-project-context",
    "openclaw": "integrations/openclaw/plugins/memory-powercontext/skills/powercontext-project-context",
}


def file_skill(directory: Path) -> dict[str, Any]:
    """Read one entry and its reachable local references, diagnosing broken links."""
    directory = directory.resolve()
    resources: dict[str, str] = {}

    def read(relative: str, origin: str) -> None:
        path = (directory / relative).resolve()
        if not path.is_relative_to(directory) or not path.is_file():
            message = f"Skill {directory.name}: {origin} links to missing or out-of-package resource {relative}"
            raise ValueError(message)
        key = path.relative_to(directory).as_posix()
        if key in resources:
            return
        resources[key] = path.read_text(encoding="utf-8")
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
