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

"""Create reviewable source archives; official CLI validation produces .difypkg files."""

from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT.parents[1] / "dist" / "dify"


def package_sources() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name in ("powercontext", "powercontext_agent"):
        package = ROOT / name
        target = OUTPUT / f"{name}-0.0.1-source.zip"
        with ZipFile(target, "w", compression=ZIP_DEFLATED) as archive:
            for path in sorted(package.rglob("*")):
                if path.is_file() and (
                    path.suffix in {".py", ".yaml", ".svg", ".md", ".txt"} or path.name in {"LICENSE", ".difyignore"}
                ):
                    archive.write(path, f"{name}/{path.relative_to(package)}")
        print(target)


if __name__ == "__main__":
    package_sources()
