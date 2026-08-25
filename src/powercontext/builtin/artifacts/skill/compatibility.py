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

"""Deterministic, non-executing compatibility assessment for Agent targets."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from powercontext.builtin.artifacts.skill.external import AgentEnvironmentProfile, AgentSkillTarget
from powercontext.builtin.artifacts.skill.models import SkillContent
from powercontext.builtin.artifacts.skill.package import SkillPackageError, SkillPackageSnapshot, package_file
from powercontext.builtin.artifacts.skill.projection import validate_skill_projection_target


class SkillCompatibilityState(StrEnum):
    """Static compatibility conclusion without executing package content."""

    COMPATIBLE = "compatible"
    INCOMPATIBLE = "incompatible"
    UNKNOWN = "unknown"
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class SkillCompatibilityAssessment(BaseModel):
    """One rebuildable assessment for an exact package and target profile."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: SkillCompatibilityState
    reasons: tuple[str, ...]
    environment_fingerprint: str
    selected_runtime_variant: str | None = None


class SkillRuntimeRequirements(BaseModel):
    """Declarative needs for one optional PowerContext runtime variant."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operating_systems: tuple[Literal["linux", "darwin", "windows", "other"], ...] = Field(min_length=1)
    commands: dict[str, str] = Field(default_factory=dict, max_length=64)
    network: Literal["none", "required"] = "none"
    writable_roots: tuple[str, ...] = Field(default=(), max_length=32)


class SkillRuntimeVariant(BaseModel):
    """One non-executing runtime choice declared inside the exact package."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    entrypoint: str = Field(min_length=1, max_length=512)
    interpreter: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9+._-]*$")
    requirements: SkillRuntimeRequirements

    @field_validator("entrypoint")
    @classmethod
    def validate_entrypoint(cls, value: str) -> str:
        path = PurePosixPath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts) or "\\" in value:
            raise ValueError("runtime entrypoint must be a safe package-relative path")  # noqa: TRY003
        return value


