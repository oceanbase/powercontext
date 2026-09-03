# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ruff: noqa: C901, RUF001, S603, S607, SIM102, TRY003, TRY004

"""Repository contract for PowerContext integration capabilities."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
import tomllib
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class IntegrationKind(StrEnum):
    AGENT_HOST = "agent_host"
    FRAMEWORK_ADAPTER = "framework_adapter"
    EVALUATION_HARNESS = "evaluation_harness"


class IntegrationAvailability(StrEnum):
    RELEASED = "released"
    MASTER_ONLY = "master_only"
    EXPERIMENTAL = "experimental"
    PROPOSED = "proposed"
    UNSUPPORTED = "unsupported"


class IntegrationCapability(StrEnum):
    """A user-observable capability, never an authorization grant.

    candidate_review permits listing and reading candidates only. It does not
    imply approval, rejection, revision, or any other decision authority.
    """

    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"
    SOURCE_CAPTURE = "source_capture"
    CONTEXT_INJECTION = "context_injection"
    FLUSH_OR_CHECKPOINT = "flush_or_checkpoint"
    WORK_CONTRACT = "work_contract"
    HANDOFF = "handoff"
    ACKNOWLEDGE = "acknowledge"
    TASK_OUTCOME = "task_outcome"
    EXPERIENCE_READ_OR_GENERATE = "experience_read_or_generate"
    SKILL_READ_OR_GENERATE = "skill_read_or_generate"
    CANDIDATE_REVIEW = "candidate_review"
    EXTERNAL_SKILL = "external_skill"
    PRE_COMPACTION_CAPTURE = "pre_compaction_capture"
    SLASH_COMMAND = "slash_command"
    PERSISTENT_WORKSTREAM_BINDING = "persistent_workstream_binding"


class SupportProfile(StrEnum):
    MINIMAL = "minimal"
    RECOMMENDED = "recommended"
    FULL = "full"


class ToolSurfaceProbe(StrEnum):
    SERVER_MCP = "server_mcp"
    JSON_PROMPT_HOOK = "json_prompt_hook"
    DSH_TOOLS = "dsh_tools"
    DSH_COMMANDS = "dsh_commands"
    HERMES_OPERATIONS = "hermes_operations"
    HERMES_COMMANDS = "hermes_commands"
    OPENCLAW_TOOLS = "openclaw_tools"
    OPENCLAW_LIFECYCLE = "openclaw_lifecycle"
    OPENCODE_TOOLS = "opencode_tools"
    PI_TOOLS = "pi_tools"
    PI_COMMANDS = "pi_commands"
    PI_LIFECYCLE = "pi_lifecycle"
    PYDANTIC_AI_TOOLS = "pydantic_ai_tools"
    LANGCHAIN_MIDDLEWARE = "langchain_middleware"
    LANGGRAPH_TOOLS = "langgraph_tools"
    BUB_TOOLS = "bub_tools"
    BUB_LIFECYCLE = "bub_lifecycle"


MINIMAL_CAPABILITIES = frozenset({
    IntegrationCapability.MEMORY_READ,
    IntegrationCapability.MEMORY_WRITE,
    IntegrationCapability.SOURCE_CAPTURE,
    IntegrationCapability.CONTEXT_INJECTION,
})
RECOMMENDED_CAPABILITIES = MINIMAL_CAPABILITIES | frozenset({
    IntegrationCapability.WORK_CONTRACT,
    IntegrationCapability.HANDOFF,
    IntegrationCapability.ACKNOWLEDGE,
    IntegrationCapability.TASK_OUTCOME,
})
FULL_CAPABILITIES = RECOMMENDED_CAPABILITIES | frozenset({
    IntegrationCapability.EXPERIENCE_READ_OR_GENERATE,
    IntegrationCapability.SKILL_READ_OR_GENERATE,
    IntegrationCapability.CANDIDATE_REVIEW,
    IntegrationCapability.EXTERNAL_SKILL,
})
IMPLEMENTED_AVAILABILITY = frozenset({
    IntegrationAvailability.RELEASED,
    IntegrationAvailability.MASTER_ONLY,
    IntegrationAvailability.EXPERIMENTAL,
})


class IntegrationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    implementation: tuple[str, ...] = ()
    documentation: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()

    @field_validator("implementation", "documentation", "tests")
    @classmethod
    def validate_relative_paths(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        if len(paths) != len(set(paths)):
            raise ValueError("evidence paths must be unique")
        for value in paths:
            path = Path(value)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("evidence paths must be repository-relative")
        return paths

    @property
    def is_complete(self) -> bool:
        return bool(self.implementation and self.documentation and self.tests)

    @property
    def has_any(self) -> bool:
        return bool(self.implementation or self.documentation or self.tests)


class AvailabilityDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    en: str = Field(min_length=1)
    zh: str = Field(min_length=1)


class ToolExposure(BaseModel):
    """A normalized host-visible tool, hook, command, or adapter surface."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[A-Za-z0-9_.:-]+$")
    capabilities: tuple[IntegrationCapability, ...] = ()
    non_profile_reason: str | None = None

    @field_validator("capabilities")
    @classmethod
    def validate_unique_capabilities(
        cls, values: tuple[IntegrationCapability, ...]
    ) -> tuple[IntegrationCapability, ...]:
        if len(values) != len(set(values)):
            raise ValueError("tool capabilities must not contain duplicates")
        return values

    @model_validator(mode="after")
    def validate_classification(self) -> ToolExposure:
        if self.capabilities and self.non_profile_reason is not None:
            raise ValueError("a tool is either capability-mapped or explicitly outside profiles")
        if not self.capabilities and not self.non_profile_reason:
            raise ValueError("a tool without capabilities needs a non_profile_reason")
        return self


