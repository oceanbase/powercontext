#!/usr/bin/env python3
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

"""Validate the shared 000 migration and freeze it for a declared release."""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tokenize
import warnings
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = Path("src/powercontext/builtin/persistence/migrations")
SERVER = MIGRATIONS / "server_resources"
SOURCES = MIGRATIONS / "server_sources"
DRAFT = "000"
GIT = shutil.which("git") or "/usr/bin/git"
RELEASE_ID = re.compile(r"v(0|[1-9][0-9]*)_(0|[1-9][0-9]*)_(0|[1-9][0-9]*)")
DRAFT_TOKEN = re.compile(r"(?<![A-Za-z0-9])000(?![A-Za-z0-9])")


def bundles(root: Path):
    from powercontext.builtin.artifacts.memory import EmbeddingProfile
    from powercontext.builtin.persistence.migrations import MigrationBundle
    from powercontext.builtin.persistence.migrations.vector_profile import ServerVectorProfile

    with warnings.catch_warnings():
        warnings.filterwarnings("error", message="Revision .* is present more than once")
        result = {"server": MigrationBundle(root / SERVER)}
        for manifest in sorted((root / SOURCES).glob("*/manifest.json")):
            result[manifest.parent.name] = MigrationBundle(root / SERVER, source_directory=manifest.parent)
    # A fixed representative embedding space exercises the profile-dependent
    # checksum inputs; the full template is hashed regardless of dimension.
    embedding = EmbeddingProfile(profile_id="schema-check", model="schema-check", dimension=3)
    for source, bundle in list(result.items()):
        if ServerVectorProfile.resource in bundle.resources:
            result[f"{source}:vector"] = MigrationBundle(
                bundle.directory, source_directory=bundle.source_directory, embedding_profile=embedding
            )
    heads = {bundle.head for bundle in result.values()}
    if len(heads) != 1:
        message = "All registered Server source paths must reach the same head"
        raise ValueError(message)
    for bundle in result.values():
        validate_native_operations(bundle)
        if DRAFT in bundle.revisions and (bundle.head != DRAFT or len(bundle.revisions) < 2):
            message = "000 must be the sole draft at the end of an existing frozen history"
            raise ValueError(message)
        # Native execution must use exactly the same IDs and dependencies as SQLite.
        from alembic.script import ScriptDirectory

        native = ScriptDirectory(str(bundle.snapshot_directory / "mysql"))
        with warnings.catch_warnings():
            warnings.filterwarnings("error", message="Revision .* is present more than once")
            history = list(reversed(list(native.walk_revisions())))
        if tuple(item.revision for item in history) != bundle.revisions:
            message = "Native and SQLite migration chains differ"
            raise ValueError(message)
        if any(item.down_revision != (history[i - 1].revision if i else None) for i, item in enumerate(history)):
            message = "Native migration dependencies differ"
            raise ValueError(message)
    return result


def validate_native_operations(bundle) -> None:
    manifest = json.loads((bundle.snapshot_directory / "manifest.json").read_text(encoding="utf-8"))
    for resource in [*manifest.get("mysql_state_layouts", []), *manifest.get("mysql_layouts", [])]:
        layout = json.loads((bundle.snapshot_directory / resource).read_text(encoding="utf-8"))
        for entry in layout["revisions"].values():
            statements = entry["statements"] if isinstance(entry, dict) else entry
            for statement in statements:
                if statement.startswith("@"):
                    module, separator, operation = statement[1:].partition(":")
                    if not separator or not operation or module not in bundle.resources or not module.endswith(".py"):
                        message = f"Invalid conditional MySQL operation: {statement}"
                        raise ValueError(message)


def native_checksums(bundle):
    from alembic.script import ScriptDirectory

    from powercontext.builtin.persistence.migrations.models import digest

    directory = bundle.snapshot_directory / "mysql"
    hashes = {
        path.relative_to(directory).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in directory.rglob("*.py")
    }
    for revision, resource in bundle.mysql_wrappers.items():
        hashes[f"versions/{revision}_registered.py"] = bundle.resources[resource]
    # Match the native runner's environment and wrapper inputs, while also
    # protecting scripts whose filenames do not follow the revision prefix.
    return {
        item.revision: digest({
            "env": hashes["env.py"],
            "script": hashlib.sha256(Path(item.path).read_bytes()).hexdigest(),
            "wrappers": {
                name: value
                for name, value in hashes.items()
                if name.startswith(f"versions/{item.revision}_") or name == f"versions/{item.revision}.py"
            },
        })
        for item in ScriptDirectory(str(directory)).walk_revisions()
    }


