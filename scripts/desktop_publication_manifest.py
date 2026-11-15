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

"""Validate the byte and evidence handoff for Desktop release publication."""

from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO
from zipfile import ZipFile

REPOSITORY = "oceanbase/powercontext"
CANDIDATE = "desktop-release-candidate.json"
MANIFEST = "desktop-release.json"
PROVISIONING = "desktop-provisioning-evidence.json"
CHECKSUMS = "SHA256SUMS.txt"
REQUIRED = {"windows-x86_64", "macos-arm64", "macos-x86_64"}
OPTIONAL = {"linux-x86_64"}
SUFFIX = {"windows-x86_64": ".exe", "macos-arm64": ".dmg", "macos-x86_64": ".dmg", "linux-x86_64": ".deb"}
SIGNING = {
    "windows-x86_64": "authenticode",
    "macos-arm64": "developer-id-notarized-stapled",
    "macos-x86_64": "developer-id-notarized-stapled",
    "linux-x86_64": "linux-qualified",
}
ROLES = {"desktop", "server_archive", "server_catalog", "ops_archive", "ops_provision", "ops_bundle_manifest"}
SHA = r"[0-9a-f]{64}"
COMMIT = r"[0-9a-f]{40}"
TAG_PREFIX = "desktop-v"
TAG = TAG_PREFIX + r"(?:0|[1-9][0-9]{0,4})(?:\.(?:0|[1-9][0-9]{0,4})){2}"
NAME = r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,127}"
REPORT = r"https://github\.com/oceanbase/powercontext/(?:issues/[1-9][0-9]*(?:#issuecomment-[1-9][0-9]*)?|actions/runs/[1-9][0-9]*)"
MAX_ASSET = 2 * 1024**3 - 1  # GitHub per-asset limit
MAX_STAGED = 6 * 1024**3
MAX_JSON = 2 * 1024**2


class PublicationError(RuntimeError):
    """Release input or remote state violated the publication contract."""


def require(condition: object, message: str) -> None:
    if not condition:
        raise PublicationError(message)


def matching(value: Any, pattern: str, label: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise PublicationError(f"Invalid {label}")  # noqa: TRY003
    return value


def unique_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        require(key not in result, "Duplicate key in release JSON")
        result[key] = value
    return result


def read_json(path: Path, *, max_bytes: int = MAX_JSON) -> dict[str, Any]:
    require(path.stat().st_size <= max_bytes, f"{path.name} exceeds the JSON size limit")
    value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys)
    require(isinstance(value, dict), f"{path.name} must contain a JSON object")
    return value


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    sha256: str

    @classmethod
    def parse(cls, value: dict[str, Any]) -> Asset:
        name = matching(value["name"], NAME, "asset filename")
        require(not name.endswith(".") and ".." not in name, "Unsafe asset filename")
        size = value["size"]
        require(type(size) is int and 0 < size <= MAX_ASSET, f"Invalid size for {name}")
        return cls(name, size, matching(value["sha256"], SHA, "asset SHA-256"))

    @classmethod
    def from_file(cls, path: Path) -> Asset:
        with path.open("rb") as stream:
            return cls(path.name, path.stat().st_size, hashlib.file_digest(stream, "sha256").hexdigest())


def verify_bytes(stream: BinaryIO, asset: Asset, output: BinaryIO | None = None) -> None:
    digest = hashlib.sha256()
    received = 0
    while chunk := stream.read(1024**2):
        received += len(chunk)
        require(received <= asset.size, f"Unexpected size for {asset.name}")
        digest.update(chunk)
        if output is not None:
            output.write(chunk)
    require(received == asset.size and digest.hexdigest() == asset.sha256, f"Byte verification failed: {asset.name}")


