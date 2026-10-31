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

"""Freeze native candidate inputs from an exact Git source and the checked-in locks.

Network acquisition and the explicitly pinned source builds happen here.
The separate producer consumes only the resulting hashed, offline wheel closure.
"""

# ruff: noqa: TRY003, S603, S607 - explicit release-producer inputs and fixed build-host tools

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import posixpath
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

import build_intel_managed_wheel
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name, parse_wheel_filename
from packaging.version import Version

from powercontext.ops.resources import MAX_FILES, MAX_UNPACKED_BYTES, ResourceFile

_SCRIPTS = Path(__file__).resolve().parent
_INTERPRETERS = _SCRIPTS / "managed_resource_interpreters.json"
_BUILD_TOOLS = _SCRIPTS / "managed_resource_build_tools.json"
_SOURCE_REPOSITORY = "https://github.com/oceanbase/powercontext"


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def producer_identity() -> dict[str, Any]:
    """Bind executable producer sources separately from the packaged Server source."""
    names = (
        "prepare_managed_resources.py",
        "produce_managed_resources.py",
        "build_ops_bundle.py",
        "managed_resource_interpreters.json",
        "managed_resource_build_tools.json",
        "build_intel_managed_wheel.py",
        "managed_resource_intel_build.json",
    )
    files = {"scripts/" + name: sha256(_SCRIPTS / name) for name in names}
    revision = subprocess.check_output(["git", "-C", str(_SCRIPTS.parent), "rev-parse", "HEAD"], text=True).strip()
    matches = True
    for relative, digest in files.items():
        committed = subprocess.run(
            ["git", "-C", str(_SCRIPTS.parent), "show", f"{revision}:{relative}"], capture_output=True, check=False
        )
        matches = matches and committed.returncode == 0 and hashlib.sha256(committed.stdout).hexdigest() == digest
    return {"git_commit": revision, "files_sha256": files, "matches_committed_files": matches}


def environment() -> dict[str, str]:
    return {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("PYTHON", "PIP_", "UV_", "POWERCONTEXT", "SETUPTOOLS_SCM", "HATCH_"))
    }


