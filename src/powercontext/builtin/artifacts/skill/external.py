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

"""Host-local discovery and exact resolution for external Codex Skills."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from powercontext.errors import PowerContextError
from powercontext.limits import (
    MAX_ARTIFACT_ID_LENGTH,
    MAX_EXTERNAL_SKILL_DESCRIPTION_LENGTH,
    MAX_EXTERNAL_SKILL_HOST_ID_LENGTH,
    MAX_EXTERNAL_SKILL_LOCATOR_LENGTH,
    MAX_EXTERNAL_SKILL_NAME_LENGTH,
)

MAX_EXTERNAL_SKILL_FILES = 256
MAX_EXTERNAL_SKILL_PACKAGE_BYTES = 4 * 1024 * 1024
MAX_EXTERNAL_SKILL_MANIFEST_BYTES = 128 * 1024

ExternalSkillInstallationScope = Literal["user", "project", "plugin"]
ExternalSkillId = Annotated[str, Field(min_length=1, max_length=MAX_ARTIFACT_ID_LENGTH)]
ExternalSkillName = Annotated[str, Field(min_length=1, max_length=MAX_EXTERNAL_SKILL_NAME_LENGTH)]
ExternalSkillDescription = Annotated[str, Field(min_length=1, max_length=MAX_EXTERNAL_SKILL_DESCRIPTION_LENGTH)]
ExternalSkillHostId = Annotated[str, Field(min_length=1, max_length=MAX_EXTERNAL_SKILL_HOST_ID_LENGTH)]
ExternalSkillLocator = Annotated[str, Field(min_length=1, max_length=MAX_EXTERNAL_SKILL_LOCATOR_LENGTH)]
ExternalSkillFingerprint = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_HOST_ID_ADAPTER = TypeAdapter(ExternalSkillHostId)


class ExternalSkillResolutionStatus(StrEnum):
    """Whether an exact registered package is usable in this environment."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class ExternalSkillNotFoundError(PowerContextError, LookupError):
    """Raised when a registration is absent from the caller's scope."""

    def __init__(self, external_skill_id: str) -> None:
        self.external_skill_id = external_skill_id
        super().__init__("external Skill registration was not found")


class ExternalSkillRegistryUnavailableError(PowerContextError, RuntimeError):
    """Raised when no local external Skill provider is configured."""

    def __init__(self) -> None:
        super().__init__("external Skill Registry is not configured")


class ExternalSkillSnapshotUnavailableError(PowerContextError, RuntimeError):
    """Raised when an exact external package cannot be captured safely."""

    def __init__(self, external_skill_id: str) -> None:
        self.external_skill_id = external_skill_id
        super().__init__("external Skill snapshot is unavailable")


