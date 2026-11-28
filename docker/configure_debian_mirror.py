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

"""Apply the optional Debian mirror before an image installs packages."""

import os
import re
from pathlib import Path
from urllib.parse import urlsplit

_DEFAULT_URI = re.compile(r"(?<!\S)https?://deb\.debian\.org(/debian(?:-security)?)/?(?=\s|$)")


def configure_debian_mirror(mirror: str, *, root: Path = Path("/")) -> None:
    if not mirror:
        return
    release = root / "etc/os-release"
    if not release.is_file() or not re.search(r'^ID=["\']?debian["\']?$', release.read_text(), re.MULTILINE):
        return
    parsed = urlsplit(mirror)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        msg = "The Debian mirror must be an HTTP(S) base URL"
        raise ValueError(msg)
    apt = root / "etc/apt"
    sources = [
        apt / "sources.list",
        *(apt / "sources.list.d").glob("*.list"),
        *(apt / "sources.list.d").glob("*.sources"),
    ]
    for source in sources:
        if not source.is_file():
            continue
        lines = []
        for line in source.read_text().splitlines(keepends=True):
            if re.match(r"^\s*(?:URIs:|deb(?:-src)?\s)", line):
                active, comment, suffix = line.partition("#")
                line = _DEFAULT_URI.sub(lambda match: mirror.rstrip("/") + match[1], active) + comment + suffix
            lines.append(line)
        source.write_text("".join(lines))
    config = apt / "apt.conf.d/99powercontext-debian-mirror"
    config.parent.mkdir(parents=True, exist_ok=True)
    # A per-host rule keeps the build client's proxy and private routes intact.
    config.write_text(
        f'Acquire::http::Proxy::{parsed.hostname} "DIRECT";\nAcquire::https::Proxy::{parsed.hostname} "DIRECT";\n'
    )


if __name__ == "__main__":
    configure_debian_mirror(os.environ.get("DEBIAN_MIRROR_URL", ""))