def target_spec(target: str, *, native: bool = True) -> dict[str, Any]:
    lock = json.loads(_INTERPRETERS.read_bytes())
    spec = lock["targets"][target]
    current = (
        {"Darwin": "macos", "Windows": "windows"}.get(platform.system()),
        {"amd64": "x86_64", "x86_64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower()),
    )
    if native and current != (spec["platform"], spec["architecture"]):
        raise ValueError("Candidate production requires the matching native OS and architecture")
    return spec


def acquire(item: dict[str, Any], destination: Path, cache: Path | None = None) -> None:
    """Download fixed HTTPS bytes; incomplete and corrupt files cannot be reused."""
    if destination.exists():
        if sha256(destination) != item["sha256"]:
            raise ValueError("Existing input has the wrong digest; inspect it before retrying")
        return
    if cache is not None:
        cached = cache.resolve() / item["sha256"]
        acquire(item, cached)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(cached, destination)
        return
    parsed = urlsplit(item["url"])
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Inputs must have fixed HTTPS source URLs")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    if partial.exists():
        raise ValueError("An incomplete input exists; inspect it before retrying")
    subprocess.run(
        [
            "curl",
            "--fail",
            "--silent",
            "--show-error",
            "--location",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--retry",
            "2",
            "--max-time",
            "1800",
            "--output",
            str(partial),
            item["url"],
        ],
        check=True,
        env=environment(),
        timeout=1900,
    )
    if sha256(partial) != item["sha256"] or ("size" in item and partial.stat().st_size != item["size"]):
        raise ValueError("Downloaded input does not match its locked digest and size")
    partial.rename(destination)


def extract_runtime(archive: Path, output: Path) -> list[dict[str, str]]:  # noqa: C901 - one archive validation boundary
    """Materialize regular files and safe internal file aliases, never archive links.

    ResourceStore deliberately rejects links. Directory aliases, absolute/external
    aliases, devices and ambiguous paths are rejected before any output is made.
    """
    if output.exists():
        raise ValueError("Runtime output must not exist")
    aliases: list[dict[str, str]] = []
    with tarfile.open(archive, "r:gz") as source:
        members: dict[str, tarfile.TarInfo] = {}
        casefolded: set[str] = set()
        total = 0
        for member in source:
            name = member.name.rstrip("/")
            if name == "python" and member.isdir():
                continue
            if not name.startswith("python/"):
                raise ValueError("Interpreter archive must contain only the python/ install tree")
            relative = name.removeprefix("python/")
            ResourceFile(path=relative, size=member.size if member.isfile() else 0, sha256="0" * 64)
            if relative.casefold() in casefolded or not (
                member.isfile() or member.isdir() or member.issym() or member.islnk()
            ):
                raise ValueError("Interpreter archive has duplicate paths or unsupported file types")
            members[relative] = member
            casefolded.add(relative.casefold())
            total += member.size
            if len(members) > MAX_FILES or total > MAX_UNPACKED_BYTES:
                raise ValueError("Interpreter archive exceeds resource limits")

        def regular(name: str, seen: frozenset[str] = frozenset()) -> tarfile.TarInfo:
            member = members.get(name)
            if member is None or name in seen or member.isdir():
                raise ValueError("Runtime aliases must resolve to an internal regular file")
            if member.isfile():
                return member
            link = member.linkname
            if not link or link.startswith("/") or "\\" in link or ":" in link:
                raise ValueError("Runtime aliases must be relative and internal")
            resolved = posixpath.normpath(
                posixpath.join("python", posixpath.dirname(name), link) if member.issym() else link
            )
            if not resolved.startswith("python/"):
                raise ValueError("Runtime alias escapes the install tree")
            return regular(resolved.removeprefix("python/"), seen | {name})

        files = [(name, regular(name)) for name, member in members.items() if not member.isdir()]
        for name, member in files:
            for parent in PurePosixPath(name).parents:
                if str(parent) in members and not members[str(parent)].isdir():
                    raise ValueError("Runtime file shadows a directory")
            if not members[name].isfile():
                aliases.append({"path": name, "source": member.name.removeprefix("python/")})
        if sum(member.size for _name, member in files) > MAX_UNPACKED_BYTES:
            raise ValueError("Materialized runtime exceeds resource limits")
        output.mkdir(parents=True)
        for name, member in files:
            destination = output / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            stream = source.extractfile(member)
            if stream is None:
                raise ValueError("Runtime member has no file content")
            with stream, destination.open("xb") as written:
                shutil.copyfileobj(stream, written)
            executable = bool(member.mode & 0o111) or destination.suffix.lower() == ".exe"
            destination.chmod(0o755 if executable else 0o644)
    return sorted(aliases, key=lambda item: item["path"])


def inventory(root: Path) -> list[dict[str, Any]]:
    files = []
    for path in sorted(root.rglob("*")):
        status = path.lstat()
        if path.is_symlink() or getattr(status, "st_file_attributes", 0) & 0x400:
            raise ValueError("Candidate trees must contain no symlinks or reparse points")
        if stat.S_ISDIR(status.st_mode):
            continue
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            raise ValueError("Candidate inventory accepts only independent regular files")
        files.append({
            "path": path.relative_to(root).as_posix(),
            "size": status.st_size,
            "sha256": sha256(path),
            "executable": bool(status.st_mode & stat.S_IXUSR) or (os.name == "nt" and path.suffix.lower() == ".exe"),
        })
    return files


def _wheel_identity(path: Path) -> dict[str, Any]:
    name, version, _build, _tags = parse_wheel_filename(path.name)
    return {
        "name": name,
        "version": str(version),
        "filename": path.name,
        "sha256": sha256(path),
        "size": path.stat().st_size,
    }


def _locked_dependencies(  # noqa: C901 - exact requirement and platform-artifact selection
    export: str, lock: dict[str, Any], markers: dict[str, str], tags: list[str], target: str | None = None
) -> list[dict[str, Any]]:
    records = []
    ranks = {tag: index for index, tag in enumerate(tags)}
    for line in export.replace("\\\n", " ").splitlines():
        line = line.partition("#")[0].strip()
        if not line:
            continue
        requirement = Requirement(line.split(" --hash=")[0].strip())
        if requirement.marker and not requirement.marker.evaluate(markers):
            continue
        versions = list(requirement.specifier)
        if requirement.url or len(versions) != 1 or versions[0].operator != "==" or "*" in versions[0].version:
            raise ValueError("Exported runtime dependencies must have exact registry versions")
        name, version = canonicalize_name(requirement.name), versions[0].version
        package = next(p for p in lock["package"] if p["name"] == name and p["version"] == version)
        candidates = []
        for wheel in package.get("wheels", []):
            filename = unquote(PurePosixPath(urlsplit(wheel["url"]).path).name)
            _name, _version, _build, wheel_tags = parse_wheel_filename(filename)
            rank = min((ranks[str(tag)] for tag in wheel_tags if str(tag) in ranks), default=None)
            if rank is not None:
                candidates.append((rank, filename, wheel))
        if candidates:
            _rank, filename, artifact = min(candidates)
            kind = "wheel"
        elif (name, version) == ("jieba", "0.42.1"):
            artifact = package["sdist"]
            filename = "jieba-0.42.1.tar.gz"
            kind = "sdist"
        elif target == "macos-x86_64" and (name, version) == ("cryptography", "50.0.0"):
            artifact = package["sdist"]
            filename = "cryptography-50.0.0.tar.gz"
            kind = "intel-cryptography-sdist"
        else:
            raise ValueError(
                f"No locked compatible wheel for {name}=={version}; source builds are not implicitly allowed"
            )
        digest = artifact["hash"].removeprefix("sha256:")
        if f"--hash=sha256:{digest}" not in line:
            raise ValueError("Selected artifact is not covered by the exported requirement hashes")
        records.append({
            "name": name,
            "version": version,
            "kind": kind,
            "filename": filename,
            "url": artifact["url"],
            "sha256": digest,
            "size": artifact["size"],
        })
    return sorted(records, key=lambda item: item["name"])


def prepare(args: argparse.Namespace) -> None:  # noqa: C901 - ordered acquisition and source-build stages
    spec = target_spec(args.target)
    if re.fullmatch(r"[0-9a-f]{40}", args.source_ref) is None:
        raise ValueError("Source ref must be an exact 40-character Git commit")
    if str(Version(args.server_version)) != args.server_version:
        raise ValueError("Server version must be an exact canonical Python package version")
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Input output must not exist; inspect partial work before retrying")
    source_repo = args.source.resolve()
    commit = subprocess.check_output(
        ["git", "-C", str(source_repo), "rev-parse", args.source_ref + "^{commit}"], text=True
    ).strip()
    if commit != args.source_ref:
        raise ValueError("Source commit does not match the requested source")
    output.mkdir(parents=True)
    if args.runtime_archive is None:
        acquire(spec, output / "python.tar.gz", args.download_cache)
    else:
        if sha256(args.runtime_archive) != spec["sha256"]:
            raise ValueError("Supplied runtime archive does not match the checked-in interpreter pin")
        shutil.copyfile(args.runtime_archive, output / "python.tar.gz")
    with tempfile.TemporaryDirectory(prefix="pc-resource-prepare-") as temporary:
        work = Path(temporary).resolve()
        runtime = work / "runtime"
        aliases = extract_runtime(output / "python.tar.gz", runtime)
        build_lock = json.loads(_BUILD_TOOLS.read_bytes())
        tooling = work / "tooling"
        tooling.mkdir()
        for item in build_lock["packages"]:
            acquire(item, tooling / item["filename"], args.download_cache)
        tool_requirements = tooling / "requirements.lock"
        tool_requirements.write_text(
            "".join(f"{p['name']}=={p['version']} --hash=sha256:{p['sha256']}\n" for p in build_lock["packages"]),
            encoding="utf-8",
        )
        env = environment()
        uv = shutil.which(args.uv)
        if uv is None:
            raise ValueError("The build host needs uv")
        subprocess.run(
            [uv, "venv", "--python", str(runtime / spec["python_relative"]), str(work / "build-env")],
            check=True,
            env=env,
        )
        builder = work / "build-env" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run(
            [
                uv,
                "--offline",
                "pip",
                "install",
                "--python",
                str(builder),
                "--no-index",
                "--find-links",
                str(tooling),
                "--require-hashes",
                "-r",
                str(tool_requirements),
            ],
            check=True,
            env=env,
        )
        identity = json.loads(
            subprocess.check_output(
                [
                    str(builder),
                    "-I",
                    "-B",
                    "-c",
                    "import json; from packaging.markers import default_environment; from packaging.tags import sys_tags; print(json.dumps(dict(markers=default_environment(), tags=[str(t) for t in sys_tags()])))",
                ],
                env=env,
            )
        )
        if identity["markers"]["python_full_version"] != spec["python_version"]:
            raise ValueError("Pinned interpreter reports an unexpected Python version")
        snapshot = output / "source.tar"
        subprocess.run(
            ["git", "-C", str(source_repo), "archive", "--format=tar", "--output", str(snapshot), commit],
            check=True,
            env=env,
        )
        source = work / "source"
        source.mkdir()
        with tarfile.open(snapshot) as archive:
            archive.extractall(source, filter="data")
        epoch = subprocess.check_output(
            ["git", "-C", str(source_repo), "show", "-s", "--format=%ct", commit], text=True
        ).strip()
        # hatch-vcs invokes get_version without a distribution name, so the
        # package-specific setuptools-scm override is not available here.
        env.update({"SOURCE_DATE_EPOCH": epoch, "SETUPTOOLS_SCM_PRETEND_VERSION": args.server_version})
        exported = output / "upstream-requirements.lock"
        subprocess.run(
            [
                uv,
                "export",
                "--project",
                str(source),
                "--locked",
                "--no-dev",
                "--extra",
                "ops",
                "--extra",
                "cli",
                "--no-emit-project",
                "--no-emit-workspace",
                "--no-header",
                "--no-annotate",
                "--output-file",
                str(exported),
            ],
            check=True,
            env=env,
            stdout=subprocess.DEVNULL,
        )
        locked = tomllib.loads((source / "uv.lock").read_text(encoding="utf-8"))
        dependencies = _locked_dependencies(
            exported.read_text(encoding="utf-8"), locked, identity["markers"], identity["tags"], args.target
        )
        downloads = work / "downloads"
        downloads.mkdir()
        intel_pins = json.loads(build_intel_managed_wheel.PINS.read_bytes())
        native_artifacts = (
            list(intel_pins["artifacts"].values())
            if any(item["kind"] == "intel-cryptography-sdist" for item in dependencies)
            else []
        )
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(
                pool.map(
                    lambda item: acquire(item, downloads / item["filename"], args.download_cache),
                    [*dependencies, *native_artifacts],
                )
            )
        wheels = output / "wheels"
        wheels.mkdir()
        native_builds = []
        for item in dependencies:
            if item["kind"] == "wheel":
                shutil.copyfile(downloads / item["filename"], wheels / item["filename"])
            elif item["kind"] == "intel-cryptography-sdist":
                native_builds.append(
                    build_intel_managed_wheel.build(
                        source_archive=downloads / item["filename"],
                        source_identity=item,
                        artifacts=downloads,
                        dependencies=dependencies,
                        python=builder,
                        uv=uv,
                        wheels=wheels,
                        environment=env,
                    )
                )
            else:
                subprocess.run(
                    [
                        str(builder),
                        "-I",
                        "-B",
                        "-m",
                        "pip",
                        "--no-cache-dir",
                        "wheel",
                        "--no-deps",
                        "--no-build-isolation",
                        "--no-index",
                        "--wheel-dir",
                        str(wheels),
                        str(downloads / item["filename"]),
                    ],
                    check=True,
                    env=env,
                    cwd=work,
                )
        subprocess.run(
            [
                uv,
                "build",
                "--offline",
                "--no-build-isolation",
                "--python",
                str(builder),
                "--wheel",
                "--no-create-gitignore",
                "--out-dir",
                str(wheels),
                str(source),
            ],
            check=True,
            env=env,
            cwd=work,
        )
        closure = [_wheel_identity(path) for path in sorted(wheels.glob("*.whl"))]
        project = [item for item in closure if item["name"] == "powercontext"]
        if len(project) != 1 or project[0]["version"] != args.server_version or len(closure) != len(dependencies) + 1:
            raise ValueError("Built wheel closure does not match the requested Server version and dependencies")
        requirements = output / "requirements.lock"
        requirements.write_text(
            "".join(
                f"{p['name']}{'[ops,cli]' if p['name'] == 'powercontext' else ''}=={p['version']} --hash=sha256:{p['sha256']}\n"
                for p in closure
            ),
            encoding="utf-8",
            newline="\n",
        )
        metadata = {
            "format_version": 1,
            "target": args.target,
            "server_version": args.server_version,
            "interpreter": spec,
            "interpreter_lock_sha256": sha256(_INTERPRETERS),
            "source": {
                "repository": _SOURCE_REPOSITORY,
                "commit": commit,
                "archive_sha256": sha256(snapshot),
                "uv_lock_sha256": sha256(source / "uv.lock"),
                "pyproject_sha256": sha256(source / "pyproject.toml"),
                "source_date_epoch": int(epoch),
            },
            "build_tools": {
                "lock_sha256": sha256(_BUILD_TOOLS),
                "packages": build_lock["packages"],
                "uv_version": subprocess.check_output([uv, "--version"], text=True).strip(),
            },
            "upstream_artifacts": dependencies,
            "wheels": closure,
            "requirements_sha256": sha256(requirements),
            "upstream_requirements_sha256": sha256(exported),
            "normalized_runtime_aliases": aliases,
            "preparer": producer_identity(),
            "intel_build_pins_sha256": sha256(build_intel_managed_wheel.PINS),
            "native_builds": native_builds,
        }
        (output / "inputs.json").write_bytes(json_bytes(metadata))
        print(f"Frozen native candidate inputs: {output}; inputs_sha256={sha256(output / 'inputs.json')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=json.loads(_INTERPRETERS.read_bytes())["targets"], required=True)
    parser.add_argument("--source", type=Path, default=_SCRIPTS.parent)
    parser.add_argument("--source-ref", required=True, help="Exact source Git commit, never a moving ref")
    parser.add_argument("--server-version", required=True, help="Exact package version to embed and verify")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--runtime-archive", type=Path, help="Optional already downloaded archive; the fixed digest is still mandatory"
    )
    parser.add_argument("--uv", default="uv")
    parser.add_argument(
        "--download-cache", type=Path, help="Optional task-local digest-addressed cache; every cache hit is verified"
    )
    prepare(parser.parse_args())


if __name__ == "__main__":
    main()
