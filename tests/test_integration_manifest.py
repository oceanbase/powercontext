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

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path

import pytest
from integration_manifest import (
    DOCUMENTATION_PATHS,
    MANIFEST_PATH,
    IntegrationAvailability,
    IntegrationManifest,
    evidence_path_errors,
    load_integration_manifest,
    release_tag_errors,
    render_integration_capability_reference,
    tool_surface_errors,
)
from pydantic import ValidationError

from powercontext.cli.system import setup_app


def test_manifest_matches_setup_catalog_evidence_and_actual_tool_surfaces() -> None:
    manifest = load_integration_manifest()

    agent_hosts = {integration.id for integration in manifest.integrations if integration.kind == "agent_host"}
    setup_targets = {command.name for command in setup_app.registered_commands if command.name != "select"}

    assert agent_hosts == setup_targets
    assert evidence_path_errors(manifest) == ()
    assert release_tag_errors(manifest) == ()
    assert tool_surface_errors(manifest) == ()


def test_manifest_defines_each_availability_state() -> None:
    manifest = load_integration_manifest()

    assert set(manifest.availability_definitions) == set(IntegrationAvailability)


@pytest.mark.parametrize("locale", ["en", "zh"])
def test_generated_capability_matrix_is_current(locale: str) -> None:
    manifest = load_integration_manifest()

    assert DOCUMENTATION_PATHS[locale].read_text(encoding="utf-8") == render_integration_capability_reference(
        manifest, locale
    )


def test_tool_surface_probe_rejects_renamed_or_added_tools() -> None:
    payload = load_integration_manifest().model_dump(mode="json")
    payload["toolsets"][0]["tools"][0]["id"] = "renamed_memory_search"
    manifest = IntegrationManifest.model_validate(payload)

    errors = tool_surface_errors(manifest)

    assert errors
    assert "server-mcp: tool surface drift" in errors[0]
    assert "renamed_memory_search" in errors[0]
    assert "search_memory" in errors[0]


@pytest.mark.parametrize(
    ("integration_id", "config_name", "command", "arguments"),
    [
        ("codex", "hooks.json", "/bin/true", None),
        (
            "claude-code",
            "hooks.json",
            "/bin/true",
            ["${CLAUDE_PLUGIN_ROOT}/hooks/user_prompt_submit.py"],
        ),
        (
            "claude-code",
            "hooks.json",
            "python3",
            [
                "${CLAUDE_PLUGIN_ROOT}/hooks/missing.py",
                "${CLAUDE_PLUGIN_ROOT}/hooks/user_prompt_submit.py",
            ],
        ),
        (
            "codex",
            "hooks.json",
            "uv run --frozen /bin/true python ${PLUGIN_ROOT}/hooks/recall.py",
            None,
        ),
    ],
)
def test_prompt_hook_probe_follows_registered_command(
    tmp_path: Path,
    integration_id: str,
    config_name: str,
    command: str,
    arguments: list[str] | None,
) -> None:
    for target_id in ("codex", "claude-code", "workbuddy"):
        relative_path = Path(target_id, "plugins", "powercontext", "hooks")
        shutil.copytree(MANIFEST_PATH.parent / relative_path, tmp_path / "integrations" / relative_path)

    config_path = tmp_path / "integrations" / integration_id / "plugins/powercontext/hooks" / config_name
    config = json.loads(config_path.read_text(encoding="utf-8"))
    command_hook = config["hooks"]["UserPromptSubmit"][0]["hooks"][0]
    command_hook["command"] = command
    if arguments is None:
        command_hook.pop("args", None)
    else:
        command_hook["args"] = arguments
    config_path.write_text(json.dumps(config), encoding="utf-8")

    payload = load_integration_manifest().model_dump(mode="json")
    payload["toolsets"] = [toolset for toolset in payload["toolsets"] if toolset["id"] == "prompt-hooks"]
    payload["integrations"] = [
        {
            "id": "codex",
            "kind": "agent_host",
            "availability": "master_only",
            "capabilities": ["source_capture", "context_injection", "flush_or_checkpoint"],
            "toolsets": ["prompt-hooks"],
            "evidence": {
                "implementation": ["integrations/codex/plugins/powercontext/hooks/recall.py"],
                "documentation": ["integrations/codex/README.md"],
                "tests": ["tests/test_integration_manifest.py"],
            },
        }
    ]
    manifest = IntegrationManifest.model_validate(payload)

    assert tool_surface_errors(manifest, tmp_path) == (
        f"prompt-hooks: tool surface drift (missing ['{integration_id}:UserPromptSubmit']; "
        f"unexpected ['{integration_id}:UserPromptSubmit:incomplete'])",
    )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload["integrations"].append(deepcopy(payload["integrations"][0])),
        lambda payload: payload["integrations"][0].__setitem__("profiles", ["minimal"]),
        lambda payload: payload["integrations"][8].__setitem__("profiles", ["minimal"]),
        lambda payload: payload["integrations"][0].__setitem__("capabilities", ["unknown"]),
        lambda payload: payload["integrations"][0].__setitem__("toolsets", ["unknown-toolset"]),
        lambda payload: payload["integrations"][0]["evidence"].__setitem__("tests", []),
        lambda payload: payload["availability_definitions"].pop("released"),
        lambda payload: payload["toolsets"][0]["tools"][0].__setitem__(
            "non_profile_reason", "duplicate classification"
        ),
        lambda payload: payload["integrations"][0].__setitem__("availability", "released"),
    ],
)
def test_manifest_rejects_contradictory_implemented_declarations(mutation) -> None:
    payload = load_integration_manifest().model_dump(mode="json")
    mutation(payload)

    with pytest.raises(ValidationError):
        IntegrationManifest.model_validate(payload)


