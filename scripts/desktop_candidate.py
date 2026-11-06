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

"""Assemble and verify the read-only Desktop candidate handoff for #2112."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from packaging.version import Version

ROOT = Path(__file__).resolve().parents[1]
TARGETS = {
    "windows-x86_64": ("windows", "x86_64", "nsis", "x64-setup.exe"),
    "macos-arm64": ("macos", "arm64", "dmg", "aarch64.dmg"),
    "macos-x86_64": ("macos", "x86_64", "dmg", "x64.dmg"),
}
CONTRACT_FILES = {
    "operations.json": "desktop/src-tauri/src/transport/operations.json",
    "compatibility.json": "desktop/src-tauri/src/connections/compatibility.json",
}
PRODUCT_NAME = "PowerContext Desktop"
PRODUCT_ID = "com.powercontext.desktop"
UNQUALIFIED = "unqualified_candidate"
MANIFEST = "desktop-release-candidate.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def version(value: str) -> bool:
    return bool(re.fullmatch(r"(?:0|[1-9][0-9]{0,4})(?:\.(?:0|[1-9][0-9]{0,4})){2}", value)) and all(
        int(part) <= 65535 for part in value.split(".")
    )


def identity(desktop_version: str, server_version: str, source_sha: str, server_source_sha: str) -> dict[str, str]:
    require(version(desktop_version), "expected exact X.Y.Z Desktop version (0-65535 per part)")
    require(
        len(server_version) <= 100 and str(Version(server_version)) == server_version,
        "expected an exact canonical Python Server version",
    )
    for label, sha in (("source_sha", source_sha), ("server_source_sha", server_source_sha)):
        require(bool(re.fullmatch(r"[0-9a-f]{40}", sha)), f"{label} must be a full lowercase Git commit")
    return {
        "version": desktop_version,
        "release_tag": f"desktop-v{desktop_version}",
        "source_sha": source_sha,
        "server_source_sha": server_source_sha,
        "server_version": server_version,
    }


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", "-C", str(ROOT), *args])  # noqa: S603, S607 - fixed Git commands


def check_checkout(release: dict[str, str]) -> None:
    require(git("rev-parse", "HEAD").decode().strip() == release["source_sha"], "checkout differs from source_sha")
    require(git("cat-file", "-t", release["server_source_sha"]).strip() == b"commit", "Server source must be a commit")
    require(not git("status", "--porcelain", "--untracked-files=normal"), "candidate requires a clean checkout")
    ref = f"refs/tags/{release['release_tag']}"
    if git("tag", "--list", release["release_tag"]):
        require(
            git("rev-parse", f"{ref}^{{commit}}").decode().strip() == release["source_sha"],
            "Desktop tag already points to a different commit",
        )


def regular(path: Path) -> Path:
    # Do not follow a link, including a Windows junction, anywhere inside a handoff.
    require(
        not path.is_symlink() and not getattr(path, "is_junction", lambda: False)() and path.is_file(),
        f"missing or unsafe file: {path.name}",
    )
    return path


def directory(path: Path) -> None:
    require(
        not path.is_symlink() and not getattr(path, "is_junction", lambda: False)() and path.is_dir(),
        f"missing or unsafe directory: {path.name}",
    )


def expect_entries(path: Path, names: set[str]) -> None:
    directory(path)
    require({p.name for p in path.iterdir()} == names, f"unexpected or missing entries in {path.name}")


def read(path: Path) -> Any:
    return json.loads(regular(path).read_bytes())


def digest(path: Path) -> str:
    with regular(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def asset(root: Path, path: Path) -> dict[str, Any]:
    return {
        "name": path.relative_to(root).as_posix(),
        "size": path.stat().st_size,
        "sha256": digest(path),
    }


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def create(path: Path, content: bytes) -> None:
    with path.open("xb") as stream:
        stream.write(content)


def package_name(release: dict[str, str], target: str) -> str:
    ext = "exe" if target.startswith("windows-") else "dmg"
    return f"powercontext-desktop-{release['version']}-{target}.{ext}"


def resource_names(release: dict[str, str], target: str) -> dict[str, str]:
    suffix = f"{release['version']}-{target}"
    return {
        "server_archive": f"powercontext-server-{suffix}.tar.gz",
        "server_catalog": f"powercontext-server-{suffix}.json",
        "ops_archive": f"powercontext-ops-{suffix}.tar.gz",
        "ops_provision": f"powercontext-ops-provision-{suffix}.json",
        "ops_bundle_manifest": f"powercontext-ops-bundle-{suffix}.json",
    }


def server_url(target: str) -> str:
    return f"https://candidate.invalid/{target}/server.tar.gz"


def prepare_desktop(candidate: Path, desktop: Path, release: dict[str, str]) -> None:
    """Pin the producer metadata in the app using the existing Tauri resource mapping."""
    staging = desktop / ".artifacts/ops-resource"
    (staging / "server").mkdir(parents=True)
    for name in ("ops-provision.json", "server/catalog.json"):
        create(staging / name, regular(candidate / name).read_bytes())
    create(
        desktop / ".artifacts/candidate.conf.json",
        json_bytes({
            "productName": PRODUCT_NAME,
            "identifier": PRODUCT_ID,
            "version": release["version"],
            "bundle": {"shortDescription": "PowerContext Desktop candidate"},
        }),
    )


def project_wheel(inputs: dict[str, Any]) -> dict[str, Any]:
    [wheel] = [item for item in inputs["wheels"] if item["name"] == "powercontext"]
    require(
        bool(re.fullmatch(r"powercontext-[a-zA-Z0-9_.+!-]+\.whl", wheel["filename"])),
        "unexpected Server wheel name",
    )
    return wheel


def stage_target(
    output: Path, bundles: Path, resources: Path, inputs: Path, smoke: Path, release: dict[str, str], target: str
) -> None:
    """Collect one fresh product build; internal/preview package names are never accepted."""
    _, _, bundle_dir, suffix = TARGETS[target]
    expected = f"{PRODUCT_NAME}_{release['version']}_{suffix}"
    bundle_folder = bundles / bundle_dir
    directory(bundle_folder)
    require(
        [p.name for p in bundle_folder.glob(f"*.{suffix.rsplit('.', 1)[1]}")] == [expected],
        f"expected exactly one normal product bundle: {expected}",
    )
    output.mkdir(parents=True)
    wheel = project_wheel(read(inputs / "inputs.json"))["filename"]
    names = resource_names(release, target)
    files = {
        package_name(release, target): bundle_folder / expected,
        names["server_archive"]: resources / "server/server.tar.gz",
        names["ops_archive"]: resources / "ops.tar.gz",
        names["server_catalog"]: resources / "server/catalog.json",
        names["ops_provision"]: resources / "ops-provision.json",
        names["ops_bundle_manifest"]: resources / "ops/bundle.json",
        "server-resource.json": resources / "server/resource.json",
        "ops-resource.json": resources / "ops-resource.json",
        "producer-candidate.json": resources / "candidate.json",
        "inputs.json": inputs / "inputs.json",
        "requirements.lock": inputs / "requirements.lock",
        "upstream-requirements.lock": inputs / "upstream-requirements.lock",
        "resource-smoke.json": smoke,
        wheel: inputs / "wheels" / wheel,
    }
    for name, source in files.items():
        shutil.copyfile(regular(source), output / name)
    # Confirm the exact resource pins passed to the builder were the ones staged for handoff.
    for path, name in (
        ("ops-provision.json", names["ops_provision"]),
        ("server/catalog.json", names["server_catalog"]),
    ):
        require(
            digest(ROOT / "desktop/.artifacts/ops-resource" / path) == digest(output / name),
            f"{target}: bundle input differs from collected {name}",
        )


def validate_target(
    root: Path, target: str, release: dict[str, str], contract: dict[str, Any], profiles: list[dict[str, Any]]
) -> dict[str, Any]:
    path = root / target
    directory(path)
    inputs = read(path / "inputs.json")
    wheel = project_wheel(inputs)
    names = {
        "desktop": package_name(release, target),
        **resource_names(release, target),
        "server_wheel": wheel["filename"],
        "producer_candidate": "producer-candidate.json",
        "server_resource": "server-resource.json",
        "ops_resource": "ops-resource.json",
        "inputs": "inputs.json",
        "requirements": "requirements.lock",
        "upstream_requirements": "upstream-requirements.lock",
        "resource_smoke": "resource-smoke.json",
    }
    expect_entries(path, set(names.values()))
    assets = {key: asset(root, path / name) for key, name in names.items()}
    hashes = {key: item["sha256"] for key, item in assets.items()}
    require(all(item["size"] > 0 for item in assets.values()), f"{target}: empty asset")
    producer = read(path / names["producer_candidate"])
    binding = producer["binding"]
    platform, architecture, _, _ = TARGETS[target]
    source_sha = release["source_sha"]
    require(
        inputs["format_version"] == 1
        and inputs["target"] == target
        and inputs["source"]["commit"] == binding["source_commit"] == release["server_source_sha"]
        and inputs["server_version"] == binding["server_version"] == wheel["version"] == release["server_version"]
        and inputs["interpreter"]["platform"] == platform
        and inputs["interpreter"]["architecture"] == architecture
        and inputs["interpreter"]["sha256"] == binding["python_source_sha256"]
        and inputs["requirements_sha256"] == hashes["requirements"]
        and inputs["upstream_requirements_sha256"] == hashes["upstream_requirements"]
        and all(
            p["git_commit"] == source_sha and p["matches_committed_files"] is True
            for p in (inputs["preparer"], binding["producer"])
        ),
        f"{target}: producer source, target or locked inputs mismatch",
    )
    expected_ref = "unqualified-candidate:sha256:" + hashlib.sha256(json_bytes(binding)).hexdigest()
    require(
        producer["format_version"] == 1
        and producer["status"] == UNQUALIFIED
        and producer["signing"]
        == producer["notarization"]
        == producer["clean_machine_qualification"]
        == "not_performed"
        and producer["qualification_ref"] == expected_ref
        and producer["catalog_sha256"] == hashes["server_catalog"]
        and producer["ops_archive_sha256"] == binding["ops_archive_sha256"] == hashes["ops_archive"]
        and binding["inputs_sha256"] == hashes["inputs"]
        and binding["server_archive_sha256"] == hashes["server_archive"]
        and binding["ops_bundle_manifest_sha256"] == hashes["ops_bundle_manifest"]
        and binding["server_wheel_sha256"] == wheel["sha256"] == hashes["server_wheel"],
        f"{target}: producer digest or candidate status mismatch",
    )
    catalog = read(path / names["server_catalog"])
    [server] = catalog["resources"]
    descriptor = read(path / names["ops_provision"])
    server_resource = read(path / names["server_resource"])
    ops_resource = read(path / names["ops_resource"])
    require(
        catalog["format_version"] == 1
        and server["platform"] == platform
        and server["architecture"] == architecture
        and server["server_version"] == release["server_version"]
        and server["python_version"] == inputs["interpreter"]["python_version"]
        and server["qualification_ref"] == expected_ref
        and server["url"] == server_url(target)
        and server["archive_sha256"] == hashes["server_archive"]
        and server["archive_size"] == assets["server_archive"]["size"]
        and server["python_relative"] == inputs["interpreter"]["server_python_relative"]
        and (platform != "windows" or server["python_relative"] == "pythonw.exe")
        and descriptor
        == {
            "format_version": 1,
            "binding_mode": "managed_catalog",
            "bundle_manifest_sha256": hashes["ops_bundle_manifest"],
            "catalog_sha256": hashes["server_catalog"],
        }
        and server_resource["resource"] == server
        and server_resource["inputs"] == inputs
        and ops_resource["archive_sha256"] == hashes["ops_archive"]
        and ops_resource["archive_size"] == assets["ops_archive"]["size"]
        and all(
            r["format_version"] == 1 and r["status"] == UNQUALIFIED and r["inputs_sha256"] == hashes["inputs"]
            for r in (server_resource, ops_resource)
        ),
        f"{target}: resource metadata or Desktop provision pin mismatch",
    )
    smoke = read(path / names["resource_smoke"])
    require(
        smoke["status"] == "candidate_smoke_passed"
        and smoke["qualified"] is False
        and smoke["archive_sha256"] == hashes["server_archive"]
        and smoke["catalog_sha256"] == hashes["server_catalog"]
        and smoke["ops"] == "independent_inspection_passed"
        and smoke["server"]["launcher"] == "passed",
        f"{target}: native resource smoke missing or mismatched",
    )
    records = [
        {"id": profile["id"], "evidence": profile["evidence"]}
        for profile in profiles
        if not profile["id"].startswith("ci-")
        and profile["serverCommit"] == release["server_source_sha"]
        and profile["artifactSha256"] == hashes["server_wheel"]
        and profile["contractSha256"] == contract["contractSha256"]
        and set(contract["operations"]).issubset(profile["operations"])
    ]
    return {
        "build_kind": "product",
        "assets": {key: assets.pop(key) for key in ("desktop", "server_archive", "server_catalog", "ops_archive")},
        "provenance": assets,
        "qualification": {"producer_ref": expected_ref, "matching_product_records": records, "status": UNQUALIFIED},
        "runtime": {
            "server_version": server["server_version"],
            "python_version": server["python_version"],
            "ops_protocol_min": server["ops_protocol_min"],
            "ops_protocol_max": server["ops_protocol_max"],
        },
    }


def manifest(root: Path, release: dict[str, str], *, verifying: bool) -> dict[str, Any]:
    expect_entries(root, {*TARGETS, "contract"} | ({MANIFEST} if verifying else set()))
    contract_path = root / "contract"
    expect_entries(contract_path, set(CONTRACT_FILES))
    operations = read(contract_path / "operations.json")
    profiles = read(contract_path / "compatibility.json")
    return {
        "schema_version": 1,
        "kind": "desktop-product-candidate",
        **release,
        "status": UNQUALIFIED,
        "publishable": False,
        "contract": {
            "sha256": operations["contractSha256"],
            "operations": sorted(operations["operations"]),
            "assets": {name: asset(root, contract_path / name) for name in CONTRACT_FILES},
        },
        "targets": {target: validate_target(root, target, release, operations, profiles) for target in TARGETS},
    }


def assemble(root: Path, release: dict[str, str]) -> None:
    expect_entries(root, set(TARGETS))
    path = root / "contract"
    path.mkdir()
    for name, source in CONTRACT_FILES.items():
        create(path / name, git("show", f"{release['source_sha']}:{source}"))
    create(root / MANIFEST, json_bytes(manifest(root, release, verifying=False)))


def verify(root: Path, release: dict[str, str], manifest_sha256: str) -> None:
    directory(root)
    require(digest(root / MANIFEST) == manifest_sha256, "candidate manifest digest mismatch")
    require(
        read(root / MANIFEST) == manifest(root, release, verifying=True), "candidate manifest does not match assets"
    )


def argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("identity", "prepare", "stage", "assemble", "verify"):
        sub = commands.add_parser(command)
        sub.add_argument("--version", required=True)
        sub.add_argument("--server-version", required=True)
        sub.add_argument("--source-sha", required=True)
        sub.add_argument("--server-source-sha", required=True)
        if command in {"assemble", "verify"}:
            sub.add_argument("--root", type=Path, required=True)
        if command in {"prepare", "stage"}:
            sub.add_argument("--resources", type=Path, required=True)
        if command == "stage":
            for name in ("output", "bundles", "inputs", "smoke"):
                sub.add_argument(f"--{name}", type=Path, required=True)
            sub.add_argument("--target", choices=TARGETS, required=True)
        if command == "verify":
            sub.add_argument("--manifest-sha256", required=True)
    return parser


def main() -> None:
    parser = argument_parser()
    args = parser.parse_args()
    try:
        release = identity(args.version, args.server_version, args.source_sha, args.server_source_sha)
        if args.command == "verify":
            verify(args.root, release, args.manifest_sha256)
        else:
            check_checkout(release)
            if args.command == "identity":
                print(json_bytes(release).decode(), end="")
            elif args.command == "prepare":
                prepare_desktop(args.resources, ROOT / "desktop", release)
            elif args.command == "stage":
                stage_target(args.output, args.bundles, args.resources, args.inputs, args.smoke, release, args.target)
            else:
                assemble(args.root, release)
                print(f"candidate_manifest_sha256={digest(args.root / MANIFEST)}")
    except (OSError, ValueError, KeyError, TypeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Desktop candidate rejected: {exc}\n")


if __name__ == "__main__":
    main()
