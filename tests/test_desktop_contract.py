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

"""Keep Desktop's frontend and native contract identities aligned with OpenAPI."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "manifest",
    [
        "desktop/ui/src/generated/operations.ts",
        "desktop/src-tauri/src/transport/operations.json",
    ],
)
def test_desktop_contract_identity_matches_openapi(manifest: str) -> None:
    contract = (REPOSITORY_ROOT / "openapi/powercontext.yaml").read_bytes().replace(b"\r\n", b"\n")
    expected = hashlib.sha256(contract).hexdigest()
    path = REPOSITORY_ROOT / manifest
    source = path.read_text(encoding="utf-8")
    if path.suffix == ".json":
        actual = json.loads(source)["contractSha256"]
    else:
        match = re.search(r'export const contractSha256 = "([0-9a-f]{64})";', source)
        assert match is not None, f"Missing Desktop contract identity in {manifest}"
        actual = match.group(1)

    assert actual == expected, f"Stale Desktop contract identity in {manifest}; run 'pnpm --dir desktop generate'."


def test_bundled_desktop_contract_has_a_qualified_business_profile() -> None:
    native = REPOSITORY_ROOT / "desktop/src-tauri/src"
    contract = json.loads((native / "transport/operations.json").read_text(encoding="utf-8"))
    profiles = json.loads((native / "connections/compatibility.json").read_text(encoding="utf-8"))
    required = {"remember_memory", "search_memory", "get_artifact_revision"}
    assert any(
        profile["contractSha256"] == contract["contractSha256"] and required <= set(profile["operations"])
        for profile in profiles
    ), "The bundled Desktop contract has no qualified profile for save, search and exact reads."