class IntegrationToolset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?$")
    probe: ToolSurfaceProbe
    tools: tuple[ToolExposure, ...] = Field(min_length=1)

    @field_validator("tools")
    @classmethod
    def validate_unique_tools(cls, tools: tuple[ToolExposure, ...]) -> tuple[ToolExposure, ...]:
        if len({tool.id for tool in tools}) != len(tools):
            raise ValueError("toolset tool ids must be unique")
        return tools


class IntegrationDeclaration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^[a-z0-9]+(?:[a-z0-9-]*[a-z0-9])?$")
    kind: IntegrationKind
    availability: IntegrationAvailability
    capabilities: tuple[IntegrationCapability, ...] = ()
    profiles: tuple[SupportProfile, ...] = ()
    toolsets: tuple[str, ...] = ()
    evidence: IntegrationEvidence = Field(default_factory=IntegrationEvidence)
    release_tag: str | None = None
    proposal: str | None = None
    rationale: str | None = None

    @field_validator("capabilities", "profiles", "toolsets")
    @classmethod
    def validate_unique_values(cls, values: tuple[StrEnum | str, ...]) -> tuple[StrEnum | str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("capabilities, profiles, and toolsets must not contain duplicates")
        return values


class IntegrationManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(ge=1)
    availability_definitions: dict[IntegrationAvailability, AvailabilityDefinition]
    toolsets: tuple[IntegrationToolset, ...] = ()
    integrations: tuple[IntegrationDeclaration, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_declarations(self) -> IntegrationManifest:
        if self.schema_version != 1:
            raise ValueError(f"unsupported integration manifest schema version: {self.schema_version}")
        if set(self.availability_definitions) != set(IntegrationAvailability):
            raise ValueError("availability definitions must cover every availability state")
        if any(
            not description.strip()
            for definition in self.availability_definitions.values()
            for description in (definition.en, definition.zh)
        ):
            raise ValueError("availability definitions must not be empty")
        toolsets = {toolset.id: toolset for toolset in self.toolsets}
        if len(toolsets) != len(self.toolsets):
            raise ValueError("toolset ids must be unique")
        if len({integration.id for integration in self.integrations}) != len(self.integrations):
            raise ValueError("integration ids must be unique")
        used_toolsets: set[str] = set()
        for integration in self.integrations:
            unknown = set(integration.toolsets) - toolsets.keys()
            if unknown:
                raise ValueError(f"{integration.id}: unknown toolsets: {sorted(unknown)!r}")
            used_toolsets.update(integration.toolsets)
            if integration.availability in IMPLEMENTED_AVAILABILITY:
                if not integration.evidence.is_complete:
                    raise ValueError(f"{integration.id}: implemented integrations need implementation, docs, and tests")
                if not integration.toolsets:
                    raise ValueError(f"{integration.id}: implemented integrations need a tool surface assertion")
                if integration.availability is IntegrationAvailability.RELEASED and not integration.release_tag:
                    raise ValueError(f"{integration.id}: released integrations need a release_tag")
                if integration.proposal or integration.rationale:
                    raise ValueError(
                        f"{integration.id}: implemented integrations cannot use proposal or rationale evidence"
                    )
                if frozenset(integration.capabilities) != capabilities_from_toolsets(integration.toolsets, toolsets):
                    raise ValueError(f"{integration.id}: capabilities must equal its tool surface capabilities")
            elif integration.availability is IntegrationAvailability.PROPOSED:
                if not integration.proposal:
                    raise ValueError(f"{integration.id}: proposed integrations need a proposal pointer")
                if integration.toolsets or integration.profiles:
                    raise ValueError(f"{integration.id}: proposed integrations cannot claim current support")
                if integration.release_tag or integration.rationale:
                    raise ValueError(f"{integration.id}: proposed integrations need only proposal evidence")
            else:
                if not integration.rationale:
                    raise ValueError(f"{integration.id}: unsupported integrations need a rationale pointer")
                if (
                    integration.capabilities
                    or integration.profiles
                    or integration.toolsets
                    or integration.evidence.has_any
                ):
                    raise ValueError(f"{integration.id}: unsupported integrations cannot claim current support")
                if integration.release_tag or integration.proposal:
                    raise ValueError(f"{integration.id}: unsupported integrations need only rationale evidence")
            if integration.kind is not IntegrationKind.AGENT_HOST and integration.profiles:
                raise ValueError("only agent_host integrations may declare support profiles")
            if integration.kind is IntegrationKind.AGENT_HOST and integration.availability in IMPLEMENTED_AVAILABILITY:
                if frozenset(integration.profiles) != derived_profiles(integration.capabilities):
                    raise ValueError(f"agent_host {integration.id!r} profiles must equal its derived profiles")
        unused = set(toolsets) - used_toolsets
        if unused:
            raise ValueError(f"toolsets must be referenced by an integration: {sorted(unused)!r}")
        return self


def capabilities_from_toolsets(
    toolset_ids: Iterable[str], toolsets: dict[str, IntegrationToolset]
) -> frozenset[IntegrationCapability]:
    return frozenset(
        capability
        for toolset_id in toolset_ids
        for tool in toolsets[toolset_id].tools
        for capability in tool.capabilities
    )


def derived_profiles(capabilities: Iterable[IntegrationCapability]) -> frozenset[SupportProfile]:
    declared = frozenset(capabilities)
    profiles: set[SupportProfile] = set()
    if declared >= MINIMAL_CAPABILITIES:
        profiles.add(SupportProfile.MINIMAL)
    if declared >= RECOMMENDED_CAPABILITIES:
        profiles.add(SupportProfile.RECOMMENDED)
    if declared >= FULL_CAPABILITIES:
        profiles.add(SupportProfile.FULL)
    return frozenset(profiles)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPOSITORY_ROOT / "integrations" / "capabilities.toml"
DOCUMENTATION_PATHS = {
    "en": REPOSITORY_ROOT / "docs" / "en" / "docs" / "reference" / "integration-capabilities.md",
    "zh": REPOSITORY_ROOT / "docs" / "zh" / "docs" / "reference" / "integration-capabilities.md",
}


def load_integration_manifest(path: Path = MANIFEST_PATH) -> IntegrationManifest:
    with path.open("rb") as handle:
        return IntegrationManifest.model_validate(tomllib.load(handle))


def evidence_path_errors(manifest: IntegrationManifest, repository_root: Path = REPOSITORY_ROOT) -> tuple[str, ...]:
    errors: list[str] = []
    for integration in manifest.integrations:
        if (
            integration.availability in IMPLEMENTED_AVAILABILITY
            or integration.availability is IntegrationAvailability.PROPOSED
        ):
            for category, paths in (
                ("implementation", integration.evidence.implementation),
                ("documentation", integration.evidence.documentation),
                ("tests", integration.evidence.tests),
            ):
                for pointer in paths:
                    resolved = (repository_root / pointer).resolve()
                    if not resolved.is_relative_to(repository_root.resolve()):
                        errors.append(f"{integration.id}: {category} escapes the repository: {pointer}")
                    elif not resolved.is_file():
                        errors.append(f"{integration.id}: missing {category} evidence: {pointer}")
        if integration.availability is IntegrationAvailability.PROPOSED:
            errors.extend(_proposal_pointer_errors(integration.id, integration.proposal, repository_root))
        elif integration.availability is IntegrationAvailability.UNSUPPORTED:
            errors.extend(_rationale_pointer_errors(integration.id, integration.rationale, repository_root))
    return tuple(errors)


def _proposal_pointer_errors(integration_id: str, pointer: str | None, repository_root: Path) -> list[str]:
    if pointer is None:
        return [f"{integration_id}: missing proposal pointer"]
    if re.fullmatch(r"https://github\.com/[^/]+/[^/]+/(?:issues|pull)/\d+(?:#.*)?", pointer):
        return []
    path = Path(pointer)
    if (
        path.is_absolute()
        or ".." in path.parts
        or "rfcs" not in path.parts
        or path.suffix != ".md"
        or not (repository_root / path).is_file()
    ):
        return [f"{integration_id}: invalid proposal pointer: {pointer}"]
    return []


def _rationale_pointer_errors(integration_id: str, pointer: str | None, repository_root: Path) -> list[str]:
    if pointer is None:
        return [f"{integration_id}: missing rationale pointer"]
    path = Path(pointer)
    if (
        path.is_absolute()
        or ".." in path.parts
        or path.parts[:1] != ("docs",)
        or path.suffix != ".md"
        or not (repository_root / path).is_file()
    ):
        return [f"{integration_id}: invalid rationale pointer: {pointer}"]
    return []


def release_tag_errors(manifest: IntegrationManifest, repository_root: Path = REPOSITORY_ROOT) -> tuple[str, ...]:
    errors: list[str] = []
    for integration in manifest.integrations:
        if integration.availability is not IntegrationAvailability.RELEASED:
            continue
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/tags/{integration.release_tag}"],
            cwd=repository_root,
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            errors.append(f"{integration.id}: release tag does not resolve: {integration.release_tag}")
    return tuple(errors)


def tool_surface_errors(manifest: IntegrationManifest, repository_root: Path = REPOSITORY_ROOT) -> tuple[str, ...]:
    errors: list[str] = []
    for toolset in manifest.toolsets:
        try:
            actual = _probe_toolset(toolset.probe, repository_root)
        except ValueError as error:
            errors.append(f"{toolset.id}: {error}")
            continue
        expected = {tool.id for tool in toolset.tools}
        if actual != expected:
            missing = sorted(expected - actual)
            unexpected = sorted(actual - expected)
            errors.append(f"{toolset.id}: tool surface drift (missing {missing!r}; unexpected {unexpected!r})")
    for integration in manifest.integrations:
        if "server-mcp" in integration.toolsets:
            errors.extend(_mcp_configuration_errors(integration.id, repository_root))
    return tuple(errors)


def _probe_toolset(probe: ToolSurfaceProbe, root: Path) -> set[str]:
    if probe is ToolSurfaceProbe.SERVER_MCP:
        from powercontext.server.mcp import _MCP_OPERATION_IDS

        return set(_MCP_OPERATION_IDS)
    if probe is ToolSurfaceProbe.JSON_PROMPT_HOOK:
        return _prompt_hook_ids(root)
    if probe is ToolSurfaceProbe.DSH_TOOLS:
        return _typescript_operation_tools(root / "integrations/dsh/plugins/powercontext/src/tools.ts", "pcTool")
    if probe is ToolSurfaceProbe.OPENCODE_TOOLS:
        return _typescript_operation_tools(
            root / "integrations/opencode/plugins/powercontext/src/index.ts", "operationTool"
        )
    if probe is ToolSurfaceProbe.PI_TOOLS:
        return _typescript_operation_tools(
            root / "integrations/pi/plugins/powercontext/src/tools.ts", "registerOperationTool"
        )
    if probe is ToolSurfaceProbe.DSH_COMMANDS:
        return _command_id(root / "integrations/dsh/plugins/powercontext/src/commands.ts", "commands.register")
    if probe is ToolSurfaceProbe.PI_COMMANDS:
        return _command_id(root / "integrations/pi/plugins/powercontext/src/commands.ts", "pi.registerCommand")
    if probe is ToolSurfaceProbe.PI_LIFECYCLE:
        return _typescript_event_markers(
            root / "integrations/pi/plugins/powercontext/extensions/powercontext.ts",
            {"session_before_compact:flush_memory": "flushPending"},
        )
    if probe is ToolSurfaceProbe.HERMES_OPERATIONS:
        return _python_string_dict_keys(
            root / "integrations/hermes/plugins/powercontext/operations.py", "OPERATION_TOOL_MAP"
        )
    if probe is ToolSurfaceProbe.HERMES_COMMANDS:
        return _hermes_command_ids(root)
    if probe is ToolSurfaceProbe.OPENCLAW_TOOLS:
        return set(
            re.findall(
                r'POWERCONTEXT_MEMORY_[A-Z_]+_TOOL = "([^"]+)"',
                _read(root, "integrations/openclaw/plugins/memory-powercontext/src/tools.ts"),
            )
        )
    if probe is ToolSurfaceProbe.OPENCLAW_LIFECYCLE:
        return _typescript_event_markers(
            root / "integrations/openclaw/plugins/memory-powercontext/src/lifecycle.ts",
            {
                "before_prompt_build:prepare_context": "/v1/context/prepare",
                "before_compaction:flush_memory": "await flush(",
            },
        )
    if probe is ToolSurfaceProbe.PYDANTIC_AI_TOOLS:
        return _pydantic_ai_tool_ids(root)
    if probe is ToolSurfaceProbe.LANGCHAIN_MIDDLEWARE:
        return _python_method_markers(
            root / "integrations/langchain/src/powercontext_langchain/middleware.py",
            {
                "wrap_model_call:prepare_context": ("wrap_model_call", "_prepare("),
                "after_agent:capture_content_source": ("after_agent", "_capture("),
            },
        )
    if probe is ToolSurfaceProbe.LANGGRAPH_TOOLS:
        return _python_decorator_tool_ids(root / "integrations/langgraph/src/powercontext_langgraph/tools.py")
    if probe is ToolSurfaceProbe.BUB_TOOLS:
        return _python_decorator_tool_ids(root / "integrations/bub/src/powercontext_bub/tools.py")
    if probe is ToolSurfaceProbe.BUB_LIFECYCLE:
        return _python_method_markers(
            root / "integrations/bub/src/powercontext_bub/plugin.py",
            {
                "before_llm_call:prepare_context": ("before_llm_call", "_prepare_context("),
                "before_llm_call:capture_content_source": ("before_llm_call", "_capture_event("),
                "save_state:flush_memory": ("save_state", "_flush_captured_sources("),
            },
        )
    raise ValueError(f"unimplemented tool surface probe: {probe}")


def _read(root: Path, relative_path: str) -> str:
    return (root / relative_path).read_text(encoding="utf-8")


def _typescript_operation_tools(path: Path, helper: str) -> set[str]:
    source = path.read_text(encoding="utf-8")
    expression = (
        rf"(?:name:\s*'(?P<name>pc_[a-z_]+)'|(?P<key>pc_[a-z_]+):\s*{helper})"
        rf"[\s\S]{{0,900}}?(?:operationId:\s*'|run\(runtime, exec, ')(?P<operation>[a-z_]+)'"
    )
    pairs = {
        f"{match.group('name') or match.group('key')}:{match.group('operation')}"
        for match in re.finditer(expression, source)
    }
    if not pairs:
        raise ValueError(f"could not extract {helper} registrations")
    return pairs


def _command_id(path: Path, registration: str) -> set[str]:
    source = path.read_text(encoding="utf-8")
    if registration not in source:
        raise ValueError(f"missing {registration} registration")
    match = re.search(r"(?:name:\s*|registerCommand\()'([^']+)'", source)
    if match is None:
        raise ValueError("could not extract command name")
    return {match.group(1)}


def _python_string_dict_keys(path: Path, dictionary: str) -> set[str]:
    source = path.read_text(encoding="utf-8")
    match = re.search(rf"{dictionary}:.*?=\s*\{{(?P<body>[\s\S]*?)\n\}}", source)
    if match is None:
        raise ValueError(f"could not find {dictionary}")
    return set(re.findall(r'^\s*"([^"]+)"\s*:', match.group("body"), re.MULTILINE))


def _hermes_command_ids(root: Path) -> set[str]:
    source = _read(root, "integrations/hermes/plugins/powercontext/commands.py")
    if "POWERCONTEXT_SUBCOMMANDS" not in source or '"workstream"' not in source:
        raise ValueError("Hermes command surface is incomplete")
    return {"pc", "powercontext"}


def _prompt_hook_ids(root: Path) -> set[str]:
    hooks = {
        "codex": (
            "integrations/codex/plugins/powercontext/hooks/hooks.json",
            {"${PLUGIN_ROOT}": Path("..")},
        ),
        "claude-code": (
            "integrations/claude-code/plugins/powercontext/hooks/hooks.json",
            {"${CLAUDE_PLUGIN_ROOT}": Path("..")},
        ),
        "workbuddy": (
            "integrations/workbuddy/plugins/powercontext/hooks/hooks.workbuddy.json",
            {"<WORKBUDDY_HOOKS_DIR>": Path(".")},
        ),
    }
    result: set[str] = set()
    for integration_id, (json_path, placeholder_roots) in hooks.items():
        payload = json.loads(_read(root, json_path))
        events = payload.get("hooks")
        if not isinstance(events, dict):
            raise ValueError(f"{integration_id} hook config has no hooks map")
        config_path = root / json_path
        for event, registrations in events.items():
            scripts = _registered_hook_scripts(
                integration_id,
                event,
                registrations,
                config_path,
                placeholder_roots,
            )
            if event == "UserPromptSubmit" and any(_is_complete_prompt_hook(script) for script in scripts):
                result.add(f"{integration_id}:{event}")
            else:
                result.add(f"{integration_id}:{event}:incomplete")
    return result


def _registered_hook_scripts(
    integration_id: str,
    event: str,
    registrations: object,
    config_path: Path,
    placeholder_roots: dict[str, Path],
) -> set[Path]:
    if not isinstance(registrations, list):
        raise ValueError(f"{integration_id} {event} hook registrations must be a list")
    scripts: set[Path] = set()
    for registration in registrations:
        if not isinstance(registration, dict):
            raise ValueError(f"{integration_id} {event} hook registration must be an object")
        commands = registration.get("hooks")
        if not isinstance(commands, list):
            raise ValueError(f"{integration_id} {event} hook registration has no hooks list")
        for command in commands:
            script = _python_script_from_hook_command(
                integration_id,
                event,
                command,
                config_path,
                placeholder_roots,
            )
            if script is not None:
                scripts.add(script)
    return scripts


def _python_script_from_hook_command(
    integration_id: str,
    event: str,
    command: object,
    config_path: Path,
    placeholder_roots: dict[str, Path],
) -> Path | None:
    if not isinstance(command, dict) or command.get("type") != "command":
        return None
    executable = command.get("command")
    if not isinstance(executable, str):
        raise ValueError(f"{integration_id} {event} command hook has no command")
    try:
        tokens = shlex.split(executable)
    except ValueError as error:
        raise ValueError(f"{integration_id} {event} command hook cannot be parsed") from error
    arguments = command.get("args", [])
    if not isinstance(arguments, list):
        raise ValueError(f"{integration_id} {event} command hook args must be a string list")
    for argument in arguments:
        if not isinstance(argument, str):
            raise ValueError(f"{integration_id} {event} command hook args must be a string list")
        tokens.append(argument)
    invocation = _unwrap_uv_run(tokens)
    if len(invocation) < 2 or not _is_python_executable(invocation[0]):
        return None
    script = invocation[1]
    if not script.endswith(".py"):
        return None
    return _resolve_registered_hook_script(script, config_path, placeholder_roots)


def _unwrap_uv_run(tokens: list[str]) -> list[str]:
    if not tokens or Path(tokens[0]).name != "uv":
        return tokens
    if len(tokens) < 2 or tokens[1] != "run":
        return []
    index = 2
    while index < len(tokens):
        option = tokens[index]
        if option in {"--frozen", "--quiet"}:
            index += 1
        elif option == "--project" and index + 1 < len(tokens):
            index += 2
        else:
            break
    return tokens[index:]


def _is_python_executable(executable: str) -> bool:
    return (
        executable == "<POWERCONTEXT_PYTHON>"
        or re.fullmatch(r"python(?:3(?:\.\d+)*)?(?:\.exe)?", Path(executable).name) is not None
    )


def _resolve_registered_hook_script(
    token: str,
    config_path: Path,
    placeholder_roots: dict[str, Path],
) -> Path:
    for placeholder, relative_root in placeholder_roots.items():
        if token == placeholder or token.startswith(f"{placeholder}/"):
            relative_path = token.removeprefix(placeholder).lstrip("/")
            candidate = config_path.parent / relative_root / relative_path
            break
    else:
        candidate = Path(token)
        if not candidate.is_absolute():
            candidate = config_path.parent / candidate
    resolved = candidate.resolve()
    plugin_root = config_path.parent.parent.resolve()
    if not resolved.is_relative_to(plugin_root):
        raise ValueError(f"registered hook script escapes the plugin root: {token}")
    return resolved


def _is_complete_prompt_hook(script: Path) -> bool:
    if not script.is_file():
        return False
    source = script.read_text(encoding="utf-8")
    return all(marker in source for marker in ("/v1/context/prepare", "/v1/sources/content", "/v1/memory/flush"))


def _python_method_markers(path: Path, markers: dict[str, tuple[str, str]]) -> set[str]:
    source = path.read_text(encoding="utf-8")
    actual: set[str] = set()
    for identifier, (method, marker) in markers.items():
        method_match = re.search(
            rf"(?:async\s+)?def {method}\([^)]*\).*?(?=\n    (?:async\s+)?def |\Z)", source, re.DOTALL
        )
        if method_match is None or marker not in method_match.group(0):
            raise ValueError(f"missing {method} surface for {marker}")
        actual.add(identifier)
    return actual


def _typescript_event_markers(path: Path, markers: dict[str, str]) -> set[str]:
    source = path.read_text(encoding="utf-8")
    actual: set[str] = set()
    for identifier, marker in markers.items():
        event, _ = identifier.split(":", maxsplit=1)
        match = re.search(rf'(?:api|pi)\.on\(["\']{event}["\'][\s\S]*?(?=\n  (?:api|pi)\.on|\n\}}\n|\Z)', source)
        if match is None or marker not in match.group(0):
            raise ValueError(f"missing {event} surface for {marker}")
        actual.add(identifier)
    return actual


def _pydantic_ai_tool_ids(root: Path) -> set[str]:
    return set(
        re.findall(
            r"self\.add_function\(\s*self\.(powercontext_[a-z_]+)",
            _read(root, "integrations/pydantic-ai/src/powercontext_pydantic_ai/toolset.py"),
        )
    )


def _python_decorator_tool_ids(path: Path) -> set[str]:
    identifiers = set(re.findall(r'@tool\([^\n]*?(?:name=)?"([^"]+)"', path.read_text(encoding="utf-8")))
    if not identifiers:
        raise ValueError("could not extract decorated tools")
    return identifiers


def _mcp_configuration_errors(integration_id: str, root: Path) -> list[str]:
    paths = {
        "codex": "integrations/codex/plugins/powercontext/.mcp.json",
        "claude-code": "integrations/claude-code/plugins/powercontext/.mcp.json",
        "workbuddy": "integrations/workbuddy/plugins/powercontext/.mcp.json",
    }
    path = paths.get(integration_id)
    if path is None:
        return []
    serialized = json.dumps(json.loads(_read(root, path)))
    return (
        []
        if "/mcp" in serialized and "powercontext" in serialized
        else [f"{integration_id}: missing PowerContext MCP config"]
    )


_KIND_LABELS = {
    "en": {
        IntegrationKind.AGENT_HOST: "Agent host",
        IntegrationKind.FRAMEWORK_ADAPTER: "Framework adapter",
        IntegrationKind.EVALUATION_HARNESS: "Evaluation harness",
    },
    "zh": {
        IntegrationKind.AGENT_HOST: "Agent 宿主",
        IntegrationKind.FRAMEWORK_ADAPTER: "框架适配器",
        IntegrationKind.EVALUATION_HARNESS: "评测 harness",
    },
}
_AVAILABILITY_LABELS = {
    "en": {
        IntegrationAvailability.RELEASED: "Released",
        IntegrationAvailability.MASTER_ONLY: "Master only",
        IntegrationAvailability.EXPERIMENTAL: "Experimental",
        IntegrationAvailability.PROPOSED: "Proposed",
        IntegrationAvailability.UNSUPPORTED: "Unsupported",
    },
    "zh": {
        IntegrationAvailability.RELEASED: "已发布",
        IntegrationAvailability.MASTER_ONLY: "仅 master",
        IntegrationAvailability.EXPERIMENTAL: "实验性",
        IntegrationAvailability.PROPOSED: "提议中",
        IntegrationAvailability.UNSUPPORTED: "不支持",
    },
}


def render_integration_capability_reference(manifest: IntegrationManifest, locale: str) -> str:
    """Render the concise, generated capability matrix for one documentation locale."""

    if locale not in {"en", "zh"}:
        raise ValueError(f"unsupported locale: {locale}")
    if locale == "en":
        front_matter = (
            "---\n"
            "title: Integration capability matrix\n"
            "description: Generated capability matrix for PowerContext integrations.\n"
            "---\n\n# Integration capability matrix\n\n"
            "This generated matrix is a repository contract, not a public HTTP capability API.\n\n"
            "candidate_review permits listing and reading candidates only; it never grants decision authority.\n\n"
            "## Availability\n\n"
        )
        profile_heading = "## Support profiles\n\n"
        profile_lines = (
            "- **Minimal**: Memory read/write, Source capture, and Context injection.\n"
            "- **Recommended**: Minimal plus Work Contract, Handoff, acknowledgement, and Task Outcome.\n"
            "- **Full**: Recommended plus Experience, Skill, Candidate review, and External Skill.\n\n"
        )
        matrix_heading = "## Current matrix\n\n"
    else:
        front_matter = (
            "---\ntitle: 集成能力矩阵\ndescription: PowerContext 集成的生成式能力矩阵。\n"
            "---\n\n# 集成能力矩阵\n\n"
            "本矩阵由仓库 contract 生成，不是公开 HTTP capability API。\n\n"
            "candidate_review 仅可列举和读取候选材料，不授予决策权限。\n\n"
            "## 可用性\n\n"
        )
        profile_heading = "## 支持 Profile\n\n"
        profile_lines = (
            "- **Minimal**：Memory 读写、Source capture 和 Context injection。\n"
            "- **Recommended**：Minimal 加上 Work Contract、Handoff、acknowledgement 和 Task Outcome。\n"
            "- **Full**：Recommended 加上 Experience、Skill、Candidate review 和 External Skill。\n\n"
        )
        matrix_heading = "## 当前矩阵\n\n"
    availability_lines = [
        f"- **{_AVAILABILITY_LABELS[locale][availability]}**: "
        f"{getattr(manifest.availability_definitions[availability], locale)}"
        for availability in IntegrationAvailability
    ]
    rows = ["| ID | Kind | Availability | Profiles | Capabilities |", "| --- | --- | --- | --- | --- |"]
    for integration in manifest.integrations:
        profiles = ", ".join(profile.value for profile in integration.profiles) or "—"
        capabilities = "<br>".join(capability.value for capability in integration.capabilities) or "—"
        rows.append(
            f"| {integration.id} | {_KIND_LABELS[locale][integration.kind]} | "
            f"{_AVAILABILITY_LABELS[locale][integration.availability]} | {profiles} | {capabilities} |"
        )
    return (
        front_matter
        + "\n".join(availability_lines)
        + "\n\n"
        + profile_heading
        + profile_lines
        + matrix_heading
        + "\n".join(rows)
        + "\n"
    )


__all__ = [
    "DOCUMENTATION_PATHS",
    "FULL_CAPABILITIES",
    "IMPLEMENTED_AVAILABILITY",
    "MANIFEST_PATH",
    "MINIMAL_CAPABILITIES",
    "RECOMMENDED_CAPABILITIES",
    "AvailabilityDefinition",
    "IntegrationAvailability",
    "IntegrationCapability",
    "IntegrationDeclaration",
    "IntegrationEvidence",
    "IntegrationKind",
    "IntegrationManifest",
    "IntegrationToolset",
    "SupportProfile",
    "ToolExposure",
    "ToolSurfaceProbe",
    "capabilities_from_toolsets",
    "derived_profiles",
    "evidence_path_errors",
    "load_integration_manifest",
    "release_tag_errors",
    "render_integration_capability_reference",
    "tool_surface_errors",
]
