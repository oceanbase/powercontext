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

"""Produce unqualified managed Server and independent Ops candidates offline.

This does not publish, sign, notarize or qualify a release. Hashes describe the
unsigned candidate bytes; qualified catalogs must be frozen after actual signing.
"""

# ruff: noqa: TRY003, S603 - explicit release-producer inputs and actionable failures

from __future__ import annotations

import argparse
import csv
import email.parser
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path, PureWindowsPath
from typing import Any
from urllib.parse import urlsplit

import build_intel_managed_wheel
import build_ops_bundle
from packaging.utils import parse_wheel_filename
from prepare_managed_resources import (
    _BUILD_TOOLS,
    _INTERPRETERS,
    environment,
    extract_runtime,
    inventory,
    json_bytes,
    producer_identity,
    sha256,
    target_spec,
)

from powercontext.ops.installer import ManagedLayout
from powercontext.ops.protocol import PROTOCOL_VERSION
from powercontext.ops.resources import ResourceFile, ResourceSpec, validate_destination

_PROBE = """
import importlib.metadata, json, platform, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
if not sys.flags.isolated or not sys.dont_write_bytecode:
    raise RuntimeError("The managed interpreter must use -I -B")
import fastapi, fastmcp, sqlalchemy, sqlite_vec
import powercontext.server.cli, powercontext.service.launcher, powercontext.cli.app
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.backends.openssl.backend import backend
key = AESGCM.generate_key(bit_length=128)
aead = AESGCM(key)
ciphertext = aead.encrypt(bytes(12), b"candidate integrity check", b"candidate")
if aead.decrypt(bytes(12), ciphertext, b"candidate") != b"candidate integrity check":
    raise RuntimeError("Bundled cryptography AES-GCM failed")
private = Ed25519PrivateKey.generate()
private.public_key().verify(private.sign(b"candidate"), b"candidate")
outside = [str(Path(m.__file__).resolve()) for m in list(sys.modules.values())
           if getattr(m, "__file__", None) and not m.__file__.startswith("<")
           and not Path(m.__file__).resolve().is_relative_to(root)]
if outside or any(not Path(p).resolve().is_relative_to(root) for p in sys.path):
    raise RuntimeError("Managed Server imports or paths escaped its artifact: " + repr(outside))
print(json.dumps(dict(python_version=platform.python_version(),
                     server_version=importlib.metadata.version("powercontext"),
                     platform=sys.platform, architecture=platform.machine(), outside=outside,
                     cryptography_version=importlib.metadata.version("cryptography"),
                     openssl_version=backend.openssl_version_text(), crypto_self_check="passed")))
"""


def verify_inputs(root: Path, expected_sha256: str) -> dict[str, Any]:
    if sha256(root / "inputs.json") != expected_sha256:
        raise ValueError("Prepared inputs do not match the caller-pinned digest")
    metadata = json.loads((root / "inputs.json").read_bytes())
    spec = target_spec(metadata["target"])
    if (
        metadata["format_version"] != 1
        or metadata["interpreter"] != spec
        or metadata["interpreter_lock_sha256"] != sha256(_INTERPRETERS)
        or metadata["build_tools"]["lock_sha256"] != sha256(_BUILD_TOOLS)
        or metadata["intel_build_pins_sha256"] != sha256(build_intel_managed_wheel.PINS)
        or sha256(root / "python.tar.gz") != spec["sha256"]
        or sha256(root / "source.tar") != metadata["source"]["archive_sha256"]
        or sha256(root / "requirements.lock") != metadata["requirements_sha256"]
        or sha256(root / "upstream-requirements.lock") != metadata["upstream_requirements_sha256"]
    ):
        raise ValueError("Prepared source, interpreter or requirement identity is inconsistent")
    found = {path.name for path in (root / "wheels").iterdir()}
    expected = {item["filename"] for item in metadata["wheels"]}
    if found != expected or len(expected) != len(metadata["wheels"]):
        raise ValueError("Wheelhouse must contain exactly the frozen wheel closure")
    for item in metadata["wheels"]:
        path = root / "wheels" / item["filename"]
        if (
            path.name != item["filename"]
            or path.is_symlink()
            or sha256(path) != item["sha256"]
            or path.stat().st_size != item["size"]
        ):
            raise ValueError("A frozen wheel has changed")
        name, version, _build, _tags = parse_wheel_filename(path.name)
        if (name, str(version)) != (item["name"], item["version"]):
            raise ValueError("Frozen wheel identity does not match its filename")
    project = [item for item in metadata["wheels"] if item["name"] == "powercontext"]
    if len(project) != 1 or project[0]["version"] != metadata["server_version"]:
        raise ValueError("Frozen wheel closure does not match the requested Server version")
    requirements = "".join(
        f"{p['name']}{'[ops,cli]' if p['name'] == 'powercontext' else ''}=={p['version']} --hash=sha256:{p['sha256']}\n"
        for p in metadata["wheels"]
    ).encode("utf-8")
    if (root / "requirements.lock").read_bytes() != requirements:
        raise ValueError("Requirements must describe exactly the frozen wheel closure and Ops/CLI extras")
    return metadata


