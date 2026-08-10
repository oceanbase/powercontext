"""Project one exact managed Skill Revision into a Codex skill directory."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

from powercontext.artifacts import ArtifactRef
from powercontext.http import SkillProposal

PROJECTION_SCHEMA = "powercontext.codex-skill-projection.v1"
MAX_CODEX_SKILL_NAME_LENGTH = 64
MAX_CODEX_SKILL_DESCRIPTION_LENGTH = 1_024
_CODEX_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def project_skill(
    artifact: ArtifactRef,
    content: SkillProposal,
    destination: Path,
    /,
) -> Path:
    """Create a new host-local Codex projection without replacing existing content."""

    if artifact.family != "skill":
        raise ValueError("artifact must identify a managed Skill")  # noqa: TRY003
    target = destination.resolve(strict=False)
    _validate_codex_projection(content, target)
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    skill_text = _skill_markdown(artifact, content)
    manifest = {
        "schema": PROJECTION_SCHEMA,
        "artifact": artifact.model_dump(mode="json"),
        "skill_sha256": hashlib.sha256(skill_text.encode("utf-8")).hexdigest(),
    }
    with tempfile.TemporaryDirectory(prefix=".powercontext-skill-", dir=target.parent) as temporary:
        staging = Path(temporary) / "projection"
        staging.mkdir()
        (staging / "SKILL.md").write_text(skill_text, encoding="utf-8")
        (staging / "powercontext.json").write_text(
            f"{json.dumps(manifest, indent=2, sort_keys=True)}\n",
            encoding="utf-8",
        )
        shutil.copytree(staging, target)
    return target


def _validate_codex_projection(content: SkillProposal, destination: Path) -> None:
    if len(content.name) > MAX_CODEX_SKILL_NAME_LENGTH or _CODEX_SKILL_NAME.fullmatch(content.name) is None:
        raise ValueError(  # noqa: TRY003
            "managed Skill name must be at most 64 lowercase letters, digits, and single hyphens for Codex"
        )
    if destination.name != content.name:
        raise ValueError("Codex skill directory name must match the managed Skill name")  # noqa: TRY003
    if len(content.description) > MAX_CODEX_SKILL_DESCRIPTION_LENGTH or any(
        value in content.description for value in "<>"
    ):
        raise ValueError(  # noqa: TRY003
            "managed Skill description must be at most 1024 characters and contain no angle brackets for Codex"
        )


def _skill_markdown(artifact: ArtifactRef, content: SkillProposal) -> str:
    validation = "\n".join(f"- {item.root}" for item in content.validation)
    exact_ref = f"artifact:{artifact.family}/{artifact.artifact_id}@{artifact.revision}"
    return (
        "---\n"
        f"name: {json.dumps(content.name, ensure_ascii=False)}\n"
        f"description: {json.dumps(content.description, ensure_ascii=False)}\n"
        "---\n\n"
        f"<!-- Generated from {exact_ref}. The Artifact Revision remains authoritative. -->\n\n"
        f"{content.instructions.rstrip()}\n\n"
        "## Validation\n\n"
        f"{validation}\n"
    )


__all__ = ["PROJECTION_SCHEMA", "project_skill"]
