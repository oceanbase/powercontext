# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Shared plugin sources must stay identical across hosts until they move to one package.

Kimi Code and ZCode ship byte-identical copies of the JavaScript prompt-privacy module, and DSH,
OpenClaw, OpenCode, and Pi ship byte-identical copies of the TypeScript transport. The Python
helpers that hosts share are generated from ``src/powercontext_integration_lifecycle/`` instead,
and ``make integration-lifecycle-check`` verifies their copies.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
PLUGINS = "integrations/*/plugins/*"
SHARED_SOURCES = ("shared/prompt-privacy.mjs", "src/transport.ts")
# Installed dependencies are not plugin sources, even when one ships a file with the same name.
_DEPENDENCY_DIRECTORIES = frozenset({"node_modules", ".venv"})


def _copies(name: str) -> list[Path]:
    found = {*ROOT.glob(f"{PLUGINS}/{name}"), *ROOT.glob(f"{PLUGINS}/**/{Path(name).name}")}
    return sorted(path for path in found if _DEPENDENCY_DIRECTORIES.isdisjoint(path.relative_to(ROOT).parts))


@pytest.mark.parametrize("name", SHARED_SOURCES)
def test_shared_plugin_source_is_identical_in_every_host(name: str) -> None:
    copies = _copies(name)
    assert len(copies) > 1, f"{name} is no longer shared; drop it from SHARED_SOURCES"

    expected = copies[0].read_bytes().replace(b"\r\n", b"\n")
    drifted = [
        str(copy.relative_to(ROOT)) for copy in copies[1:] if copy.read_bytes().replace(b"\r\n", b"\n") != expected
    ]
    assert not drifted, f"{name} differs from {copies[0].relative_to(ROOT)} in: {', '.join(drifted)}"
