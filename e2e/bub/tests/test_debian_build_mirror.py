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

import runpy
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "configure_debian_mirror.py"


def test_debian_build_mirror_preserves_repository_metadata_and_other_routes(tmp_path: Path) -> None:
    etc = tmp_path / "etc"
    apt = etc / "apt"
    sources = apt / "sources.list.d"
    sources.mkdir(parents=True)
    (etc / "os-release").write_text("ID=debian\nVERSION_CODENAME=trixie\n")
    legacy = "deb [signed-by=/keys/debian.gpg] http://deb.debian.org/debian/ trixie main contrib\n"
    custom = "deb https://packages.example.org/debian trixie main\n"
    comment = "# deb http://deb.debian.org/debian stable main\n"
    (apt / "sources.list").write_text(legacy + custom + comment)
    deb822 = (
        "Types: deb deb-src\n"
        "URIs: https://deb.debian.org/debian-security http://deb.debian.org/debian https://custom.example/debian\n"
        "Suites: trixie-security\nComponents: main non-free-firmware\n"
        "Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg\n"
    )
    (sources / "debian.sources").write_text(deb822)
    config = apt / "apt.conf.d"
    config.mkdir()
    proxy = 'Acquire::http::Proxy "http://proxy.example:7890";\n'
    (config / "90proxy").write_text(proxy)

    runpy.run_path(str(_SCRIPT))["configure_debian_mirror"]("https://mirror.example/base/", root=tmp_path)

    assert (apt / "sources.list").read_text() == legacy.replace(
        "http://deb.debian.org/debian/", "https://mirror.example/base/debian"
    ) + custom + comment
    assert (sources / "debian.sources").read_text() == deb822.replace(
        "https://deb.debian.org", "https://mirror.example/base"
    ).replace("http://deb.debian.org", "https://mirror.example/base")
    assert (config / "90proxy").read_text() == proxy
    assert (config / "99powercontext-debian-mirror").read_text() == (
        'Acquire::http::Proxy::mirror.example "DIRECT";\nAcquire::https::Proxy::mirror.example "DIRECT";\n'
    )


@pytest.mark.parametrize(("system", "mirror"), [("debian", ""), ("ubuntu", "https://mirror.example")])
def test_debian_build_mirror_keeps_default_and_other_os_unchanged(tmp_path: Path, system: str, mirror: str) -> None:
    etc = tmp_path / "etc"
    apt = etc / "apt"
    apt.mkdir(parents=True)
    (etc / "os-release").write_text(f'ID="{system}"\n')
    original = "deb http://deb.debian.org/debian stable main\n"
    (apt / "sources.list").write_text(original)

    runpy.run_path(str(_SCRIPT))["configure_debian_mirror"](mirror, root=tmp_path)

    assert (apt / "sources.list").read_text() == original
    assert not (apt / "apt.conf.d").exists()