def extract_stage(archive: Path, destination: Path, *, only: str | None = None) -> None:
    """Extract only a flat, bounded set of inert regular files into an empty directory."""
    with ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if only is not None:
            require(
                len(entries) == 1 and entries[0].filename == only and entries[0].file_size <= MAX_JSON,
                "Unexpected contents in the 09-A manifest artifact",
            )
        require(0 < len(entries) <= 60, "Invalid staged asset count")
        require(sum(entry.file_size for entry in entries) <= MAX_STAGED, "Staging exceeds the total size limit")
        seen: set[str] = set()
        for entry in entries:
            name = matching(entry.filename, NAME, "staged filename")
            require(not name.endswith(".") and ".." not in name, "Unsafe staged filename")
            require(name.casefold() not in seen, "Duplicate staged asset filename")
            require(
                stat.S_IFMT(entry.external_attr >> 16) in (0, stat.S_IFREG) and 0 < entry.file_size <= MAX_ASSET,
                f"Staging member must be a bounded regular file: {name}",
            )
            seen.add(name.casefold())
            with bundle.open(entry) as source, (destination / name).open("xb") as output:
                while data := source.read(1024**2):
                    output.write(data)


def asset_url(tag: str, name: str) -> str:
    return f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"


@dataclass(frozen=True)
class Bundle:
    root: Path
    tag: str
    source_sha: str
    manifest_sha256: str
    targets: dict[str, dict[str, Asset]]
    assets: dict[str, Asset]
    qualification_reports: dict[str, str]
    server_reports: dict[str, str]
    server_source_sha: str
    candidate_sha256: str

    @property
    def release_marker(self) -> str:
        return f"<!-- desktop-release-v1 source={self.source_sha} manifest={self.manifest_sha256} -->"

    def notes(self) -> str:
        qualified = "\n".join(
            f"- `{target}`: [platform acceptance]({report})"
            for target, report in sorted(self.qualification_reports.items())
        )
        return (
            f"{self.release_marker}\n\n"
            f"Qualified Desktop packages and pinned local Server/Ops resources for `{self.source_sha}`.\n\n"
            f"{qualified}\n\n"
            "Local setup requires network access. Use only the platform packages listed here. "
            "See desktop-release.json for exact support and installation evidence, and SHA256SUMS.txt for file hashes.\n"
        )


def validate_platform(record: dict[str, Any], target: str, assets: dict[str, Asset]) -> str:
    require(record["kind"] == "installed-product", f"{target}: native product evidence required")
    require(record["signing"] == SIGNING[target], f"{target}: missing required signing/notarization evidence")
    require(
        record["asset_sha256"] == {role: asset.sha256 for role, asset in assets.items()},
        f"{target}: qualification must bind every final asset",
    )
    require(record["clean_machine_provisioning"] == "passed", f"{target}: clean-machine provisioning is required")
    require(
        record["installed_catalog_sha256"] == assets["server_catalog"].sha256
        and record["installed_ops_provision_sha256"] == assets["ops_provision"].sha256
        and record["installed_ops_bundle_manifest_sha256"] == assets["ops_bundle_manifest"].sha256,
        f"{target}: installed signed package must bind the published catalog and Ops pins",
    )
    for field in ("os_version", "webview_version", "server_version", "runtime_version", "limitations"):
        require(isinstance(record[field], str) and 0 < len(record[field]) <= 500, f"{target}: missing {field}")
    return matching(record["report"], REPORT, "platform acceptance report URL")


def validate_catalog(
    root: Path, tag: str, target: str, assets: dict[str, Asset], qualification: dict[str, Any]
) -> None:
    catalog = read_json(root / assets["server_catalog"].name, max_bytes=32 * 1024**2)
    require(
        catalog["format_version"] == 1 and len(catalog["resources"]) == 1,
        f"{target}: expected one pinned Server resource",
    )
    spec = catalog["resources"][0]
    archive = assets["server_archive"]
    os_name, architecture = target.split("-", 1)
    require(
        spec["platform"] == os_name
        and spec["architecture"] == architecture
        and spec["server_version"] == qualification["server_version"]
        and spec["archive_sha256"] == archive.sha256
        and spec["archive_size"] == archive.size
        and spec["url"] == asset_url(tag, archive.name),
        f"{target}: catalog does not identify the final published Server resource",
    )
    require(
        spec["qualification_ref"] == qualification["server"]["id"],
        f"{target}: catalog qualification differs from the approved Server qualification",
    )
    require(
        f"git+https://github.com/{REPOSITORY}@{qualification['server']['source_sha']}" in spec["source_refs"]
        and spec["python_version"] == qualification["runtime_version"],
        f"{target}: catalog does not identify the approved Server commit and interpreter",
    )
    require(
        set(spec["allowed_origins"]) == {"https://github.com", "https://release-assets.githubusercontent.com"},
        f"{target}: catalog must permit the GitHub asset origin and its download redirect",
    )


