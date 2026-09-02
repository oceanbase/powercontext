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

import io
import os
import stat
import zipfile
from pathlib import Path

import pytest

import powercontext.builtin.artifacts.skill.package as package_module
from powercontext.builtin.artifacts.skill import (
    MAX_SKILL_PACKAGE_BYTES,
    MAX_SKILL_PACKAGE_FILES,
    SkillContent,
    SkillPackageError,
    build_instruction_skill_package,
    capture_skill_archive,
    capture_skill_directory,
    materialize_skill_package,
    package_file,
)


def _write_package(root: Path) -> Path:
    package = root / "release-check"
    (package / "scripts").mkdir(parents=True)
    (package / "references").mkdir()
    (package / "assets").mkdir()
    (package / "SKILL.md").write_text(
        "---\n"
        "name: release-check\n"
        "description: Verify a release before publishing it.\n"
        "license: Apache-2.0\n"
        "compatibility: Requires Python 3.11 or newer.\n"
        "metadata:\n"
        "  owner: release-team\n"
        "allowed-tools: Bash(git:*) Read\n"
        "---\n\n"
        "Run the verification script and inspect its report.\n",
        encoding="utf-8",
    )
    script = package / "scripts" / "verify.py"
    script.write_text("print('verified')\n", encoding="utf-8")
    script.chmod(0o755)
    (package / "references" / "policy.md").write_text("# Release policy\n", encoding="utf-8")
    (package / "assets" / "report.json").write_bytes(b'{"status":"pending"}\n')
    (package / ".hidden-note").write_text("Preserved.\n", encoding="utf-8")
    return package


def test_directory_package_round_trips_exact_files_and_executable_mode(tmp_path: Path) -> None:
    package = _write_package(tmp_path)

    snapshot = capture_skill_directory(package)
    restored = tmp_path / "restored"
    materialize_skill_package(snapshot, restored)

    assert snapshot.metadata.name == "release-check"
    assert snapshot.metadata.metadata == {"owner": "release-team"}
    assert snapshot.reference.file_count == 5
    assert package_file(snapshot, "references/policy.md") == b"# Release policy\n"
    assert (restored / ".hidden-note").read_text(encoding="utf-8") == "Preserved.\n"
    assert (restored / "scripts" / "verify.py").stat().st_mode & stat.S_IXUSR
    for entry in snapshot.entries:
        assert (restored / entry.path).read_bytes() == (package / entry.path).read_bytes()


def test_different_zip_order_converges_on_the_same_canonical_package(tmp_path: Path) -> None:
    package = _write_package(tmp_path)
    expected = capture_skill_directory(package)
    paths = [entry.path for entry in expected.entries]

    first = _zip_files(package, paths)
    second = _zip_files(package, reversed(paths))

    first_snapshot = capture_skill_archive(first)
    second_snapshot = capture_skill_archive(second)
    assert first_snapshot.reference == expected.reference
    assert second_snapshot.reference == expected.reference
    assert first_snapshot.archive_bytes == second_snapshot.archive_bytes == expected.archive_bytes


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("../outside", b"bad"),
        (".env", b"SECRET=value"),
        ("nested\\windows", b"bad"),
    ],
)
def test_archive_rejects_unsafe_paths(name: str, content: bytes) -> None:
    archive = _zip_entries((
        ("SKILL.md", b"---\nname: safe-skill\ndescription: Safe.\n---\n"),
        (name, content),
    ))

    with pytest.raises(SkillPackageError):
        capture_skill_archive(archive)


