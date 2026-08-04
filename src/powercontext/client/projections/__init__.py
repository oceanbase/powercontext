"""Host-local projections owned by the installed Client package."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum
from pathlib import Path

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.skill import SkillContent
from powercontext.client.projections.codex import project_skill as export_codex_skill


class SkillExportTarget(StrEnum):
    """Agent integrations supported by managed Skill export."""

    CODEX = "codex"


_SkillExporter = Callable[[ArtifactRef, SkillContent, Path], Path]
_SKILL_EXPORTERS: dict[SkillExportTarget, _SkillExporter] = {
    SkillExportTarget.CODEX: export_codex_skill,
}


def export_skill(
    artifact: ArtifactRef,
    content: SkillContent,
    target: SkillExportTarget,
    destination: Path,
    /,
) -> Path:
    """Export one exact managed Skill Revision for an Agent integration."""

    return _SKILL_EXPORTERS[target](artifact, content, destination)


__all__ = ["SkillExportTarget", "export_skill"]
