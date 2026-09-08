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

"""Exact approved package delivery without a new remote target lifecycle."""

# ruff: noqa: TRY003 - bounded validation errors are part of this bridge's diagnostics.

from __future__ import annotations

import base64
import hashlib
from pathlib import Path

from powercontext.builtin.artifacts.skill.package import (
    capture_skill_archive,
    materialize_skill_package,
)
from powercontext.client import PowerContextClient
from powercontext.http import (
    ArtifactReference,
    GetSkillPackageRequest,
    SkillPackageDownload,
    SkillPackageManifest,
)
from powercontext_datus.freeze import IntegrityError, snapshot


def install_package(
    manifest: SkillPackageManifest,
    download: SkillPackageDownload,
    skill_root: Path,
) -> dict[str, object]:
    """Install one verified data/instruction-only package into a new destination.

    The caller owns skill_root and must restrict Datus's effective Skill/tool
    inventory separately. A successful install never grants tool authority.
    """
    if skill_root.is_symlink() or not skill_root.is_dir():
        raise IntegrityError("Skill root must be an existing regular directory")
    archive = base64.b64decode(download.archive_base64, validate=True)
    if download.package != manifest.package or hashlib.sha256(archive).hexdigest() != manifest.package.archive_digest:
        raise IntegrityError("download does not match exact package manifest")
    package = capture_skill_archive(archive)
    if package.reference.model_dump() != manifest.package.model_dump():
        raise IntegrityError("canonical package identity mismatch")
    if package.metadata.name != manifest.name or package.metadata.description != manifest.description:
        raise IntegrityError("package discovery metadata mismatch")
    expected_files = sorted(
        (entry.path, entry.digest, entry.size, entry.media_type, bool(entry.mode & 0o111)) for entry in package.entries
    )
    actual_files = sorted(
        (entry.path, entry.digest, entry.size, entry.media_type, entry.executable) for entry in manifest.files
    )
    if expected_files != actual_files:
        raise IntegrityError("package file inventory mismatch")
    for entry in package.entries:
        if entry.mode & 0o111 or Path(entry.path).suffix.lower() not in {
            ".md",
            ".txt",
            ".sql",
            ".json",
            ".yaml",
            ".yml",
        }:
            raise IntegrityError("Datus bridge accepts instruction/data files, not executable packages")
    destination = skill_root / manifest.name
    materialize_skill_package(package, destination)
    return {
        "name": manifest.name,
        "tree_digest": manifest.package.tree_digest,
        "archive_digest": manifest.package.archive_digest,
        "files": snapshot(destination),
    }


async def deliver_skill(
    client: PowerContextClient,
    *,
    scope_id: str,
    artifact: ArtifactReference,
    skill_root: Path,
) -> dict[str, object]:
    """Download through the authenticated SDK and preserve exact revision lineage."""
    if artifact.family != "skill":
        raise ValueError("only approved Skill revisions can be delivered")
    request = GetSkillPackageRequest(scope_id=scope_id, artifact=artifact)
    manifest = await client.get_skill_package_manifest(request)
    download = await client.download_skill_package(request)
    return {
        "scope_id": scope_id,
        "artifact": artifact.model_dump(mode="json"),
        **install_package(manifest, download, skill_root),
    }