def test_manifest_accepts_planned_capabilities_without_claiming_current_support() -> None:
    payload = load_integration_manifest().model_dump(mode="json")
    payload["integrations"].append({
        "id": "future-host",
        "kind": "agent_host",
        "availability": "proposed",
        "capabilities": ["memory_read"],
        "proposal": "https://github.com/oceanbase/powercontext/issues/1357",
    })

    manifest = IntegrationManifest.model_validate(payload)

    assert manifest.integrations[-1].availability == "proposed"


@pytest.mark.parametrize(
    ("availability", "pointer", "expected_error"),
    [
        ("proposed", "not-a-proposal", "future-host: invalid proposal pointer: not-a-proposal"),
        (
            "unsupported",
            "https://github.com/oceanbase/powercontext/issues/1357",
            "future-host: invalid rationale pointer: https://github.com/oceanbase/powercontext/issues/1357",
        ),
    ],
)
def test_unimplemented_status_pointers_must_have_their_required_evidence(
    availability: str, pointer: str, expected_error: str
) -> None:
    payload = load_integration_manifest().model_dump(mode="json")
    payload["integrations"].append({
        "id": "future-host",
        "kind": "agent_host",
        "availability": availability,
        "proposal" if availability == "proposed" else "rationale": pointer,
    })
    manifest = IntegrationManifest.model_validate(payload)

    assert evidence_path_errors(manifest) == (expected_error,)


@pytest.mark.parametrize(
    "declaration",
    [
        {
            "id": "missing-proposal",
            "kind": "agent_host",
            "availability": "proposed",
        },
        {
            "id": "unsupported-with-capability",
            "kind": "agent_host",
            "availability": "unsupported",
            "capabilities": ["memory_read"],
            "rationale": "docs/en/docs/reference/interfaces.md",
        },
        {
            "id": "unsupported-without-rationale",
            "kind": "agent_host",
            "availability": "unsupported",
        },
        {
            "id": "unsupported-with-implementation",
            "kind": "agent_host",
            "availability": "unsupported",
            "rationale": "docs/en/docs/reference/interfaces.md",
            "evidence": {"implementation": ["src/powercontext/integration_manifest.py"]},
        },
    ],
)
def test_manifest_rejects_invalid_unimplemented_status_evidence(declaration) -> None:
    payload = load_integration_manifest().model_dump(mode="json")
    payload["integrations"].append(declaration)

    with pytest.raises(ValidationError):
        IntegrationManifest.model_validate(payload)


def test_released_entries_need_a_resolvable_tag() -> None:
    payload = load_integration_manifest().model_dump(mode="json")
    payload["integrations"][0]["availability"] = "released"
    payload["integrations"][0]["release_tag"] = "not-a-powercontext-release"
    manifest = IntegrationManifest.model_validate(payload)

    assert release_tag_errors(manifest) == ("codex: release tag does not resolve: not-a-powercontext-release",)
