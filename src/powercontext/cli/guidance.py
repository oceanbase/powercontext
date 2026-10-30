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

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit

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
    source_files: tuple[str, ...] = ("package.json",)
    package_name: str | None = None
    variables: tuple[str, ...] = ()
    script_evidence: tuple[str, ...] = ()

    def recognizes(self, path: Path) -> bool:
        """Identify a host package without requiring optional build output."""
        if not all((path / name).is_file() for name in self.source_files):
            return False
        if self.package_name is None:
            return True
        try:
            manifest = json.loads((path / self.source_files[0]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(manifest, dict) and manifest.get("name") == self.package_name

    @property
    def skill(self) -> Path:
        return Path(self.plugin) / SKILL_DIRECTORY


HOST_GUIDANCE = {
    "codex": HostGuidance(
        "integrations/codex/plugins/powercontext",
        variables=("PLUGIN_ROOT",),
        script_evidence=("scripts/scope_binding.py",),
    ),
    "claude-code": HostGuidance(
        "integrations/claude-code/plugins/powercontext",
        variables=("PLUGIN_ROOT",),
        script_evidence=("scripts/workspace_scope.py",),
    ),
    "dsh": HostGuidance(
        "integrations/dsh/plugins/powercontext",
        package_name="powercontext-dsh",
    ),
    "openclaw": HostGuidance(
        "integrations/openclaw/plugins/memory-powercontext",
        package_name="@oceanbase/openclaw-memory-powercontext",
    ),
    "opencode": HostGuidance(
        "integrations/opencode/plugins/powercontext",
        package_name="powercontext-opencode",
    ),
    "pi": HostGuidance(
        "integrations/pi/plugins/powercontext",
        package_name="powercontext-pi",
    ),
    "hermes": HostGuidance(
        "integrations/hermes/plugins/powercontext",
        source_files=("plugin.yaml", "__init__.py"),
    ),
    "workbuddy": HostGuidance(
        "integrations/workbuddy/plugins/powercontext",
        variables=("POWERCONTEXT_PYTHON", "POWERCONTEXT_SCOPE_BINDING_SCRIPT"),
        script_evidence=("scripts/workspace_scope.py",),
        source_files=("hooks/workbuddy_powercontext_hook.py", "scripts/workspace_scope.py"),
    ),
    "zcode": HostGuidance(
        "integrations/zcode/plugins/powercontext",
        script_evidence=("scripts/scope.mjs",),
        source_files=(".zcode-plugin/plugin.json", "hooks/user_prompt_submit.mjs"),
        package_name="powercontext",
    ),
    "kimi-code": HostGuidance(
        "integrations/kimi-code/plugins/powercontext",
        script_evidence=("scripts/scope.mjs",),
        source_files=("kimi.plugin.json",),
        package_name="powercontext",
    ),
    "agent-plugin": HostGuidance("integrations/agent-plugin/powercontext"),
    "minimax": HostGuidance(
        "integrations/minimax/plugins/powercontext",
        source_files=(".minimax-plugin/plugin.json",),
        package_name="powercontext",
    ),
}


class GuidanceError(ValueError):
    """A refresh cannot safely identify its managed region."""


def _resource_name(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or ":" in value
        or PurePosixPath(value).is_absolute()
        or ".." in PurePosixPath(value).parts
        or PurePosixPath(value).as_posix() != value
        or value in {".", RESOURCE_MANIFEST}
    ):
        raise GuidanceError("invalid guidance resource path in manifest")  # noqa: TRY003
    return value


def _resource_inventory(directory: Path) -> set[str] | None:
    manifest = directory / RESOURCE_MANIFEST
    if not manifest.exists():
        return None
    if not manifest.resolve().is_relative_to(directory.resolve()):
        raise GuidanceError("guidance manifest escapes its package")  # noqa: TRY003
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise GuidanceError("invalid guidance manifest") from error  # noqa: TRY003
    if not isinstance(payload, dict):
        raise GuidanceError("invalid guidance manifest")  # noqa: TRY003
    resources = payload.get("resources")
    if payload.get("schema_version") != 1 or not isinstance(resources, list):
        raise GuidanceError("unsupported guidance manifest schema")  # noqa: TRY003
    names = {_resource_name(name) for name in resources}
    if "SKILL.md" not in names or len(names) != len(resources):
        raise GuidanceError("guidance manifest needs a unique resource inventory including SKILL.md")  # noqa: TRY003
    return names


def _linked_resources(directory: Path, *, require_files: bool) -> set[str]:
    """Walk package-owned Markdown links; local notes outside markers are not ownership evidence."""
    pending = [directory / "SKILL.md"]
    seen: set[str] = set()
    while pending:
        path = pending.pop().resolve()
        if not path.is_relative_to(directory.resolve()):
            raise GuidanceError("guidance resource escapes its package")  # noqa: TRY003
        name = path.relative_to(directory.resolve()).as_posix()
        if name in seen:
            continue
        seen.add(name)
        if not path.is_file():
            if require_files:
                raise GuidanceError(f"missing guidance resource: {name}")  # noqa: TRY003
            continue
        if path.suffix != ".md":
            continue
        content = path.read_text(encoding="utf-8")
        region = guidance_region(content)
        if region is not None:
            content = content[region[0] : region[1]]
        for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", content):
            parsed = urlsplit(target)
            if not parsed.scheme and not parsed.netloc and parsed.path:
                pending.append(path.parent / unquote(parsed.path))
    return seen


def _package_resources(directory: Path) -> set[str]:
    inventory = _resource_inventory(directory)
    linked = _linked_resources(directory, require_files=True)
    if inventory is None:
        for path in directory.rglob("*.md"):
            if guidance_region(path.read_text(encoding="utf-8")) is not None:
                raise GuidanceError("managed guidance is missing its resource manifest")  # noqa: TRY003
        return linked
    names = inventory
    packaged = {
        path.relative_to(directory).as_posix()
        for path in directory.rglob("*")
        if path.is_file() and path.relative_to(directory).as_posix() != RESOURCE_MANIFEST
    }
    if packaged - names:
        raise GuidanceError("unlisted guidance resource in package manifest")  # noqa: TRY003
    if linked - names:
        raise GuidanceError("linked guidance resource is absent from the manifest")  # noqa: TRY003
    for name in names:
        path = directory / name
        if not path.is_file() or not path.resolve().is_relative_to(directory.resolve()):
            raise GuidanceError(f"missing or out-of-package guidance resource: {name}")  # noqa: TRY003
    return names


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


def merge_guidance(existing: str, generated: str) -> str:
    """Replace only managed bytes; preserve content outside ownership markers."""
    desired = guidance_region(generated)
    if desired is None:
        message = "generated guidance has no managed region"
        raise GuidanceError(message)
    current = guidance_region(existing)
    if current is None:
        if not existing:
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
    content: bytes,
    current: Path,
    replacements: dict[str, str],
) -> bytes:
    """Expand installation paths and refresh one Markdown resource."""
    generated = content.decode("utf-8").replace("\r\n", "\n")
    for placeholder, replacement in replacements.items():
        generated = generated.replace(placeholder, replacement)
    if current.is_file():
        existing = current.read_bytes().decode("utf-8")
        if guidance_region(generated) is None:
            if existing.replace("\r\n", "\n") == generated:
                return existing.encode("utf-8")
            message = "incoming unmarked guidance cannot safely replace existing content"
            raise GuidanceError(message)
        generated = merge_guidance(existing, generated)
    return generated.encode("utf-8")


def _validate_resource_transition(source: Path, previous: Path, resources: set[str]) -> None:
    """Do not promote retired or ambiguously owned package files into user content."""
    if previous.is_dir():
        prior = _resource_inventory(previous)
        owned = prior if prior is not None else _linked_resources(previous, require_files=False)
        removed = {name for name in owned - resources if (previous / name).is_file()}
        if removed:
            message = f"removing owned guidance requires an explicit migration: {sorted(removed)}"
            raise GuidanceError(message)
        for path in previous.rglob("*"):
            if not path.is_file() or (source / path.relative_to(previous)).exists():
                continue
            if not path.resolve().is_relative_to(previous.resolve()):
                raise GuidanceError("user Skill resource escapes its package")  # noqa: TRY003
            if path.suffix == ".md" and guidance_region(path.read_text(encoding="utf-8")) is not None:
                raise GuidanceError("unlisted owned guidance requires an explicit migration")  # noqa: TRY003


def refresh_skill(
    source: Path,
    target: Path,
    *,
    replacements: dict[str, str] | None = None,
    previous: Path | None = None,
) -> None:
    """Refresh packaged resources without deleting user files or changing unchanged mtimes.

    Plan every merge first, so malformed markers or unrecognized user content cannot
    leave a partially refreshed Skill. Native host installation remains separate.
    """
    resources = _package_resources(source)
    writes: dict[Path, bytes] = {}
    previous = previous or target
    _validate_resource_transition(source, previous, resources)
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
            content = _render_installed_markdown(content, current, replacements or {})
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
    if not previous.is_dir() and staged.is_dir():
        _package_resources(staged)
    if previous.is_dir():
        if not (staged / "SKILL.md").is_file():
            message = "incoming package omits existing guidance; cannot safely replace a pre-marker or managed Skill"
            raise GuidanceError(message)
        refresh_skill(staged, staged, previous=previous)
        for path in previous.rglob("*"):
            destination = staged / path.relative_to(previous)
            if path.is_file() and not destination.exists():
                if not path.resolve().is_relative_to(previous.resolve()):
                    message = "user Skill resource escapes its package"
                    raise GuidanceError(message)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, destination)
