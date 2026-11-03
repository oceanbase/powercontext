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

"""Build an offline Ops bundle from a redistributable Python tree and hashed wheels.

This is a release-producer tool, not an end-user installer. Inputs must already be
trusted and qualified for redistribution on the build host's platform/architecture.
"""

# ruff: noqa: TRY003 - build failures carry actionable operator diagnostics

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

_ENTRY = '''"""Start Ops using only its own bundled dependencies and Python standard library."""
import runpy
import sys
from pathlib import Path

if not sys.flags.isolated or not sys.flags.no_site:
    raise SystemExit("Start this entry with the bundled Python and -I -S")
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent / "packages"))
runpy.run_module("powercontext.ops", run_name="__main__")
'''

_PROBE = """
import json, platform, sys
print(json.dumps(dict(executable=sys.executable, prefix=sys.prefix,
                     base_prefix=sys.base_prefix, paths=sys.path,
                     python_version=platform.python_version(),
                     platform=sys.platform, architecture=platform.machine())))
"""


def _runtime_python(runtime: Path, relative: str) -> Path:
    python = runtime / relative
    if not python.resolve().is_relative_to(runtime.resolve()) or not python.is_file():
        raise ValueError("Python executable must be an existing file inside the runtime")
    if (runtime / "pyvenv.cfg").exists():
        raise ValueError("A virtual environment is not a redistributable Python runtime")
    for item in runtime.rglob("*"):
        if item.is_symlink():
            if Path(os.readlink(item)).is_absolute() or not item.resolve().is_relative_to(runtime.resolve()):
                raise ValueError("Runtime symlinks must be relative and remain inside the runtime")
        elif getattr(item.lstat(), "st_file_attributes", 0) & 0x400:
            raise ValueError("Runtime must not contain junctions or other reparse points")
    return python


def _isolated_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.upper().startswith(("PYTHON", "UV_", "PIP_"))}


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def build(args: argparse.Namespace) -> None:
    runtime = args.runtime.resolve()
    output = args.output.resolve()
    _runtime_python(runtime, args.python_relative)
    if output.is_relative_to(runtime) or runtime.is_relative_to(output):
        raise ValueError("Output and input runtime trees must not contain each other")
    if output.exists():
        raise ValueError("Output must not exist; partial builds must be inspected before retrying")
    uv = shutil.which(args.uv)
    if uv is None:
        raise ValueError("The build host needs uv to install the supplied offline wheel closure")
    output.mkdir(parents=True)
    shutil.copytree(runtime, output / "runtime", symlinks=True)
    python = _runtime_python(output / "runtime", args.python_relative)
    environment = _isolated_environment()
    probe = subprocess.run(  # noqa: S603 - trusted, explicitly selected build input
        [str(python), "-I", "-S", "-B", "-c", _PROBE],
        cwd=output,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    identity = json.loads(probe.stdout)
    for value in [identity["prefix"], identity["base_prefix"], identity["executable"], *identity["paths"]]:
        if not Path(value).resolve().is_relative_to(output / "runtime"):
            raise ValueError("Copied Python still depends on paths outside the bundle runtime")
    subprocess.run(  # noqa: S603 - offline installer with trusted release-producer inputs
        [
            uv,
            "--offline",
            "pip",
            "install",
            "--python",
            str(python),
            "--target",
            str(output / "packages"),
            "--link-mode",
            "copy",
            "--no-index",
            "--find-links",
            str(args.wheelhouse.resolve()),
            "--only-binary",
            ":all:",
            "--require-hashes",
            "--requirements",
            str(args.requirements.resolve()),
        ],
        cwd=output,
        env=environment,
        check=True,
        timeout=600,
    )
    entry = output / "entry.py"
    entry.write_text(_ENTRY, encoding="utf-8")
    check = subprocess.run(  # noqa: S603 - verify the copied bundle, never the source checkout
        [str(python), "-I", "-S", "-B", str(entry), "--help"],
        cwd=output,
        env={**environment, "PATH": "", "PYTHONPATH": str(output / "nonexistent")},
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if not check.stdout.strip():
        raise ValueError("Ops entry did not provide its command-line interface")
    paths = [item for item in sorted(output.rglob("*")) if item.is_file()]
    with ThreadPoolExecutor(max_workers=4) as hashing:
        files = {
            item.relative_to(output).as_posix(): digest
            for item, digest in zip(paths, hashing.map(_sha256, paths), strict=True)
        }
    manifest = {
        "format_version": 1,
        "python_version": identity["python_version"],
        "platform": identity["platform"],
        "architecture": identity["architecture"],
        "python_relative": "runtime/" + Path(args.python_relative).as_posix(),
        "arguments": ["-I", "-S", "-B", "entry.py"],
        "requirements_sha256": hashlib.sha256(args.requirements.read_bytes()).hexdigest(),
        "installed_bytes": sum((output / relative).stat().st_size for relative in files),
        "files_sha256": files,
    }
    (output / "bundle.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Built independent Ops bundle: {output}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", type=Path, required=True, help="Trusted, complete redistributable Python tree")
    parser.add_argument("--python-relative", required=True, help="python.exe on Windows; bin/python3 on macOS")
    parser.add_argument(
        "--wheelhouse", type=Path, required=True, help="Prepared compatible wheels, including PowerContext"
    )
    parser.add_argument(
        "--requirements", type=Path, required=True, help="Exact versions and SHA-256 hashes of all wheels"
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="New bundle directory; never replaces an existing tree"
    )
    parser.add_argument("--uv", default="uv", help="Build-host uv executable (not shipped or used at runtime)")
    build(parser.parse_args())


if __name__ == "__main__":
    main()
