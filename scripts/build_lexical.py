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


"""Build the native analyzer into regular and editable PowerContext wheels."""

import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from hatchling.builders.config import BuilderConfig
from hatchling.builders.hooks.plugin.interface import BuildHookInterface
from typing_extensions import override


class CustomBuildHook(BuildHookInterface[BuilderConfig]):
    @override
    def initialize(self, version, build_data):
        if self.target_name != "wheel":
            return
        root = Path(self.root).resolve()
        crate = root / "src/powercontext/builtin/artifacts/lexical"
        self._temporary = tempfile.TemporaryDirectory(prefix="powercontext-lexical-")
        work = Path(self._temporary.name)
        env = os.environ.copy()
        toolchain = "1.95.0"
        if os.name == "nt":
            arch = "aarch64" if platform.machine().lower() == "arm64" else "x86_64"
            toolchain += f"-{arch}-pc-windows-msvc"
        env.setdefault("RUSTUP_TOOLCHAIN", toolchain)
        env.setdefault("CARGO_TARGET_DIR", str(root / ".artifacts/lexical-target"))
        env.setdefault("SOURCE_DATE_EPOCH", "315532800")
        flags = env.get("CARGO_ENCODED_RUSTFLAGS")
        options = flags.split("\x1f") if flags else shlex.split(env.get("RUSTFLAGS", ""))
        options.append(f"--remap-path-prefix={root}=powercontext")
        if os.name == "nt":
            options += ["-C", "link-arg=/Brepro", "-C", "link-arg=/PDBALTPATH:powercontext-lexical.pdb"]
        env["CARGO_ENCODED_RUSTFLAGS"] = "\x1f".join(options)
        subprocess.run(  # noqa: S603 - fixed build tool and checkout-owned manifest.
            [
                sys.executable,
                "-m",
                "maturin",
                "build",
                "--locked",
                "--release",
                "--manifest-path",
                str(crate / "Cargo.toml"),
                "--out",
                str(work),
            ],
            cwd=crate,
            env=env,
            check=True,
        )
        (wheel,) = work.glob("*.whl")
        build_data["pure_python"] = False
        build_data["tag"] = "-".join(wheel.stem.rsplit("-", 3)[1:])
        payload = work / "payload"
        with zipfile.ZipFile(wheel) as archive:
            extensions = [
                name
                for name in archive.namelist()
                if name.startswith("_lexical/_lexical.") and name.endswith((".pyd", ".so"))
            ]
            (extension,) = extensions
            members = {extension: "powercontext/" + Path(extension).name}
            # Keep maturin's audited shared libraries at the same relative depth.
            members.update({name: name for name in archive.namelist() if ".libs/" in name and not name.endswith("/")})
            for name, destination in members.items():
                target = payload / destination
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
                build_data["force_include"][str(target)] = destination
                if version == "editable":
                    editable = root / "src" / destination
                    editable.parent.mkdir(parents=True, exist_ok=True)
                    # Windows locks a loaded .pyd; unchanged rebuilds need no replacement.
                    if not editable.is_file() or editable.read_bytes() != target.read_bytes():
                        shutil.copyfile(target, editable)

    @override
    def finalize(self, version, build_data, artifact_path):
        temporary = getattr(self, "_temporary", None)
        if temporary is not None:
            temporary.cleanup()
