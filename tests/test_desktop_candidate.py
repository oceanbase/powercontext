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

"""Exercise the candidate handoff without pretending to qualify a native package."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from desktop_candidate import (
    MANIFEST,
    TARGETS,
    UNQUALIFIED,
    assemble,
    digest,
    identity,
    json_bytes,
    package_name,
    resource_names,
    server_url,
    stage_target,
    verify,
)

SOURCE = "a" * 40
SERVER_SOURCE = "e" * 40
RELEASE = identity("1.0.0", "1.5.1.dev25+g63e9665c6", SOURCE, SERVER_SOURCE)
OPERATIONS = {"contractSha256": "b" * 64, "operations": {"get_stats": {}, "remember_memory": {}}}


def write(path: Path, value: object) -> str:
    path.write_bytes(json_bytes(value))
    return digest(path)


def synthetic_target(root: Path, target: str) -> str:
    platform, architecture, _, _ = TARGETS[target]
    path = root / target
    path.mkdir()
    names = resource_names(RELEASE, target)
    wheel = f"powercontext-{RELEASE['server_version']}-py3-none-any.whl"
    for name in (package_name(RELEASE, target), wheel, names["server_archive"], names["ops_archive"]):
        (path / name).write_bytes(name.encode())
    producer = {"git_commit": SOURCE, "matches_committed_files": True}
    interpreter = {
        "platform": platform,
        "architecture": architecture,
        "python_version": "3.12.16",
        "sha256": "c" * 64,
        "server_python_relative": "pythonw.exe" if platform == "windows" else "bin/python3",
    }
    ops_manifest = write(path / names["ops_bundle_manifest"], {"target": target})
    wheel_sha = digest(path / wheel)
    inputs = {
        "format_version": 1,
        "target": target,
        "source": {"commit": SERVER_SOURCE},
        "server_version": RELEASE["server_version"],
        "interpreter": interpreter,
        "requirements_sha256": write(path / "requirements.lock", {"local": "lock"}),
        "upstream_requirements_sha256": write(path / "upstream-requirements.lock", {"upstream": "lock"}),
        "preparer": producer,
        "wheels": [
            {"name": "powercontext", "version": RELEASE["server_version"], "filename": wheel, "sha256": wheel_sha}
        ],
    }
    inputs_sha = write(path / "inputs.json", inputs)
    binding = {
        "producer": producer,
        "source_commit": SERVER_SOURCE,
        "server_version": RELEASE["server_version"],
        "python_source_sha256": interpreter["sha256"],
        "inputs_sha256": inputs_sha,
        "server_archive_sha256": digest(path / names["server_archive"]),
        "ops_archive_sha256": digest(path / names["ops_archive"]),
        "ops_bundle_manifest_sha256": ops_manifest,
        "server_wheel_sha256": wheel_sha,
    }
    binding_sha = write(path / "binding.tmp", binding)
    (path / "binding.tmp").unlink()
    ref = f"unqualified-candidate:sha256:{binding_sha}"
    server = {
        "platform": platform,
        "architecture": architecture,
        "server_version": RELEASE["server_version"],
        "python_version": interpreter["python_version"],
        "python_relative": interpreter["server_python_relative"],
        "ops_protocol_min": 1,
        "ops_protocol_max": 1,
        "url": server_url(target),
        "qualification_ref": ref,
        "archive_sha256": binding["server_archive_sha256"],
        "archive_size": (path / names["server_archive"]).stat().st_size,
    }
    catalog_sha = write(path / names["server_catalog"], {"format_version": 1, "resources": [server]})
    status = {"format_version": 1, "status": UNQUALIFIED, "inputs_sha256": inputs_sha}
    write(path / "server-resource.json", {**status, "inputs": inputs, "resource": server})
    write(
        path / "ops-resource.json",
        {
            **status,
            "archive_sha256": binding["ops_archive_sha256"],
            "archive_size": (path / names["ops_archive"]).stat().st_size,
        },
    )
    write(
        path / names["ops_provision"],
        {
            "format_version": 1,
            "binding_mode": "managed_catalog",
            "bundle_manifest_sha256": ops_manifest,
            "catalog_sha256": catalog_sha,
        },
    )
    write(
        path / "producer-candidate.json",
        {
            "format_version": 1,
            "status": UNQUALIFIED,
            "binding": binding,
            "catalog_sha256": catalog_sha,
            "ops_archive_sha256": binding["ops_archive_sha256"],
            "qualification_ref": ref,
            "signing": "not_performed",
            "notarization": "not_performed",
            "clean_machine_qualification": "not_performed",
        },
    )
    write(
        path / "resource-smoke.json",
        {
            "status": "candidate_smoke_passed",
            "qualified": False,
            "archive_sha256": binding["server_archive_sha256"],
            "catalog_sha256": catalog_sha,
            "ops": "independent_inspection_passed",
            "server": {"launcher": "passed"},
        },
    )
    return wheel_sha


@pytest.fixture
def legs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    wheel_shas = {synthetic_target(tmp_path, target) for target in TARGETS}
    [wheel_sha] = wheel_shas
    matching = {
        "id": "matching",
        "serverCommit": SERVER_SOURCE,
        "contractSha256": OPERATIONS["contractSha256"],
        "artifactSha256": wheel_sha,
        "operations": list(OPERATIONS["operations"]),
        "evidence": "desktop/VALIDATION.md",
    }
    profiles = [
        matching,
        {**matching, "id": "wrong-wheel", "artifactSha256": "d" * 64},
        {**matching, "id": "desktop-source", "serverCommit": SOURCE},
        {**matching, "id": "old-operation-set", "operations": ["remember_memory"]},
        {**matching, "id": "ci-fixture"},
    ]

    def read_commit(command: str, object_name: str) -> bytes:
        assert command == "show"
        return json_bytes(OPERATIONS if object_name.endswith("/operations.json") else profiles)

    monkeypatch.setattr("desktop_candidate.git", read_commit)
    return tmp_path


def test_assemble_without_linux_and_never_promote_a_candidate(legs: Path) -> None:
    assemble(legs, RELEASE)
    path = legs / MANIFEST
    manifest = json.loads(path.read_bytes())
    verify(legs, RELEASE, digest(path))
    assert manifest["release_tag"] == "desktop-v1.0.0"
    assert manifest["source_sha"] == SOURCE
    assert manifest["server_source_sha"] == SERVER_SOURCE
    assert manifest["publishable"] is False
    assert set(manifest["targets"]) == {"windows-x86_64", "macos-arm64", "macos-x86_64"}
    target = manifest["targets"]["macos-arm64"]
    assert target["qualification"]["matching_product_records"] == [
        {"id": "matching", "evidence": "desktop/VALIDATION.md"}
    ]
    assert target["assets"]["desktop"]["name"] == "macos-arm64/powercontext-desktop-1.0.0-macos-arm64.dmg"


@pytest.mark.parametrize(
    "wrong_leg", ["missing-intel", "internal", "wrong-wheel", "wrong-pin", "wrong-source", "wrong-target"]
)
def test_assembly_rejects_incomplete_or_unbound_required_outputs(legs: Path, wrong_leg: str) -> None:
    target = legs / "windows-x86_64"
    if wrong_leg == "missing-intel":
        (legs / "macos-x86_64").rename(legs / "linux-x86_64")
    elif wrong_leg == "internal":
        (target / "ci-compatibility.json").write_text("{}", encoding="utf-8")
    elif wrong_leg == "wrong-wheel":
        next(target.glob("*.whl")).write_bytes(b"wrong")
    elif wrong_leg == "wrong-pin":
        windows_pin = resource_names(RELEASE, "windows-x86_64")["ops_provision"]
        arm_pin = resource_names(RELEASE, "macos-arm64")["ops_provision"]
        (target / windows_pin).write_bytes((legs / "macos-arm64" / arm_pin).read_bytes())
    else:
        inputs = json.loads((target / "inputs.json").read_bytes())
        if wrong_leg == "wrong-source":
            inputs["source"]["commit"] = "f" * 40
        else:
            inputs["target"] = "macos-arm64"
        write(target / "inputs.json", inputs)
    with pytest.raises(ValueError):
        assemble(legs, RELEASE)
    assert not (legs / MANIFEST).exists()


def test_rejects_preview_bundle_before_staging(tmp_path: Path) -> None:
    nsis = tmp_path / "nsis"
    nsis.mkdir()
    (nsis / "PowerContext Desktop Preview_0.1.0_x64-setup.exe").write_bytes(b"internal")
    with pytest.raises(ValueError, match="normal product bundle"):
        stage_target(tmp_path / "output", tmp_path, tmp_path, tmp_path, tmp_path, RELEASE, "windows-x86_64")


def test_verification_rejects_tampered_package_or_manifest(legs: Path) -> None:
    assemble(legs, RELEASE)
    original = digest(legs / MANIFEST)
    with pytest.raises(ValueError, match="manifest digest"):
        verify(legs, RELEASE, "a" * 64)
    path = legs / "macos-arm64" / package_name(RELEASE, "macos-arm64")
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="does not match assets"):
        verify(legs, RELEASE, original)


@pytest.mark.parametrize("value", ["v1.0.0", "65536.0.0", "1.2.03", "1.2.3-rc.1", "1.2.3\npath"])
def test_rejects_ambiguous_or_unsafe_version(value: str) -> None:
    with pytest.raises(ValueError, match=r"exact X\.Y\.Z"):
        identity(value, "1.2.3", SOURCE, SERVER_SOURCE)