def deterministic_archive(root: Path, output: Path, files: list[dict[str, Any]]) -> None:
    """Stable ordering, permissions, owner, timestamp, gzip header and file bytes."""
    with (
        output.open("xb") as raw,
        gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive,
    ):
        for file in files:
            member = tarfile.TarInfo(file["path"])
            member.size = file["size"]
            member.mode = 0o755 if file["executable"] else 0o644
            with (root / file["path"]).open("rb") as source:
                archive.addfile(member, source)


def _remove_generated_wrappers(packages: Path) -> None:
    """The managed profile launches modules, not installer-generated console wrappers.

    uv's generated shebangs/Windows launchers bind the build path. Remove only
    those wrapper directories and repair their RECORD rows before freezing bytes.
    uv cache records contain mutable build-input timestamps, so they also
    stay out of the sealed runtime. Importable modules and wheel data remain.
    """
    for name in ("bin", "Scripts"):
        scripts = packages / name
        if scripts.exists():
            shutil.rmtree(scripts)
    for cache in packages.glob("*.dist-info/uv_cache.json"):
        cache.unlink()
    for record in packages.glob("*.dist-info/RECORD"):
        with record.open(newline="", encoding="utf-8") as stream:
            rows = [row for row in csv.reader(stream) if (packages / row[0]).is_file()]
        with record.open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream, lineterminator="\n").writerows(sorted(rows))


def _licenses(inputs: Path, runtime: Path, metadata: dict[str, Any]) -> dict[str, Any]:
    packages = []
    for item in metadata["wheels"]:
        with zipfile.ZipFile(inputs / "wheels" / item["filename"]) as wheel:
            path = next(name for name in wheel.namelist() if name.endswith(".dist-info/METADATA"))
            message = email.parser.Parser().parsestr(wheel.read(path).decode("utf-8"))
            notices = [
                name
                for name in wheel.namelist()
                if any(part.lower().startswith(("license", "copying", "notice")) for part in Path(name).parts)
            ]
            packages.append({
                "name": item["name"],
                "version": item["version"],
                "wheel_sha256": item["sha256"],
                "license_expression": message.get("License-Expression"),
                "license_text": message.get("License"),
                "classifiers": message.get_all("Classifier", []),
                "notices": [
                    {"wheel_path": name, "sha256": hashlib.sha256(wheel.read(name)).hexdigest()}
                    for name in sorted(notices)
                    if not name.endswith("/")
                ],
            })
    runtime_notices = [
        file
        for file in inventory(runtime)
        if any(part.lower().startswith(("license", "copying", "notice")) for part in Path(file["path"]).parts)
    ]
    if not runtime_notices:
        raise ValueError("The redistributable interpreter must retain its in-tree license notices")
    lock = json.loads(_INTERPRETERS.read_bytes())
    return {
        "format_version": 1,
        "runtime_license": lock["license"],
        "runtime_notices": runtime_notices,
        "packages": packages,
    }


