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

import io
import zipfile
from pathlib import Path

from powercontext.builtin.artifacts.skill import (
    AgentEnvironmentProfile,
    AgentSkillTarget,
    SkillCompatibilityState,
    assess_skill_compatibility,
    capture_skill_archive,
)


def test_compatibility_is_reasoned_per_agent_format_and_observed_environment(tmp_path: Path) -> None:
    package = capture_skill_archive(_archive(description="Use <carefully> for release checks."))
    codex = AgentSkillTarget(
        target_id="codex-project",
        agent_kind="codex",
        installation_scope="project",
        path=tmp_path / ".agents" / "skills",
        environment=AgentEnvironmentProfile(
            operating_system="linux",
            architecture="x86_64",
            commands={"bash": "5.2"},
            network_policy="disabled",
            dependency_install_policy="denied",
        ),
    )
    claude = AgentSkillTarget(
        target_id="claude-project",
        agent_kind="claude_code",
        installation_scope="project",
        path=tmp_path / ".claude" / "skills",
        environment=codex.environment,
    )

    codex_assessment = assess_skill_compatibility(package.as_skill_content(), package, codex)
    claude_assessment = assess_skill_compatibility(package.as_skill_content(), package, claude)

    assert codex_assessment.state is SkillCompatibilityState.INCOMPATIBLE
    assert claude_assessment.state is SkillCompatibilityState.COMPATIBLE
    assert codex_assessment.environment_fingerprint != claude_assessment.environment_fingerprint


def test_script_compatibility_preserves_unknown_and_manual_review_states(tmp_path: Path) -> None:
    package = capture_skill_archive(_archive(description="Verify releases."))
    unobserved = AgentSkillTarget(
        target_id="codex-project",
        agent_kind="codex",
        installation_scope="project",
        path=tmp_path,
    )
    missing_bash = unobserved.model_copy(
        update={
            "environment": AgentEnvironmentProfile(
                operating_system="linux",
                architecture="arm64",
                commands={},
            )
        }
    )

    assert (
        assess_skill_compatibility(package.as_skill_content(), package, unobserved).state
        is SkillCompatibilityState.UNKNOWN
    )
    assert (
        assess_skill_compatibility(package.as_skill_content(), package, missing_bash).state
        is SkillCompatibilityState.MANUAL_REVIEW_REQUIRED
    )


def test_declared_runtime_variant_matches_versions_and_host_requirements(tmp_path: Path) -> None:
    package = capture_skill_archive(_runtime_archive())
    target = AgentSkillTarget(
        target_id="codex-project",
        agent_kind="codex",
        installation_scope="project",
        path=tmp_path,
        environment=AgentEnvironmentProfile(
            operating_system="linux",
            architecture="x86_64",
            commands={"python": "3.13.2"},
            network_policy="disabled",
            writable_roots=("workspace",),
        ),
    )
    assert target.environment is not None
    environment = target.environment

    compatible = assess_skill_compatibility(package.as_skill_content(), package, target)
    wrong_os = assess_skill_compatibility(
        package.as_skill_content(),
        package,
        target.model_copy(update={"environment": environment.model_copy(update={"operating_system": "windows"})}),
    )
    old_python = assess_skill_compatibility(
        package.as_skill_content(),
        package,
        target.model_copy(update={"environment": environment.model_copy(update={"commands": {"python": "3.10.14"}})}),
    )

    assert compatible.state is SkillCompatibilityState.COMPATIBLE
    assert compatible.selected_runtime_variant == "python"
    assert wrong_os.state is SkillCompatibilityState.INCOMPATIBLE
    assert old_python.state is SkillCompatibilityState.INCOMPATIBLE


def test_invalid_runtime_declaration_is_reported_without_execution(tmp_path: Path) -> None:
    package = capture_skill_archive(_runtime_archive(entrypoint="../outside.py"))
    target = AgentSkillTarget(
        target_id="codex-project",
        agent_kind="codex",
        installation_scope="project",
        path=tmp_path,
    )

    assessment = assess_skill_compatibility(package.as_skill_content(), package, target)

    assert assessment.state is SkillCompatibilityState.INCOMPATIBLE
    assert "runtime declaration is invalid" in assessment.reasons[0]


def _archive(*, description: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "SKILL.md",
            f"---\nname: release-check\ndescription: {description}\n---\n\nRun the check.\n",
        )
        script = zipfile.ZipInfo("scripts/check.sh")
        script.external_attr = 0o100755 << 16
        archive.writestr(script, "#!/bin/sh\nexit 0\n")
    return buffer.getvalue()


def _runtime_archive(*, entrypoint: str = "scripts/check.py") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "SKILL.md",
            "---\nname: release-check\ndescription: Verify releases.\n---\n\nRun the check.\n",
        )
        archive.writestr("scripts/check.py", "raise SystemExit('must not execute')\n")
        archive.writestr(
            "powercontext.runtime.yaml",
            "schema: powercontext.skill-runtime.v1\n"
            "variants:\n"
            "  - id: python\n"
            f"    entrypoint: {entrypoint}\n"
            "    interpreter: python\n"
            "    requirements:\n"
            "      operating_systems: [linux, darwin]\n"
            "      commands:\n"
            "        python: '>=3.11'\n"
            "      network: none\n"
            "      writable_roots: [workspace]\n",
        )
    return buffer.getvalue()
