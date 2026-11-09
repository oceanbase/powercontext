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

"""Stage the checksum-pinned NSIS tools required by the locked Tauri CLI."""

from __future__ import annotations

import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from download_ci_asset import acquire_asset


def main() -> None:
    # Tauri CLI 2.11.4 uses this cache layout and still verifies the plugin itself.
    # Source: tauri-apps/tauri, tauri-cli-v2.11.4, crates/tauri-bundler/src/bundle/windows/nsis/mod.rs.
    target = Path(os.environ["LOCALAPPDATA"]) / "tauri" / "NSIS"
    with tempfile.TemporaryDirectory(prefix="powercontext-nsis-") as directory:
        root = Path(directory)
        archive = root / "nsis.zip"
        acquire_asset(
            "https://github.com/tauri-apps/binary-releases/releases/download/nsis-3.11/nsis-3.11.zip",
            archive,
            "c7d27f780ddb6cffb4730138cd1591e841f4b7edb155856901cdf5f214394fa1",
        )
        plugin = root / "nsis_tauri_utils.dll"
        acquire_asset(
            "https://github.com/tauri-apps/nsis-tauri-utils/releases/download/nsis_tauri_utils-v0.5.3/nsis_tauri_utils.dll",
            plugin,
            "5ba143b5db4a87d32d6e7802e033330aae56cbceabe0d1e3ba41948385ad4709",
        )
        with zipfile.ZipFile(archive) as source:
            source.extractall(root)
        shutil.copytree(root / "nsis-3.11", target, dirs_exist_ok=True)
        plugins = target / "Plugins" / "x86-unicode" / "additional"
        plugins.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(plugin, plugins / plugin.name)


if __name__ == "__main__":
    main()