def validate_provision(root: Path, target: str, assets: dict[str, Asset]) -> None:
    spec = read_json(root / assets["ops_provision"].name)
    require(
        spec
        == {
            "format_version": 1,
            "binding_mode": "managed_catalog",
            "catalog_sha256": assets["server_catalog"].sha256,
            "bundle_manifest_sha256": assets["ops_bundle_manifest"].sha256,
        },
        f"{target}: Ops provision descriptor does not pin the final catalog and helper",
    )


def candidate_assets(origin: dict[str, Any], target: str, version: str) -> dict[str, Asset]:
    entries = {
        **origin["assets"],
        "ops_provision": origin["provenance"]["ops_provision"],
        "ops_bundle_manifest": origin["provenance"]["ops_bundle_manifest"],
    }
    require(entries.keys() == ROLES, f"{target}: incomplete candidate asset inventory")
    result = {}
    for role, entry in entries.items():
        name = entry["name"]
        require(isinstance(name, str) and name.startswith(target + "/"), f"{target}: invalid candidate asset path")
        result[role] = Asset.parse({**entry, "name": name.removeprefix(target + "/")})
    require(
        result["desktop"].name == f"powercontext-desktop-{version}-{target}{SUFFIX[target]}",
        f"{target}: installer is not the normal product package",
    )
    return result


def validate_server_qualification(
    target: str, qualification: dict[str, Any], origin: dict[str, Any], candidate: dict[str, Any]
) -> None:
    server = qualification["server"]
    ref = matching(server["id"], NAME, "Server qualification ID")
    require(
        not ref.lower().startswith(("ci-", "fixture", "unqualified", "candidate")),
        f"{target}: fixture or unqualified Server is not publishable",
    )
    matching(server["report"], REPORT, "Server qualification report URL")
    require(
        any(record["id"] == ref for record in origin["qualification"]["matching_product_records"]),
        f"{target}: Desktop does not carry a matching product qualification for this Server",
    )
    require(
        server["kind"] == "qualified-product-server"
        and server["wheel_sha256"]
        == matching(origin["provenance"]["server_wheel"]["sha256"], SHA, "Server wheel SHA-256")
        and server["contract_sha256"] == matching(candidate["contract"]["sha256"], SHA, "Desktop contract SHA-256")
        and server["source_sha"] == matching(candidate["server_source_sha"], COMMIT, "Server source commit")
        and qualification["server_version"] == origin["runtime"]["server_version"] == candidate["server_version"]
        and qualification["runtime_version"] == origin["runtime"]["python_version"],
        f"{target}: product qualification does not identify the candidate Server wheel and Desktop contract",
    )