class ExternalSkillRegistration(BaseModel):
    """One rebuildable observation of an Agent-native Skill package."""

    model_config = ConfigDict(frozen=True)

    external_skill_id: ExternalSkillId
    provider: Literal["codex"] = "codex"
    agent_kind: Literal["codex"] = "codex"
    host_id: ExternalSkillHostId
    installation_scope: ExternalSkillInstallationScope
    locator: ExternalSkillLocator
    fingerprint: ExternalSkillFingerprint
    name: ExternalSkillName
    description: ExternalSkillDescription

    @field_validator("external_skill_id", "host_id", "locator", "name", "description")
    @classmethod
    def reject_blank_or_untrimmed_text(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("external Skill text values must be non-empty and trimmed")  # noqa: TRY003
        return value


class ExternalSkillResolution(BaseModel):
    """Live resolution of an exact external Skill registration."""

    registration: ExternalSkillRegistration
    status: ExternalSkillResolutionStatus
    entrypoint: str | None = None


class ExternalSkillProviderScan(BaseModel):
    """A complete provider snapshot plus bounded invalid-package accounting."""

    registrations: tuple[ExternalSkillRegistration, ...] = ()
    skipped: int = Field(default=0, ge=0)


class ExternalSkillSnapshot(BaseModel):
    """Exact primary content plus the fingerprint of its authoritative package."""

    registration: ExternalSkillRegistration
    manifest: str = Field(min_length=1, max_length=MAX_EXTERNAL_SKILL_MANIFEST_BYTES)


class ExternalSkillProvider(Protocol):
    """Discover and resolve packages owned by one local Agent environment."""

    name: str
    agent_kind: str
    host_id: str

    def scan(self) -> ExternalSkillProviderScan: ...

    def resolve(self, registration: ExternalSkillRegistration, /) -> ExternalSkillResolution: ...


class CodexSkillRoot(BaseModel):
    """One explicitly configured Codex installation root."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    root_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    installation_scope: ExternalSkillInstallationScope
    path: Path


class CodexSkillProvider:
    """Read Codex-native packages without copying or rewriting their content."""

    name = "codex"
    agent_kind = "codex"

    def __init__(self, *, host_id: str, roots: tuple[CodexSkillRoot, ...]) -> None:
        self.host_id = _HOST_ID_ADAPTER.validate_python(host_id)
        root_ids = [root.root_id for root in roots]
        if len(root_ids) != len(set(root_ids)):
            raise ValueError("Codex Skill root IDs must be unique")  # noqa: TRY003
        self._roots = tuple(
            root.model_copy(update={"path": root.path.expanduser().resolve(strict=False)}) for root in roots
        )

    def scan(self) -> ExternalSkillProviderScan:
        registrations: list[ExternalSkillRegistration] = []
        skipped = 0
        for root in self._roots:
            if not root.path.is_dir():
                continue
            for package in sorted(root.path.iterdir(), key=lambda value: value.name):
                if not package.is_dir() or package.is_symlink():
                    continue
                try:
                    registrations.append(self._registration(root, package))
                except (OSError, UnicodeError, ValueError):
                    skipped += 1
        return ExternalSkillProviderScan(registrations=tuple(registrations), skipped=skipped)

    def resolve(self, registration: ExternalSkillRegistration, /) -> ExternalSkillResolution:
        if (
            registration.provider != self.name
            or registration.agent_kind != self.agent_kind
            or registration.host_id != self.host_id
        ):
            return _unavailable(registration)
        root = self._root_for(registration)
        if root is None:
            return _unavailable(registration)
        package = Path(registration.locator)
        try:
            resolved = package.resolve(strict=True)
            if resolved.parent != root.path or not resolved.is_dir() or resolved.is_symlink():
                return _unavailable(registration)
            current = self._registration(root, resolved)
        except (OSError, UnicodeError, ValueError):
            return _unavailable(registration)
        if (
            current.external_skill_id != registration.external_skill_id
            or current.fingerprint != registration.fingerprint
        ):
            return _unavailable(registration)
        return ExternalSkillResolution(
            registration=registration,
            status=ExternalSkillResolutionStatus.AVAILABLE,
            entrypoint=str(resolved / "SKILL.md"),
        )

    def _root_for(self, registration: ExternalSkillRegistration) -> CodexSkillRoot | None:
        prefix = f"codex:{registration.installation_scope}:"
        if not registration.external_skill_id.startswith(prefix):
            return None
        root_id = registration.external_skill_id.removeprefix(prefix).split("/", 1)[0]
        return next(
            (
                root
                for root in self._roots
                if root.root_id == root_id and root.installation_scope == registration.installation_scope
            ),
            None,
        )

    def _registration(self, root: CodexSkillRoot, package: Path) -> ExternalSkillRegistration:
        if package.parent != root.path:
            raise ValueError("Codex Skill package must be an immediate child of its configured root")  # noqa: TRY003
        manifest = package / "SKILL.md"
        name, description = _skill_metadata(manifest)
        external_skill_id = f"codex:{root.installation_scope}:{root.root_id}/{package.name}"
        return ExternalSkillRegistration(
            external_skill_id=external_skill_id,
            host_id=self.host_id,
            installation_scope=root.installation_scope,
            locator=str(package),
            fingerprint=_package_fingerprint(package),
            name=name,
            description=description,
        )


def _unavailable(registration: ExternalSkillRegistration) -> ExternalSkillResolution:
    return ExternalSkillResolution(
        registration=registration,
        status=ExternalSkillResolutionStatus.UNAVAILABLE,
    )


def _skill_metadata(manifest: Path) -> tuple[str, str]:
    if manifest.is_symlink():
        raise ValueError("Codex Skill manifest must not be a symlink")  # noqa: TRY003
    content = manifest.read_bytes()
    if len(content) > MAX_EXTERNAL_SKILL_MANIFEST_BYTES:
        raise ValueError("Codex Skill manifest exceeds the supported size")  # noqa: TRY003
    lines = content.decode("utf-8").splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("Codex Skill manifest is missing frontmatter")  # noqa: TRY003
    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        field, separator, raw_value = line.partition(":")
        if separator and field in {"name", "description"}:
            metadata[field] = _frontmatter_scalar(raw_value.strip())
    else:
        raise ValueError("Codex Skill frontmatter is not terminated")  # noqa: TRY003
    try:
        return metadata["name"], metadata["description"]
    except KeyError as error:
        raise ValueError("Codex Skill frontmatter requires name and description") from error  # noqa: TRY003


def _frontmatter_scalar(value: str) -> str:
    if not value:
        raise ValueError("Codex Skill frontmatter values must not be empty")  # noqa: TRY003
    if value.startswith(('"', "'")):
        try:
            parsed = json.loads(value) if value.startswith('"') else value[1:-1]
        except (json.JSONDecodeError, IndexError) as error:
            raise ValueError("Codex Skill frontmatter contains an invalid scalar") from error  # noqa: TRY003
        if not isinstance(parsed, str):
            raise ValueError("Codex Skill frontmatter values must be strings")  # noqa: TRY003
        return parsed
    return value


def _package_fingerprint(package: Path) -> str:
    files = tuple(_package_files(package))
    if not files or len(files) > MAX_EXTERNAL_SKILL_FILES:
        raise ValueError("Codex Skill package has an unsupported file count")  # noqa: TRY003
    digest = hashlib.sha256()
    total_bytes = 0
    for path in files:
        relative = path.relative_to(package).as_posix().encode("utf-8")
        content = path.read_bytes()
        total_bytes += len(content)
        if total_bytes > MAX_EXTERNAL_SKILL_PACKAGE_BYTES:
            raise ValueError("Codex Skill package exceeds the supported size")  # noqa: TRY003
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _package_files(package: Path) -> Iterable[Path]:
    for path in sorted(package.rglob("*"), key=lambda value: value.relative_to(package).as_posix()):
        if path.is_symlink():
            raise ValueError("Codex Skill packages containing symlinks are not supported")  # noqa: TRY003
        if path.is_file():
            yield path


__all__ = [
    "MAX_EXTERNAL_SKILL_DESCRIPTION_LENGTH",
    "MAX_EXTERNAL_SKILL_FILES",
    "MAX_EXTERNAL_SKILL_HOST_ID_LENGTH",
    "MAX_EXTERNAL_SKILL_LOCATOR_LENGTH",
    "MAX_EXTERNAL_SKILL_MANIFEST_BYTES",
    "MAX_EXTERNAL_SKILL_NAME_LENGTH",
    "MAX_EXTERNAL_SKILL_PACKAGE_BYTES",
    "CodexSkillProvider",
    "CodexSkillRoot",
    "ExternalSkillInstallationScope",
    "ExternalSkillNotFoundError",
    "ExternalSkillProvider",
    "ExternalSkillProviderScan",
    "ExternalSkillRegistration",
    "ExternalSkillRegistryUnavailableError",
    "ExternalSkillResolution",
    "ExternalSkillResolutionStatus",
    "ExternalSkillSnapshot",
    "ExternalSkillSnapshotUnavailableError",
]