class SkillRuntimeManifest(BaseModel):
    """Optional namespaced runtime declaration retained inside the package."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_: Literal["powercontext.skill-runtime.v1"] = Field(alias="schema")
    variants: tuple[SkillRuntimeVariant, ...] = Field(min_length=1, max_length=32)

    @field_validator("variants")
    @classmethod
    def validate_unique_variants(cls, value: tuple[SkillRuntimeVariant, ...]) -> tuple[SkillRuntimeVariant, ...]:
        ids = [variant.id for variant in value]
        if len(ids) != len(set(ids)):
            raise ValueError("runtime variant IDs must be unique")  # noqa: TRY003
        return value


def assess_skill_compatibility(
    content: SkillContent,
    package: SkillPackageSnapshot,
    target: AgentSkillTarget,
    /,
) -> SkillCompatibilityAssessment:
    """Assess format and observable interpreter availability without running scripts."""

    fingerprint = target_environment_fingerprint(target)
    try:
        validate_skill_projection_target(content, target)
    except ValueError as error:
        return SkillCompatibilityAssessment(
            state=SkillCompatibilityState.INCOMPATIBLE,
            reasons=(str(error),),
            environment_fingerprint=fingerprint,
        )

    scripts = tuple(entry for entry in package.entries if entry.path.startswith("scripts/"))
    runtime = _runtime_manifest(package)
    if isinstance(runtime, str):
        return SkillCompatibilityAssessment(
            state=SkillCompatibilityState.INCOMPATIBLE,
            reasons=(runtime,),
            environment_fingerprint=fingerprint,
        )
    if runtime is not None:
        return _assess_runtime_manifest(package, target, runtime, fingerprint)
    if not scripts:
        return SkillCompatibilityAssessment(
            state=SkillCompatibilityState.COMPATIBLE,
            reasons=("The package satisfies the Agent format and contains no scripts.",),
            environment_fingerprint=fingerprint,
        )
    if target.environment is None:
        return SkillCompatibilityAssessment(
            state=SkillCompatibilityState.UNKNOWN,
            reasons=("The target has no observed environment profile for package scripts.",),
            environment_fingerprint=fingerprint,
        )

    required_commands = sorted({_required_command(entry.path) for entry in scripts} - {None})
    missing = tuple(command for command in required_commands if command not in target.environment.commands)
    unclassified = tuple(entry.path for entry in scripts if _required_command(entry.path) is None)
    reasons = []
    if missing:
        reasons.append(f"Observed target profile does not report required commands: {', '.join(missing)}.")
    if unclassified:
        reasons.append(f"Script runtime requires manual review: {', '.join(unclassified)}.")
    if reasons:
        return SkillCompatibilityAssessment(
            state=SkillCompatibilityState.MANUAL_REVIEW_REQUIRED,
            reasons=tuple(reasons),
            environment_fingerprint=fingerprint,
        )
    return SkillCompatibilityAssessment(
        state=SkillCompatibilityState.COMPATIBLE,
        reasons=("The Agent format and declared script interpreters match the observed target profile.",),
        environment_fingerprint=fingerprint,
    )


def _runtime_manifest(package: SkillPackageSnapshot) -> SkillRuntimeManifest | str | None:
    path = "powercontext.runtime.yaml"
    if path not in {entry.path for entry in package.entries}:
        return None
    try:
        content = package_file(package, path).decode("utf-8")
        parsed = yaml.safe_load(content)
        return SkillRuntimeManifest.model_validate(parsed)
    except (UnicodeDecodeError, yaml.YAMLError, ValidationError, SkillPackageError) as error:
        return f"The optional PowerContext runtime declaration is invalid: {error}"


def _assess_runtime_manifest(
    package: SkillPackageSnapshot,
    target: AgentSkillTarget,
    manifest: SkillRuntimeManifest,
    fingerprint: str,
) -> SkillCompatibilityAssessment:
    entries = {entry.path for entry in package.entries}
    missing_entrypoints = tuple(
        variant.entrypoint for variant in manifest.variants if variant.entrypoint not in entries
    )
    if missing_entrypoints:
        return SkillCompatibilityAssessment(
            state=SkillCompatibilityState.INCOMPATIBLE,
            reasons=(f"Runtime variants refer to missing package files: {', '.join(missing_entrypoints)}.",),
            environment_fingerprint=fingerprint,
        )
    if target.environment is None:
        return SkillCompatibilityAssessment(
            state=SkillCompatibilityState.UNKNOWN,
            reasons=("The target has no observed environment profile for declared runtime variants.",),
            environment_fingerprint=fingerprint,
        )

    operating_system = (
        "darwin" if target.environment.operating_system == "macos" else target.environment.operating_system
    )
    matching_os = tuple(
        variant for variant in manifest.variants if operating_system in variant.requirements.operating_systems
    )
    if not matching_os:
        return SkillCompatibilityAssessment(
            state=SkillCompatibilityState.INCOMPATIBLE,
            reasons=(f"No runtime variant supports the observed operating system: {operating_system}.",),
            environment_fingerprint=fingerprint,
        )

    manual_reasons: list[str] = []
    incompatible_reasons: list[str] = []
    for variant in matching_os:
        result = _assess_variant(variant, target.environment)
        if result is None:
            return SkillCompatibilityAssessment(
                state=SkillCompatibilityState.COMPATIBLE,
                reasons=(f"Runtime variant {variant.id} matches the observed target profile.",),
                environment_fingerprint=fingerprint,
                selected_runtime_variant=variant.id,
            )
        state, reason = result
        (manual_reasons if state is SkillCompatibilityState.MANUAL_REVIEW_REQUIRED else incompatible_reasons).append(
            reason
        )
    if manual_reasons:
        return SkillCompatibilityAssessment(
            state=SkillCompatibilityState.MANUAL_REVIEW_REQUIRED,
            reasons=tuple(manual_reasons + incompatible_reasons),
            environment_fingerprint=fingerprint,
        )
    return SkillCompatibilityAssessment(
        state=SkillCompatibilityState.INCOMPATIBLE,
        reasons=tuple(incompatible_reasons),
        environment_fingerprint=fingerprint,
    )


def _assess_variant(
    variant: SkillRuntimeVariant,
    environment: AgentEnvironmentProfile,
) -> tuple[SkillCompatibilityState, str] | None:
    commands = {
        **variant.requirements.commands,
        variant.interpreter: variant.requirements.commands.get(variant.interpreter, ""),
    }
    command_assessment, uncertain_versions = _assess_command_requirements(variant, environment, commands)
    if command_assessment is not None:
        return command_assessment

    network_assessment = _assess_network_requirement(variant, environment)
    if network_assessment is not None:
        return network_assessment

    missing_roots = set(variant.requirements.writable_roots) - set(environment.writable_roots)
    if missing_roots:
        return (
            SkillCompatibilityState.INCOMPATIBLE,
            f"Runtime variant {variant.id} requires unavailable writable roots: {', '.join(sorted(missing_roots))}.",
        )
    if uncertain_versions:
        return (
            SkillCompatibilityState.MANUAL_REVIEW_REQUIRED,
            f"Runtime variant {variant.id} has unparseable command versions: {', '.join(uncertain_versions)}.",
        )
    return None


def _assess_command_requirements(
    variant: SkillRuntimeVariant,
    environment: AgentEnvironmentProfile,
    commands: dict[str, str],
) -> tuple[tuple[SkillCompatibilityState, str] | None, tuple[str, ...]]:
    missing = tuple(command for command in commands if command not in environment.commands)
    if missing:
        return (
            (
                SkillCompatibilityState.INCOMPATIBLE,
                f"Runtime variant {variant.id} requires unavailable commands: {', '.join(sorted(missing))}.",
            ),
            (),
        )
    uncertain_versions = []
    mismatched_versions = []
    for command, requirement in commands.items():
        if not requirement:
            continue
        matches = _version_matches(environment.commands[command], requirement)
        if matches is None:
            uncertain_versions.append(command)
        elif not matches:
            mismatched_versions.append(command)
    if mismatched_versions:
        return (
            (
                SkillCompatibilityState.INCOMPATIBLE,
                f"Runtime variant {variant.id} has unsupported command versions: {', '.join(sorted(mismatched_versions))}.",
            ),
            (),
        )
    return None, tuple(sorted(uncertain_versions))


def _assess_network_requirement(
    variant: SkillRuntimeVariant,
    environment: AgentEnvironmentProfile,
) -> tuple[SkillCompatibilityState, str] | None:
    if variant.requirements.network != "required":
        return None
    if environment.network_policy == "disabled":
        return (
            SkillCompatibilityState.INCOMPATIBLE,
            f"Runtime variant {variant.id} requires disabled network access.",
        )
    if environment.network_policy in {"restricted", "unknown"}:
        return (
            SkillCompatibilityState.MANUAL_REVIEW_REQUIRED,
            f"Runtime variant {variant.id} requires network access under {environment.network_policy} policy.",
        )
    return None


def _version_matches(observed: str, requirement: str) -> bool | None:
    try:
        return Version(observed) in SpecifierSet(requirement)
    except (InvalidVersion, InvalidSpecifier):
        return None


def target_environment_fingerprint(target: AgentSkillTarget, /) -> str:
    """Hash only secret-free target compatibility facts and adapter identity."""

    value = {
        "agent_kind": target.agent_kind,
        "installation_scope": target.installation_scope,
        "environment": None if target.environment is None else target.environment.model_dump(mode="json"),
    }
    canonical = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()


def _required_command(path: str) -> str | None:
    suffix = path.rpartition(".")[2].casefold()
    return {
        "py": "python",
        "sh": "bash",
        "bash": "bash",
        "js": "node",
        "mjs": "node",
        "ts": "node",
        "ps1": "pwsh",
        "rb": "ruby",
    }.get(suffix)


__all__ = [
    "SkillCompatibilityAssessment",
    "SkillCompatibilityState",
    "SkillRuntimeManifest",
    "SkillRuntimeRequirements",
    "SkillRuntimeVariant",
    "assess_skill_compatibility",
    "target_environment_fingerprint",
]
