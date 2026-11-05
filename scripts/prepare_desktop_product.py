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

"""Prepare a product's pinned Ops bundle and downloadable Server catalog before packaging."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit

from desktop_candidate import PRODUCT_ID, PRODUCT_NAME, ROOT, digest, directory, json_bytes, read, require, version
from prepare_managed_resources import inventory
from produce_managed_resources import measure_windows_paths

from powercontext.ops.resources import MAX_FILES, MAX_UNPACKED_BYTES, ResourceSpec


def prepare(
    resources: Path,
    candidate_sha256: str,
    output: Path,
    desktop_version: str,
    server_url: str,
    origins: list[str],
    qualification: str,
) -> None:
    """Read immutable producer inputs; publish a new staging directory only after verification."""
    require(version(desktop_version), "expected Desktop X.Y.Z version")
    directory(resources)
    require(
        not output.resolve().is_relative_to(resources.resolve())
        and not resources.resolve().is_relative_to(output.resolve()),
        "resources and output must not contain one another",
    )
    require(digest(resources / "candidate.json") == candidate_sha256, "producer candidate digest mismatch")
    candidate = read(resources / "candidate.json")
    require(candidate["format_version"] == 1, "unsupported producer candidate")
    binding = candidate["binding"]
    catalog_path = resources / "server/catalog.json"
    require(digest(catalog_path) == candidate["catalog_sha256"], "producer catalog digest mismatch")
    catalog = read(catalog_path)
    require(catalog["format_version"] == 1 and len(catalog["resources"]) == 1, "expected one Server resource")
    spec = ResourceSpec.model_validate(catalog["resources"][0])
    require(spec.server_version == binding["server_version"], "Server version mismatch")
    require(
        spec.archive_sha256 == binding["server_archive_sha256"] == digest(resources / "server/server.tar.gz"),
        "Server archive digest mismatch",
    )
    require((resources / "server/server.tar.gz").stat().st_size == spec.archive_size, "Server archive size mismatch")
    require(digest(resources / "ops/bundle.json") == binding["ops_bundle_manifest_sha256"], "Ops manifest mismatch")
    directory(resources / "ops")
    files = inventory(resources / "ops")
    manifest = read(resources / "ops/bundle.json")
    require(
        manifest["format_version"] == 1 and manifest["arguments"] == ["-I", "-S", "-B", "entry.py"],
        "unsupported Ops launcher",
    )
    require(
        manifest["platform"] == {"windows": "win32", "macos": "darwin"}[spec.platform]
        and manifest["architecture"].lower()
        in ({"arm64", "aarch64"} if spec.architecture == "arm64" else {"x86_64", "amd64"}),
        "Ops and Server target mismatch",
    )
    require(
        set(manifest)
        == {
            "format_version",
            "python_version",
            "platform",
            "architecture",
            "python_relative",
            "arguments",
            "requirements_sha256",
            "installed_bytes",
            "files_sha256",
        }
        and isinstance(manifest["python_version"], str)
        and 0 < len(manifest["python_version"]) <= 128
        and re.fullmatch(r"[0-9a-f]{64}", manifest["requirements_sha256"]) is not None
        and (resources / "ops/bundle.json").stat().st_size <= 16 * 1024 * 1024,
        "unsupported Ops manifest",
    )
    content = [f for f in files if f["path"] != "bundle.json"]
    require(
        0 < len(content) <= MAX_FILES and sum(f["size"] for f in content) <= MAX_UNPACKED_BYTES,
        "Ops bundle exceeds native limits",
    )
    require(
        {f["path"]: f["sha256"] for f in content} == manifest["files_sha256"]
        and sum(f["size"] for f in content) == manifest["installed_bytes"],
        "Ops contents mismatch",
    )
    require(
        manifest["python_relative"].startswith("runtime/")
        and any(f["path"] == manifest["python_relative"] and f["executable"] for f in content)
        and "entry.py" in manifest["files_sha256"],
        "missing executable Ops interpreter or entry",
    )
    if os.name == "nt":
        ops_root = PureWindowsPath(os.environ["LOCALAPPDATA"]) / "PowerContext/Ops/bundles"
        measure_windows_paths(ops_root / binding["ops_bundle_manifest_sha256"], files)
        measure_windows_paths(ops_root / ("prepare-" + "0" * 6), files)
    operations = read(ROOT / "desktop/src-tauri/src/transport/operations.json")
    profiles = read(ROOT / "desktop/src-tauri/src/connections/compatibility.json")
    matches = [
        p
        for p in profiles
        if p["id"] == qualification
        and not p["id"].startswith("ci-")
        and p["serverCommit"] == binding["source_commit"]
        and p["artifactSha256"] == binding["server_wheel_sha256"]
        and p["contractSha256"] == operations["contractSha256"]
        and set(operations["operations"]).issubset(p["operations"])
    ]
    require(len(matches) == 1, "no exact bundled product qualification for this Server wheel and operation set")
    require(
        "git+https://github.com/oceanbase/powercontext@" + binding["source_commit"] in spec.source_refs,
        "Server source identity missing",
    )
    host = urlsplit(server_url).hostname or ""
    require(
        bool(host)
        and not host.endswith((".invalid", ".localhost", ".test", ".example"))
        and host != "localhost"
        and not urlsplit(server_url).query,
        "expected durable distribution URL",
    )
    updated = spec.model_dump(mode="json")
    updated.update(url=server_url, allowed_origins=origins, qualification_ref=qualification)
    product = ResourceSpec.model_validate(updated)
    require(not output.exists() and not output.is_symlink(), "output already exists; use a fresh staging directory")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".desktop-product-", dir=output.parent) as temporary:
        stage = Path(temporary) / "product"
        stage.mkdir()
        shutil.copytree(resources / "ops", stage / "ops")
        require(inventory(stage / "ops") == files, "copied Ops contents changed")
        (stage / "server").mkdir()
        (stage / "server/catalog.json").write_bytes(
            json_bytes({"format_version": 1, "resources": [product.model_dump(mode="json")]})
        )
        provision = {
            "format_version": 1,
            "binding_mode": "managed_catalog",
            "bundle_manifest_sha256": binding["ops_bundle_manifest_sha256"],
            "catalog_sha256": digest(stage / "server/catalog.json"),
        }
        (stage / "ops-provision.json").write_bytes(json_bytes(provision))
        # Explicit paths preserve all inventoried files, including dotfiles. No renderer inputs select these paths.
        mappings = {str(output.resolve() / "ops" / f["path"]): "ops/" + f["path"] for f in files}
        mappings.update({str(output.resolve() / p): p for p in ("ops-provision.json", "server/catalog.json")})
        (stage / "tauri.product.conf.json").write_bytes(
            json_bytes({
                "productName": PRODUCT_NAME,
                "identifier": PRODUCT_ID,
                "version": desktop_version,
                "bundle": {"shortDescription": "PowerContext Desktop", "resources": mappings},
            })
        )
        (stage / "product-resources.json").write_bytes(
            json_bytes({
                "format_version": 1,
                "producer_candidate_sha256": candidate_sha256,
                "server_archive_sha256": spec.archive_sha256,
                "source_commit": binding["source_commit"],
                "server_wheel_sha256": binding["server_wheel_sha256"],
                "qualification": matches[0],
                "catalog_sha256": provision["catalog_sha256"],
                "ops_bundle_manifest_sha256": provision["bundle_manifest_sha256"],
                "signing": "not_performed",
                "installed_acceptance": "not_performed",
            })
        )
        stage.rename(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resources", type=Path, required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--server-url", required=True)
    parser.add_argument("--allowed-origin", action="append", required=True)
    parser.add_argument("--qualification", required=True)
    args = parser.parse_args()
    try:
        prepare(
            args.resources,
            args.candidate_sha256,
            args.output,
            args.version,
            args.server_url,
            args.allowed_origin,
            args.qualification,
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Product resources rejected: {error}\n")


if __name__ == "__main__":
    main()