def test_archive_rejects_duplicate_and_symlink_entries() -> None:
    duplicate = io.BytesIO()
    with pytest.warns(UserWarning, match="Duplicate name"), zipfile.ZipFile(duplicate, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: safe-skill\ndescription: Safe.\n---\n")
        archive.writestr("SKILL.md", "different")
    with pytest.raises(SkillPackageError, match="duplicate"):
        capture_skill_archive(duplicate.getvalue())

    linked = io.BytesIO()
    with zipfile.ZipFile(linked, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: safe-skill\ndescription: Safe.\n---\n")
        info = zipfile.ZipInfo("scripts/run")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "../outside")
    with pytest.raises(SkillPackageError, match="non-regular"):
        capture_skill_archive(linked.getvalue())


def test_archive_rejects_case_collisions_special_files_and_invalid_frontmatter() -> None:
    collision = _zip_entries((
        ("SKILL.md", b"---\nname: safe-skill\ndescription: Safe.\n---\n"),
        ("References/Policy.md", b"first"),
        ("references/policy.md", b"second"),
    ))
    with pytest.raises(SkillPackageError, match="colliding"):
        capture_skill_archive(collision)

    special = io.BytesIO()
    with zipfile.ZipFile(special, "w") as archive:
        archive.writestr("SKILL.md", "---\nname: safe-skill\ndescription: Safe.\n---\n")
        info = zipfile.ZipInfo("scripts/pipe")
        info.create_system = 3
        info.external_attr = (stat.S_IFIFO | 0o644) << 16
        archive.writestr(info, "")
    with pytest.raises(SkillPackageError, match="non-regular"):
        capture_skill_archive(special.getvalue())

    malformed = _zip_entries((("SKILL.md", b"---\nname: [unterminated\n---\n"),))
    with pytest.raises(SkillPackageError, match="invalid YAML"):
        capture_skill_archive(malformed)


def test_archive_rejects_decompression_and_file_count_bounds() -> None:
    oversized = _zip_entries((
        ("SKILL.md", b"---\nname: safe-skill\ndescription: Safe.\n---\n"),
        ("assets/large.bin", b"x" * (MAX_SKILL_PACKAGE_BYTES + 1)),
    ))
    with pytest.raises(SkillPackageError, match="entry exceeds"):
        capture_skill_archive(oversized)

    entries = [("SKILL.md", b"---\nname: safe-skill\ndescription: Safe.\n---\n")]
    entries.extend((f"references/{index}.txt", b"x") for index in range(MAX_SKILL_PACKAGE_FILES))
    with pytest.raises(SkillPackageError, match="file count"):
        capture_skill_archive(_zip_entries(entries))


def test_archive_rejects_aggregate_size_before_decompressing_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    oversized = _zip_entries((
        ("SKILL.md", b"---\nname: safe-skill\ndescription: Safe.\n---\n"),
        ("assets/first.bin", b"x" * (MAX_SKILL_PACKAGE_BYTES // 2)),
        ("assets/second.bin", b"y" * (MAX_SKILL_PACKAGE_BYTES // 2)),
    ))
    opened = 0
    original_open = zipfile.ZipFile.open

    def observe_open(self, *args, **kwargs):
        nonlocal opened
        opened += 1
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "open", observe_open)

    with pytest.raises(SkillPackageError, match="uncompressed size"):
        capture_skill_archive(oversized)

    assert opened == 0


@pytest.mark.skipif(os.name == "nt", reason="the deterministic symlink swap requires Unix symlink semantics")
def test_directory_capture_rejects_a_file_replaced_by_a_symlink_before_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _write_package(tmp_path)
    victim = package / "references/policy.md"
    outside = tmp_path / "outside-secret.txt"
    outside.write_text("must not be captured\n", encoding="utf-8")
    original_open = package_module.os.open

    def replace_before_open(path, flags, *args, **kwargs):
        if Path(path) == victim:
            victim.unlink()
            victim.symlink_to(outside)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(package_module.os, "open", replace_before_open)

    with pytest.raises(SkillPackageError, match=r"unreadable|changed during capture"):
        capture_skill_directory(package)


def test_directory_requires_standard_name_to_match_package_root(tmp_path: Path) -> None:
    package = _write_package(tmp_path)
    (package / "SKILL.md").write_text(
        "---\nname: another-name\ndescription: Does not match.\n---\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillPackageError, match="match its package directory"):
        capture_skill_directory(package)


def test_legacy_instruction_content_builds_a_standard_one_file_package() -> None:
    content = SkillContent(
        name="release-check",
        description="Verify a release before publishing it.",
        instructions="Run the release verification.",
        validation=("The release report passes.",),
    )

    snapshot = build_instruction_skill_package(content)
    packaged = snapshot.as_skill_content()

    assert [entry.path for entry in snapshot.entries] == ["SKILL.md"]
    assert packaged.package == snapshot.reference
    assert packaged.instructions == "Run the release verification.\n\n## Validation\n\n- The release report passes."
    assert packaged.validation == ()


def _zip_files(package: Path, paths) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for path in paths:
            source = package / path
            info = zipfile.ZipInfo(path)
            info.create_system = 3
            info.external_attr = source.stat().st_mode << 16
            archive.writestr(info, source.read_bytes())
    return output.getvalue()


def _zip_entries(entries) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in entries:
            archive.writestr(name, content)
    return output.getvalue()