def _server_probe(root: Path, spec: dict[str, Any], version: str, environment_values: dict[str, str]) -> None:
    python = root / spec["python_relative"]
    result = subprocess.run(
        [str(python), "-I", "-B", "-c", _PROBE, str(root)],
        cwd=root,
        env=environment_values,
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    identity = json.loads(result.stdout)
    if identity["python_version"] != spec["python_version"] or identity["server_version"] != version:
        raise ValueError("Installed Server or interpreter version does not match the frozen input")
    architecture = {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(
        identity["architecture"].lower()
    )
    if (
        identity["platform"] != {"macos": "darwin", "windows": "win32"}[spec["platform"]]
        or architecture != spec["architecture"]
    ):
        raise ValueError("Installed runtime platform does not match its resource identity")
    for relative in dict.fromkeys((spec["python_relative"], spec["server_python_relative"])):
        subprocess.run(
            [str(root / relative), "-I", "-B", "-m", "powercontext.service.launcher", "--help"],
            cwd=root,
            env=environment_values,
            check=True,
            capture_output=True,
            timeout=90,
        )


def measure_windows_paths(root: PureWindowsPath, files: list[dict[str, Any]]) -> dict[str, Any]:
    """Measure ordinary Win32 file/directory limits, including UTF-16 units."""
    units = lambda value: len(str(value).encode("utf-16-le")) // 2
    if not root.is_absolute() or str(root).startswith("\\\\?\\"):
        raise ValueError("Windows candidate paths must use an ordinary absolute root")
    directories = {root}
    paths = [root / file["path"] for file in files]
    for path in paths:
        directories.update(path.parents)
        if units(path) > 259 or any(units(part) > 255 for part in path.parts):
            raise ValueError("Candidate does not fit the actual Windows default/staging path budget")
    if any(units(path) > 247 for path in directories):
        raise ValueError("Candidate does not fit the actual Windows directory creation budget")
    return {"root": str(root), "longest_file_utf16_units": max(map(units, paths))}


def _windows_path_budget(
    files: list[dict[str, Any]], resource_id: str, ops_files: list[dict[str, Any]]
) -> dict[str, Any]:
    if os.name != "nt":
        return {"status": "not_executed", "reason": "requires native Windows paths"}
    layout = ManagedLayout.current_user()
    # Reuse the same public path validator as actual acquisition and preflight.
    # The spec is constructed by the caller; this helper reports the measured roots.
    roots = {
        "server_final": layout.programs / "server" / resource_id,
        "server_staging": layout.programs / "staging" / ("resource-" + "0" * 32),
        "ops_final": Path(os.environ["LOCALAPPDATA"]) / "PowerContext/Ops/bundle",
    }
    lengths = {}
    for name, root in roots.items():
        chosen = ops_files if name == "ops_final" else files
        lengths[name] = measure_windows_paths(PureWindowsPath(str(root)), chosen)
    return {"status": "passed", "measurements": lengths}


def produce(args: argparse.Namespace) -> None:
    inputs = args.inputs.resolve()
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Output must not exist; candidate production never overwrites an artifact")
    if output.is_relative_to(inputs) or inputs.is_relative_to(output):
        raise ValueError("Inputs and output must not contain one another")
    metadata = verify_inputs(inputs, args.inputs_sha256)
    spec = metadata["interpreter"]
    parsed = urlsplit(args.server_url)
    origin = f"https://{parsed.netloc}"
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("A fixed candidate HTTPS archive URL is required; no release URL is inferred")
    uv = shutil.which(args.uv)
    if uv is None:
        raise ValueError("The build host needs uv for offline wheel installation")
    output.mkdir(parents=True)
    env = environment()
    with tempfile.TemporaryDirectory(prefix="pc-candidate-") as temporary:
        work = Path(temporary).resolve()
        runtime = work / "python"
        aliases = extract_runtime(inputs / "python.tar.gz", runtime)
        if aliases != metadata["normalized_runtime_aliases"]:
            raise ValueError("Runtime alias normalization differs from the prepared input")
        licenses = _licenses(inputs, runtime, metadata)
        server = work / "server"
        shutil.copytree(runtime, server)
        site = server / spec["site_relative"]
        subprocess.run(
            [
                uv,
                "--offline",
                "pip",
                "install",
                "--python",
                str(server / spec["python_relative"]),
                "--target",
                str(site),
                "--link-mode",
                "copy",
                "--no-index",
                "--find-links",
                str(inputs / "wheels"),
                "--only-binary",
                ":all:",
                "--require-hashes",
                "-r",
                str(inputs / "requirements.lock"),
            ],
            cwd=work,
            env=env,
            check=True,
            timeout=600,
        )
        _remove_generated_wrappers(site)
        (server / "resource-licenses.json").write_bytes(json_bytes(licenses))
        # Move once before execution to test relocation away from installer paths.
        relocated = work / "relocated server"
        server.rename(relocated)
        poison = work / "poison"
        poison.mkdir()
        (poison / "powercontext.py").write_text("raise RuntimeError('developer import leaked')\n", encoding="utf-8")
        isolated = {**env, "PATH": "", "PYTHONPATH": str(poison), "PYTHONHOME": str(poison / "missing")}
        _server_probe(relocated, spec, metadata["server_version"], isolated)
        ops = output / "ops"
        build_ops_bundle.build(
            argparse.Namespace(
                runtime=runtime,
                python_relative=spec["python_relative"],
                wheelhouse=inputs / "wheels",
                requirements=inputs / "requirements.lock",
                output=ops,
                uv=uv,
            )
        )
        _remove_generated_wrappers(ops / "packages")
        (ops / "resource-licenses.json").write_bytes(json_bytes(licenses))
        manifest = json.loads((ops / "bundle.json").read_bytes())
        (ops / "bundle.json").unlink()
        ops_content = inventory(ops)
        manifest.update(
            installed_bytes=sum(file["size"] for file in ops_content),
            files_sha256={file["path"]: file["sha256"] for file in ops_content},
        )
        (ops / "bundle.json").write_bytes(json_bytes(manifest))
        ops_files = inventory(ops)
        deterministic_archive(ops, output / "ops.tar.gz", ops_files)
        ops_archive_digest = sha256(output / "ops.tar.gz")
        files = inventory(relocated)
        server_output = output / "server"
        server_output.mkdir()
        archive = server_output / "server.tar.gz"
        deterministic_archive(relocated, archive, files)
        archive_digest = sha256(archive)
        resource_id = "pc-" + archive_digest[:24]
        binding = {
            "inputs_sha256": args.inputs_sha256,
            "server_archive_sha256": archive_digest,
            "ops_bundle_manifest_sha256": sha256(ops / "bundle.json"),
            "ops_archive_sha256": ops_archive_digest,
            "server_wheel_sha256": next(
                item["sha256"] for item in metadata["wheels"] if item["name"] == "powercontext"
            ),
            "source_commit": metadata["source"]["commit"],
            "server_version": metadata["server_version"],
            "python_source_sha256": spec["sha256"],
            "producer": producer_identity(),
        }
        candidate_digest = hashlib.sha256(json_bytes(binding)).hexdigest()
        license_digest = hashlib.sha256(json_bytes(licenses)).hexdigest()
        resource = ResourceSpec(
            resource_id=resource_id,
            server_version=metadata["server_version"],
            python_version=spec["python_version"],
            platform=spec["platform"],
            architecture=spec["architecture"],
            archive_format="tar.gz",
            url=args.server_url,
            allowed_origins=(origin,),
            archive_sha256=archive_digest,
            archive_size=archive.stat().st_size,
            unpacked_size=sum(file["size"] for file in files),
            python_relative=spec["server_python_relative"],
            ops_protocol_min=PROTOCOL_VERSION,
            ops_protocol_max=PROTOCOL_VERSION,
            source_refs=(
                f"git+{metadata['source']['repository']}@{metadata['source']['commit']}",
                spec["url"],
                "inputs-sha256:" + args.inputs_sha256,
            ),
            license_refs=("resource-licenses.json#sha256=" + license_digest,),
            qualification_ref="unqualified-candidate:sha256:" + candidate_digest,
            files=tuple(ResourceFile.model_validate(file) for file in files),
        )
        if os.name == "nt":
            layout = ManagedLayout.current_user()
            validate_destination(resource, layout.programs / "server" / resource_id)
            validate_destination(resource, layout.programs / "staging" / ("resource-" + "0" * 32))
        path_budget = _windows_path_budget(files, resource_id, ops_files)
        catalog = server_output / "catalog.json"
        catalog.write_bytes(json_bytes({"format_version": 1, "resources": [resource.model_dump(mode="json")]}))
        catalog_digest = sha256(catalog)
        (server_output / "catalog.sha256").write_text(catalog_digest + "\n", encoding="utf-8")
        (server_output / "resource.json").write_bytes(
            json_bytes({
                "format_version": 1,
                "status": "unqualified_candidate",
                "resource": resource.model_dump(mode="json"),
                "inputs": metadata,
                "inputs_sha256": args.inputs_sha256,
            })
        )
        (output / "ops-resource.json").write_bytes(
            json_bytes({
                "format_version": 1,
                "status": "unqualified_candidate",
                "files": ops_files,
                "inputs_sha256": args.inputs_sha256,
                "archive_sha256": ops_archive_digest,
                "archive_size": (output / "ops.tar.gz").stat().st_size,
            })
        )
        (output / "ops-provision.json").write_bytes(
            json_bytes({
                "format_version": 1,
                "bundle_manifest_sha256": sha256(ops / "bundle.json"),
                "binding_mode": "managed_catalog",
                "catalog_sha256": catalog_digest,
            })
        )
        (output / "candidate.json").write_bytes(
            json_bytes({
                "format_version": 1,
                "status": "unqualified_candidate",
                "qualification_ref": resource.qualification_ref,
                "binding": binding,
                "catalog_sha256": catalog_digest,
                "ops_archive_sha256": ops_archive_digest,
                "windows_path_budget": path_budget,
                "signing": "not_performed",
                "notarization": "not_performed",
                "clean_machine_qualification": "not_performed",
            })
        )
        print(f"Produced unqualified {metadata['target']} candidate: {output}; catalog_sha256={catalog_digest}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument(
        "--inputs-sha256", required=True, help="Expected exact inputs.json SHA-256 from trusted preparation"
    )
    parser.add_argument(
        "--server-url", required=True, help="Fixed candidate URL supplied by the operator; never inferred"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uv", default="uv")
    produce(parser.parse_args())


if __name__ == "__main__":
    main()