def validate_bundle(
    root: Path,
    tag: str,
    source: str,
    manifest_sha: str,
    *,
    candidate_run_id: int,
    candidate_artifact_id: int,
    promote: bool,
) -> Bundle:
    matching(tag, TAG, "Desktop release tag")
    matching(source, COMMIT, "source commit")
    matching(manifest_sha, SHA, "release manifest SHA-256")
    manifest_asset = Asset.from_file(root / MANIFEST)
    require(manifest_asset.sha256 == manifest_sha, "Release manifest differs from the approved digest")
    manifest = read_json(root / MANIFEST)
    require(
        manifest["format_version"] == 1 and manifest["kind"] == "qualified-desktop-release",
        "Unsupported release manifest",
    )
    require((manifest["release_tag"], manifest["source_sha"]) == (tag, source), "Release identity mismatch")

    candidate_asset = Asset.from_file(root / CANDIDATE)
    require(
        candidate_asset.sha256 == manifest["candidate_manifest_sha256"]
        and manifest["candidate_run_id"] == candidate_run_id
        and manifest["candidate_artifact_id"] == candidate_artifact_id,
        "Candidate manifest identity mismatch",
    )
    candidate = read_json(root / CANDIDATE)
    candidate_targets = candidate["targets"]
    require(
        candidate["schema_version"] == 1
        and candidate["kind"] == "desktop-product-candidate"
        and candidate["status"] == "unqualified_candidate"
        and candidate["publishable"] is False,
        "Expected the normal 09-A product candidate manifest",
    )
    version = tag.removeprefix(TAG_PREFIX)
    require(
        (candidate["release_tag"], candidate["source_sha"], candidate["version"]) == (tag, source, version),
        "Candidate identity mismatch",
    )

    records = manifest["targets"]
    require(REQUIRED <= records.keys() <= REQUIRED | OPTIONAL, "Required Windows and both macOS targets are mandatory")
    require(
        records.keys() <= candidate_targets.keys() <= REQUIRED | OPTIONAL, "Unrecognized or missing candidate target"
    )
    targets: dict[str, dict[str, Asset]] = {}
    assets = {manifest_asset.name: manifest_asset, candidate_asset.name: candidate_asset}
    names = {MANIFEST.casefold(), CANDIDATE.casefold(), PROVISIONING.casefold(), CHECKSUMS.casefold()}
    reports = {}
    server_reports = {}
    for target, record in records.items():
        origin = candidate_targets[target]
        originals = candidate_assets(origin, target, version)
        require(
            origin["build_kind"] == "product" and origin["qualification"]["status"] == "unqualified_candidate",
            f"{target}: unexpected candidate qualification status",
        )
        qualification = record["qualification"]
        validate_server_qualification(target, qualification, origin, candidate)
        final = {role: Asset.parse(value) for role, value in record["assets"].items()}
        require(final.keys() == ROLES, f"{target}: incomplete final asset inventory")
        if target in REQUIRED:
            require(
                final["desktop"].sha256 != originals["desktop"].sha256,
                f"{target}: the unsigned candidate installer cannot be a signed release asset",
            )
        require(
            record["candidate_asset_sha256"] == {role: asset.sha256 for role, asset in originals.items()},
            f"{target}: signing handoff must identify every original candidate asset",
        )
        for role in sorted(ROLES):
            require(final[role].name == originals[role].name, f"{target}: final {role} has wrong filename")
        for asset in final.values():
            require(asset.name.casefold() not in names, "Duplicate release asset filename")
            names.add(asset.name.casefold())
            require(Asset.from_file(root / asset.name) == asset, f"Staged bytes differ: {asset.name}")
            assets[asset.name] = asset
        targets[target] = final
        reports[target] = validate_platform(qualification, target, final)
        server_reports[target] = qualification["server"]["report"]
        validate_catalog(root, tag, target, final, qualification)
        validate_provision(root, target, final)

    expected = set(assets) | ({PROVISIONING} if promote else set())
    require({file.name for file in root.iterdir()} == expected, "Unexpected files in signing handoff")
    checksums = root / CHECKSUMS
    with checksums.open("x", encoding="utf-8", newline="\n") as output:
        for name, asset in sorted(assets.items()):
            output.write(f"{asset.sha256}  {name}\n")
    assets[CHECKSUMS] = Asset.from_file(checksums)
    return Bundle(
        root,
        tag,
        source,
        manifest_sha,
        targets,
        assets,
        reports,
        server_reports,
        candidate["server_source_sha"],
        candidate_asset.sha256,
    )


def validate_provisioning(bundle: Bundle, evidence_sha: str, release_id: int) -> dict[str, str]:
    path = bundle.root / PROVISIONING
    matching(evidence_sha, SHA, "provisioning evidence SHA-256")
    require(Asset.from_file(path).sha256 == evidence_sha, "Provisioning evidence differs from the approved digest")
    evidence = read_json(path)
    require(
        evidence["format_version"] == 1 and evidence["kind"] == "downloaded-desktop-provisioning",
        "Unsupported provisioning evidence",
    )
    require(
        (evidence["release_id"], evidence["release_manifest_sha256"]) == (release_id, bundle.manifest_sha256),
        "Provisioning evidence is for a different prerelease",
    )
    require(evidence["targets"].keys() == bundle.targets.keys(), "Missing downloaded provisioning evidence")
    reports = {}
    for target, assets in bundle.targets.items():
        record = evidence["targets"][target]
        require(
            record["download_url"] == asset_url(bundle.tag, assets["desktop"].name)
            and record["asset_sha256"] == {role: asset.sha256 for role, asset in assets.items()},
            f"{target}: downloaded provisioning evidence does not match published assets",
        )
        require(record["clean_machine_provisioning"] == "passed", f"{target}: downloaded provisioning must pass")
        reports[target] = matching(record["report"], REPORT, "downloaded provisioning report URL")
    return reports
