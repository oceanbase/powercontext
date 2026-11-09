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

"""Acquire checksum-pinned CI assets with bounded official Release API recovery."""

# ruff: noqa: TRY003, S603 - fixed CI input URLs and host tools

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

_WINDOWS_ACCESS_VIOLATION = 0xC0000005


def _download_release_asset(url: str, partial: Path, env: dict[str, str], deadline: float) -> bool:
    """Use the official API for the same pinned asset when the release route fails."""
    parsed = urlsplit(url)
    parts = parsed.path.strip("/").split("/")
    if (
        parsed.netloc != "github.com"
        or parsed.query
        or len(parts) != 6
        or parts[2:4] != ["releases", "download"]
        or shutil.which("gh") is None
        or time.monotonic() >= deadline
    ):
        return False
    owner, repository, _, _, tag, filename = parts
    endpoint = f"repos/{owner}/{repository}/releases"
    command = ["gh", "api", "--hostname", "github.com"]
    result = subprocess.run(
        [*command, f"{endpoint}/tags/{quote(unquote(tag), safe='')}"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
        timeout=min(30, max(0.001, deadline - time.monotonic())),
    )
    assets = json.loads(result.stdout)["assets"]
    asset = next((asset for asset in assets if asset["name"] == unquote(filename)), None)
    if asset is None or type(asset.get("id")) is not int or asset["id"] <= 0:
        raise ValueError("The pinned GitHub release asset is unavailable")
    partial.unlink(missing_ok=True)
    with partial.open("xb") as output:
        subprocess.run(
            [*command, f"{endpoint}/assets/{asset['id']}", "--header", "Accept: application/octet-stream"],
            check=True,
            stdout=output,
            env=env,
            timeout=max(0.001, deadline - time.monotonic()),
        )
    return True


def download(url: str, partial: Path, *, env: dict[str, str], timeout: float = 1900) -> None:
    deadline = time.monotonic() + timeout
    command = [
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
        str(min(1800, timeout)),
        "--output",
        str(partial),
        url,
    ]
    for attempt in range(2):
        try:
            subprocess.run(command, check=True, env=env, timeout=max(0.001, deadline - time.monotonic()))
        except subprocess.CalledProcessError as error:
            if error.returncode in {5, 6, 7, 18, 22, 28, 35, 52, 55, 56} and _download_release_asset(
                url, partial, env, deadline
            ):
                return
            # curl's own retry cannot recover from a process crash on the Windows runner.
            if attempt or error.returncode != _WINDOWS_ACCESS_VIOLATION:
                raise
            partial.unlink(missing_ok=True)
        else:
            return


def acquire_asset(url: str, destination: Path, digest: str, *, timeout: float = 300) -> None:
    """Publish a complete file only after verifying its checked-in SHA256."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("CI assets require fixed HTTPS source URLs")
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or timeout <= 0:
        raise ValueError("CI assets require a SHA256 digest and positive deadline")
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    if destination.exists() or partial.exists():
        raise FileExistsError("CI download output must be fresh")
    download(url, partial, env=os.environ.copy(), timeout=timeout)
    with partial.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != digest:
        raise ValueError("Downloaded CI asset does not match its pinned SHA256")
    partial.rename(destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300)
    args = parser.parse_args()
    acquire_asset(args.url, args.output, args.sha256, timeout=args.timeout)


if __name__ == "__main__":
    main()
