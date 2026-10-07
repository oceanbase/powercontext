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

"""Host distribution descriptors and non-destructive generated guidance refresh."""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

ENTRY_NAME = "powercontext-project-context"
SKILL_DIRECTORY = Path("skills") / ENTRY_NAME
SKILL_FILE = SKILL_DIRECTORY / "SKILL.md"
START = "<!-- POWERCONTEXT-GUIDANCE:START -->"
YAML_START = "# POWERCONTEXT-GUIDANCE:START"
END = "<!-- POWERCONTEXT-GUIDANCE:END -->"
RESOURCE_MANIFEST = ".powercontext-guidance.json"


@dataclass(frozen=True)
class HostGuidance:
    """Distribution facts; supported capabilities are read from capabilities.toml."""

    plugin: str
    installer: str | None = None
    server_default: str | None = None
    server_option: bool = True
    empty_server_uses_default: bool = True
    capture_prompts: bool = False
    allow_insecure_http: bool = False
    target_option: bool = False
    variables: tuple[str, ...] = ()
    script_evidence: tuple[str, ...] = ()

    @property
    def skill(self) -> Path:
        return Path(self.plugin) / SKILL_DIRECTORY


HOST_GUIDANCE = {
    "codex": HostGuidance(
        "integrations/codex/plugins/powercontext",
        "powercontext.cli.system:install_codex_plugin",
        empty_server_uses_default=False,
        variables=("PLUGIN_ROOT",),
        script_evidence=("scripts/scope_binding.py",),
    ),
    "claude-code": HostGuidance(
        "integrations/claude-code/plugins/powercontext",
        "powercontext.cli.system:install_claude_code_plugin",
        server_default="http://127.0.0.1:8000",
        empty_server_uses_default=False,
        capture_prompts=True,
        allow_insecure_http=True,
        variables=("PLUGIN_ROOT",),
        script_evidence=("scripts/workspace_scope.py",),
    ),
    "dsh": HostGuidance(
        "integrations/dsh/plugins/powercontext",
        "powercontext.cli.dsh:install_dsh_plugin",
        server_default="http://127.0.0.1:8000",
        allow_insecure_http=True,
        target_option=True,
    ),
    "openclaw": HostGuidance(
        "integrations/openclaw/plugins/memory-powercontext",
        "powercontext.cli.openclaw:install_openclaw_plugin",
        server_default="http://127.0.0.1:8000",
        empty_server_uses_default=False,
        allow_insecure_http=True,
    ),
    "opencode": HostGuidance(
        "integrations/opencode/plugins/powercontext",
        "powercontext.cli.opencode:install_opencode_plugin",
        server_default="http://127.0.0.1:8000",
    ),
    "pi": HostGuidance(
        "integrations/pi/plugins/powercontext",
        "powercontext.cli.pi:install_pi_plugin",
        server_default="http://127.0.0.1:8000",
    ),
    "hermes": HostGuidance(
        "integrations/hermes/plugins/powercontext",
        "powercontext.cli.hermes:install_hermes_plugin",
        server_option=False,
    ),
    "workbuddy": HostGuidance(
        "integrations/workbuddy/plugins/powercontext",
        "powercontext.cli.workbuddy:install_workbuddy_plugin",
        empty_server_uses_default=False,
        variables=("POWERCONTEXT_PYTHON", "POWERCONTEXT_SCOPE_BINDING_SCRIPT"),
        script_evidence=("scripts/workspace_scope.py",),
    ),
    "zcode": HostGuidance(
        "integrations/zcode/plugins/powercontext",
        "powercontext.cli.zcode:install_zcode_plugin",
        server_default="http://127.0.0.1:8000",
        capture_prompts=True,
        allow_insecure_http=True,
        script_evidence=("scripts/scope.mjs",),
    ),
    "agent-plugin": HostGuidance("integrations/agent-plugin/powercontext"),
    "minimax": HostGuidance("integrations/minimax/plugins/powercontext"),
}


class GuidanceError(ValueError):
    """A refresh cannot safely identify its managed region."""


