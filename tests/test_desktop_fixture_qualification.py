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

"""Disposable fixture qualification preserves release evidence and rejects drift."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def qualification():
    specification = importlib.util.spec_from_file_location(
        "desktop_fixture_qualification", ROOT / "desktop/tests/fixture_qualification.py"
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


@pytest.fixture
def desktop(tmp_path: Path) -> Path:
    desktop = tmp_path / "desktop"
    (desktop / "tests").mkdir(parents=True)
    shutil.copyfile(ROOT / "desktop/tests/fixture_qualification.py", desktop / "tests/fixture_qualification.py")
    (desktop / ".artifacts/server-wheel").mkdir(parents=True)
    (desktop / ".artifacts/server-wheel/powercontext-fixture.whl").write_bytes(b"synthetic fixture artifact")
    (tmp_path / "openapi").mkdir()
    contract = tmp_path / "openapi/powercontext.yaml"
    contract.write_bytes(b"openapi: 3.0.3\n# Synthetic current contract\n")
    transport = desktop / "src-tauri/src/transport"
    transport.mkdir(parents=True)
    (transport / "operations.json").write_text(
        json.dumps({
            "contractSha256": hashlib.sha256(contract.read_bytes()).hexdigest(),
            "operations": {"remember_memory": {"method": "POST", "path": "/v1/memory/remember"}},
        }),
        encoding="utf-8",
    )
    historical = desktop / "src-tauri/src/connections"
    historical.mkdir()
    shutil.copyfile(ROOT / "desktop/src-tauri/src/connections/compatibility.json", historical / "compatibility.json")
    return desktop


def test_cli_qualifies_current_fixture_without_rewriting_historical_profiles(desktop: Path, qualification) -> None:
    historical = desktop / "src-tauri/src/connections/compatibility.json"
    before = historical.read_bytes()
    result = subprocess.run(
        [sys.executable, "-I", str(desktop / "tests/fixture_qualification.py"), "--server-commit", "a" * 40],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    profile = qualification.fixture_profile(desktop)
    assert profile["id"] == "desktop-ci-fixture"
    assert profile["serverCommit"] == "a" * 40
    assert profile["operations"] == ["remember_memory"]
    assert profile["artifactSha256"] == hashlib.sha256(b"synthetic fixture artifact").hexdigest()
    assert historical.read_bytes() == before
    assert all(entry["contractSha256"] != profile["contractSha256"] for entry in json.loads(before))


@pytest.mark.parametrize("changed", ["wheel", "contract", "operations"])
def test_fixture_readback_rejects_changed_artifact_or_contract(desktop: Path, qualification, changed: str) -> None:
    profile = qualification.build_profile(desktop, "a" * 40)
    (desktop / ".artifacts/ci-compatibility.json").write_text(json.dumps(profile), encoding="utf-8")
    if changed == "wheel":
        (desktop / ".artifacts/server-wheel/powercontext-fixture.whl").write_bytes(b"replacement fixture artifact")
    elif changed == "contract":
        (desktop.parent / "openapi/powercontext.yaml").write_bytes(b"changed contract")
    else:
        transport = desktop / "src-tauri/src/transport/operations.json"
        contract = json.loads(transport.read_text(encoding="utf-8"))
        contract["operations"]["search_memory"] = {"method": "POST", "path": "/v1/memory/search"}
        transport.write_text(json.dumps(contract), encoding="utf-8")
    with pytest.raises(ValueError, match=r"fixture_(qualification_mismatch|generated_contract_drift)"):
        qualification.fixture_profile(desktop)


def test_stale_generated_contract_cannot_create_fixture_qualification(desktop: Path, qualification) -> None:
    (desktop.parent / "openapi/powercontext.yaml").write_bytes(b"new contract")
    with pytest.raises(ValueError, match="fixture_generated_contract_drift"):
        qualification.build_profile(desktop, "a" * 40)
    assert not (desktop / ".artifacts/ci-compatibility.json").exists()
