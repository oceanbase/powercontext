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

"""Resolve a published package and a wheel installable by the verification Python."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any

from ci_verify_github_release import fetch_release
from packaging.tags import Tag, sys_tags
from packaging.utils import InvalidWheelFilename, parse_wheel_filename
from packaging.version import Version


def release_version(tag: str) -> str:
    version = tag.removeprefix("powercontext-v") if tag.startswith("powercontext-v") else tag.removeprefix("v")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?", version):
        raise SystemExit(  # noqa: TRY003 - command diagnostics describe the failed release check.
            "release_tag must use X.Y.Z with optional aN, bN, or rcN suffix and "
            "optional v or powercontext-v prefix (for example, powercontext-v1.1.0)"
        )
    return version


def select_assets(
    metadata: dict[str, Any], version: str, package: str, tags: list[Tag] | None = None
) -> dict[str, str]:
    """Detect the package from wheel names, then rank compatible wheels as pip does."""
    if package not in {"auto", "powercontext", "powermem"}:
        raise SystemExit("release_package must be one of: auto, powercontext, powermem")  # noqa: TRY003 - command diagnostics describe the failed release check.
    wheels = []
    for asset in metadata.get("assets", []):
        name = asset.get("name", "")
        try:
            distribution, wheel_version, _build, wheel_tags = parse_wheel_filename(name)
        except InvalidWheelFilename:
            continue
        if distribution in {"powercontext", "powermem"} and wheel_version == Version(version):
            wheels.append((name, distribution, wheel_tags))
    if package == "auto":
        candidates = {distribution for _name, distribution, _tags in wheels}
        if len(candidates) != 1:
            raise SystemExit(  # noqa: TRY003 - command diagnostics describe the failed release check.
                "could not uniquely detect the package contract from Release wheel assets; "
                "select release_package explicitly"
            )
        package = candidates.pop()
    ranks = {tag: rank for rank, tag in enumerate(sys_tags() if tags is None else tags)}
    compatible = [
        (min(ranks[tag] for tag in wheel_tags if tag in ranks), name)
        for name, distribution, wheel_tags in wheels
        if distribution == package and wheel_tags.intersection(ranks)
    ]
    if not compatible:
        raise SystemExit(f"No Release wheel for {package}=={version} is compatible with this Python")  # noqa: TRY003 - command diagnostics describe the failed release check.
    _rank, wheel = min(compatible)
    return {
        "package": package,
        "version": version,
        "wheel": wheel,
        "sdist": f"{package}-{version}.tar.gz",
        "native": str(any(tag.abi != "none" for tag in parse_wheel_filename(wheel)[3])).lower(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, help="Read an already fetched GitHub Release JSON file")
    args = parser.parse_args()
    tag = os.environ["RELEASE_TAG"]
    version = release_version(tag)
    metadata = (
        json.loads(args.metadata.read_text(encoding="utf-8"))
        if args.metadata
        else fetch_release(os.environ["REPOSITORY"], tag, os.environ.get("GITHUB_TOKEN"))
    )
    selected = select_assets(metadata, version, os.environ["RELEASE_PACKAGE"])
    with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as output:
        for key, value in selected.items():
            output.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