def guidance_region(content: str) -> tuple[int, int] | None:
    """Return one complete marker range, rejecting damaged or ambiguous ownership."""
    starts = content.count(START) + content.count(YAML_START)
    if not starts and END not in content:
        return None
    marker = START if START in content else YAML_START
    if starts != 1 or content.count(END) != 1 or content.index(END) < content.index(marker):
        message = "guidance needs exactly one ordered START/END marker pair"
        raise GuidanceError(message)
    return content.index(marker), content.index(END) + len(END)


def merge_guidance(existing: str, generated: str, *, legacy_sha256: str | None = None) -> str:
    """Replace only managed bytes; recognize pristine pre-marker packages on upgrade."""
    desired = guidance_region(generated)
    if desired is None:
        message = "generated guidance has no managed region"
        raise GuidanceError(message)
    current = guidance_region(existing)
    if current is None:
        digest = hashlib.sha256(existing.replace("\r\n", "\n").encode("utf-8")).hexdigest()
        if not existing or digest == legacy_sha256:
            return generated
        message = "unmarked guidance contains unrecognized content; add ownership markers before refreshing"
        raise GuidanceError(message)
    start, end = current
    return existing[:start] + generated[desired[0] : desired[1]] + existing[end:]


def write_if_changed(path: Path, content: bytes) -> bool:
    if path.is_file() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return True


def _render_installed_markdown(
    content: bytes, current: Path, replacements: dict[str, str], legacy_hash: str | None
) -> bytes:
    """Expand installation paths and safely migrate or refresh one Markdown resource."""
    generated = content.decode("utf-8").replace("\r\n", "\n")
    for placeholder, replacement in replacements.items():
        generated = generated.replace(placeholder, replacement)
    if current.is_file() and guidance_region(generated) is not None:
        existing = current.read_bytes().decode("utf-8")
        unexpanded = existing
        for placeholder, replacement in replacements.items():
            unexpanded = unexpanded.replace(replacement, placeholder)
        if hashlib.sha256(unexpanded.replace("\r\n", "\n").encode("utf-8")).hexdigest() == legacy_hash:
            legacy_hash = hashlib.sha256(existing.replace("\r\n", "\n").encode("utf-8")).hexdigest()
        generated = merge_guidance(
            existing,
            generated,
            legacy_sha256=legacy_hash,
        )
    return generated.encode("utf-8")


def refresh_skill(
    source: Path, target: Path, *, replacements: dict[str, str] | None = None, previous: Path | None = None
) -> None:
    """Refresh packaged resources without deleting user files or changing unchanged mtimes.

    Plan every merge first, so malformed markers or an edited legacy file cannot
    leave a partially refreshed Skill. Native host installation remains separate.
    """
    manifest_path = source / RESOURCE_MANIFEST
    legacy = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    writes: dict[Path, bytes] = {}
    previous = previous or target
    for path in source.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        destination = target / relative
        current = previous / relative
        if (
            not path.resolve().is_relative_to(source.resolve())
            or not destination.resolve().is_relative_to(target.resolve())
            or not current.resolve().is_relative_to(previous.resolve())
        ):
            message = "Skill resource escapes its package"
            raise GuidanceError(message)
        content = path.read_bytes()
        if path.suffix == ".md":
            content = _render_installed_markdown(content, current, replacements or {}, legacy.get(relative.as_posix()))
        writes[destination] = content
    for destination, content in writes.items():
        old = previous / destination.relative_to(target)
        if old != destination and old.is_file() and old.read_bytes() == content:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(old, destination)
        else:
            write_if_changed(destination, content)


def preserve_installed_guidance(previous_plugin: Path, staged_plugin: Path) -> None:
    """Merge managed guidance during a host's existing staged plugin upgrade."""
    previous = previous_plugin / SKILL_DIRECTORY
    staged = staged_plugin / SKILL_DIRECTORY
    if previous.is_dir() and (staged / RESOURCE_MANIFEST).is_file():
        refresh_skill(staged, staged, previous=previous)
        for path in previous.rglob("*"):
            destination = staged / path.relative_to(previous)
            if path.is_file() and not destination.exists():
                if not path.resolve().is_relative_to(previous.resolve()):
                    message = "user Skill resource escapes its package"
                    raise GuidanceError(message)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