def preserve_history(before, after) -> None:
    for source, old in before.items():
        if source not in after:
            message = f"Registered source removed: {source}"
            raise ValueError(message)
        new = after[source]
        frozen = tuple(revision for revision in old.revisions if revision != DRAFT)
        if new.revisions[: len(frozen)] != frozen:
            message = f"Frozen migration history changed: {source}"
            raise ValueError(message)
        old_native, new_native = native_checksums(old), native_checksums(new)
        for revision in frozen:
            if (
                new.checksums.get(revision) != old.checksums[revision]
                or new_native.get(revision) != old_native[revision]
                or new.fingerprints.get(revision) != old.fingerprints[revision]
            ):
                message = f"Frozen migration resources changed: {source}/{revision}"
                raise ValueError(message)
        additions = new.revisions[len(frozen) :]
        if len(additions) > 1 or any(
            revision != DRAFT and not RELEASE_ID.fullmatch(revision) for revision in additions
        ):
            message = "Append only the shared 000 draft, or its single frozen release revision"
            raise ValueError(message)


def checkout_resources(root: Path, ref: str, target: Path) -> None:
    commit = subprocess.check_output(  # noqa: S603 -- git resolves the ref without a shell
        [GIT, "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        cwd=root,
        text=True,
    ).strip()
    data = subprocess.check_output(  # noqa: S603 -- resolved commit and fixed paths
        [GIT, "archive", commit, str(SERVER), str(SOURCES)],
        cwd=root,
    )
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            path = target / member.name
            if not path.resolve().is_relative_to(target.resolve()):
                message = "Invalid resource path in Git archive"
                raise ValueError(message)
            path.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(member)
            if stream is None:
                message = "Missing resource in Git archive"
                raise ValueError(message)
            path.write_bytes(stream.read())


def check(root: Path, *, base_ref: str | None = None, release: bool = False):
    current = bundles(root)
    if release and any(bundle.head == DRAFT for bundle in current.values()):
        message = "Release contains mutable 000; run make schema-freeze VERSION=X.Y.Z"
        raise ValueError(message)
    if base_ref:
        with tempfile.TemporaryDirectory(prefix="pc-schema-base-") as temporary:
            base = Path(temporary)
            checkout_resources(root, base_ref, base)
            previous = bundles(base)
            preserve_history(previous, current)
            head = current["server"].head
            if head != DRAFT and head not in previous["server"].revisions:
                policy = json.loads((root / "release/upgrade-support.json").read_text(encoding="utf-8"))
                if head != "v" + policy["target_version"].replace(".", "_"):
                    message = "New frozen revision must match the declared release target"
                    raise ValueError(message)
    return current


def rewrite_json(value, names: dict[str, str], revision: str, field: str = ""):
    identity_fields = {"revision", "down_revision", "source_revision", "target_revision"}
    identity_maps = {"revision_resources", "sqlite_fingerprints", "domain_readiness", "revisions", "mysql_wrappers"}
    identity_values = {"sqlite_baselines", "retained_revisions"}
    if isinstance(value, str):
        if value == DRAFT and field in identity_fields | identity_values:
            return revision
        if field == "statements" and value.startswith("@"):
            module, separator, operation = value[1:].partition(":")
            if separator:
                return f"@{names.get(module, module)}:{operation}"
        return names.get(value, value)
    if isinstance(value, list):
        return [rewrite_json(item, names, revision, field) for item in value]
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            new_key = revision if key == DRAFT and field in identity_maps else key
            if new_key in result:
                message = f"Freezing would duplicate JSON key {new_key}"
                raise ValueError(message)
            result[new_key] = rewrite_json(item, names, revision, field if field in identity_values else key)
        return result
    return value


def rewrite_python(content: str, names: dict[str, str], revision: str) -> str:
    # Only revision assignments and known resource paths are rewritten. A data
    # default or SQL literal equal to 000 must remain byte-for-byte unchanged.
    identities = set()
    lines = content.splitlines()
    for node in ast.walk(ast.parse(content)):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if any(isinstance(target, ast.Name) and target.id in {"revision", "down_revision"} for target in targets):
            value = node.value
            if isinstance(value, ast.Constant) and value.value == DRAFT:
                column = len(lines[value.lineno - 1].encode("utf-8")[: value.col_offset].decode("utf-8"))
                identities.add((value.lineno, column))
    tokens = []
    for token in tokenize.generate_tokens(io.StringIO(content).readline):
        if token.type == tokenize.STRING:
            literal = ast.parse(token.string, mode="eval").body
            value = literal.value if isinstance(literal, ast.Constant) else None
            if isinstance(value, str):
                updated = revision if token.start in identities else names.get(value, value)
                if updated != value:
                    token = token._replace(string=repr(updated))
        tokens.append(token)
    return tokenize.untokenize(tokens)


def freeze(root: Path, version: str) -> str | None:  # noqa: C901 -- validate the full write plan before mutations
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
        message = "Freeze VERSION must be a stable X.Y.Z release"
        raise ValueError(message)
    revision = "v" + version.replace(".", "_")
    if len(revision) > 32:
        message = "Revision exceeds the 32-character storage limit"
        raise ValueError(message)
    policy_path = root / "release/upgrade-support.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    contract = (root / "openapi/powercontext.yaml").read_text(encoding="utf-8")
    declared = re.search(r"^  version: (.+)$", contract, re.MULTILINE)
    if policy["target_version"] != version or declared is None or declared[1] != version:
        message = "Run make version-bump first; VERSION must match both release declarations"
        raise ValueError(message)
    before = bundles(root)
    if not any(bundle.head == DRAFT for bundle in before.values()):
        return None  # No schema change: preserve the existing head and all resources.
    if any(revision in bundle.revisions for bundle in before.values()):
        message = f"Release revision already exists: {revision}"
        raise ValueError(message)
    originals = {
        path.relative_to(root): path.read_bytes()
        for folder in (root / SERVER, root / SOURCES)
        for path in folder.rglob("*")
        if path.is_file() and path.suffix in {".py", ".json", ".sql"}
    }
    # Paths are registered relative to either the main bundle or its source overlay.
    names = {}
    for path in originals:
        relative = (
            path.relative_to(SERVER) if path.is_relative_to(SERVER) else Path(*path.relative_to(SOURCES).parts[1:])
        )
        if DRAFT_TOKEN.search(relative.as_posix()):
            names[relative.as_posix()] = DRAFT_TOKEN.sub(revision, relative.as_posix())
    # Support Path joins that spell the basename rather than the registered path.
    names.update({Path(old).name: Path(new).name for old, new in list(names.items())})
    updates = {}
    for path, raw in originals.items():
        destination = Path(DRAFT_TOKEN.sub(revision, path.as_posix()))
        if destination != path and destination in originals:
            message = f"Freeze destination already exists: {destination}"
            raise ValueError(message)
        content = raw.decode("utf-8")
        if path.suffix == ".json":
            value = json.loads(content)
            updated = rewrite_json(value, names, revision)
            if updated != value:
                content = json.dumps(updated, ensure_ascii=False, indent=2) + "\n"
        elif path.suffix == ".py":
            content = rewrite_python(content, names, revision)
        if destination in updates:
            message = f"Duplicate freeze destination: {destination}"
            raise ValueError(message)
        updates[destination] = content.encode("utf-8")
    # Validate the complete transformed bundle before touching the checkout.
    with tempfile.TemporaryDirectory(prefix="pc-schema-freeze-") as temporary:
        staging = Path(temporary)
        for path, content in updates.items():
            target = staging / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        after = check(staging, release=True)
        preserve_history(before, after)
        if any(bundle.head != revision for bundle in after.values()):
            message = "Freeze did not update every registered source head"
            raise ValueError(message)
    try:
        for path, content in updates.items():
            if originals.get(path) != content:
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_bytes(content)
        for path in originals.keys() - updates.keys():
            (root / path).unlink()
        from upgrade_release_gate import managed_digest

        policy["schema_review"]["persistence_sha256"] = managed_digest(root)
        policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        for path in updates.keys() - originals.keys():
            (root / path).unlink(missing_ok=True)
        for path, content in originals.items():
            (root / path).write_bytes(content)
        raise
    return revision


def check_release_wheel(wheel: Path) -> None:
    """Inspect the actual publication artifact, not only the source checkout."""
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if "/migrations/server_" not in name or "/versions/" not in name or not name.endswith(".py"):
                continue
            tree = ast.parse(archive.read(name), filename=name)
            for node in tree.body:
                if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                    continue
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if (
                    any(isinstance(target, ast.Name) and target.id == "revision" for target in targets)
                    and isinstance(node.value, ast.Constant)
                    and node.value.value == DRAFT
                ):
                    message = "Release wheel contains mutable 000; freeze the cumulative migration first"
                    raise ValueError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "freeze"))
    parser.add_argument("--version", default=os.environ.get("VERSION"))
    parser.add_argument("--base-ref")
    parser.add_argument("--release", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "freeze":
            if not args.version:
                message = "VERSION is required"
                raise ValueError(message)  # noqa: TRY301 -- CLI argument validation
            result = freeze(ROOT, args.version)
            print(f"Frozen schema: {result}" if result else "No pending schema change; head is unchanged")
        else:
            current = check(ROOT, base_ref=args.base_ref, release=args.release)
            print(f"Server migration head: {current['server'].head}")
    except (ValueError, OSError, RuntimeError, Warning, subprocess.CalledProcessError) as error:
        print(f"Schema release check failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
