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

"""Qualify a disposable checkout and built wheel without changing release evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

DESKTOP = Path(__file__).resolve().parents[1]
PROFILE_ID = "desktop-ci-fixture"


def build_profile(desktop: Path, server_commit: str) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9a-f]{40}", server_commit):
        raise ValueError("fixture_server_commit_required")
    wheels = list((desktop / ".artifacts/server-wheel").glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError("fixture_server_wheel_count")
    contract = json.loads((desktop / "src-tauri/src/transport/operations.json").read_text(encoding="utf-8"))
    contract_digest = hashlib.sha256(
        (desktop.parent / "openapi/powercontext.yaml").read_text(encoding="utf-8").encode("utf-8")
    ).hexdigest()
    if contract["contractSha256"] != contract_digest:
        raise ValueError("fixture_generated_contract_drift")
    return {
        "id": PROFILE_ID,
        "serverCommit": server_commit,
        "contractSha256": contract_digest,
        "artifactSha256": hashlib.sha256(wheels[0].read_bytes()).hexdigest(),
        "operations": sorted(contract["operations"]),
        "evidence": "Disposable checkout and built-wheel CI fixture; not production or release qualification",
    }


def fixture_profile(desktop: Path = DESKTOP) -> dict[str, Any]:
    profile = json.loads((desktop / ".artifacts/ci-compatibility.json").read_text(encoding="utf-8"))
    if profile != build_profile(desktop, profile["serverCommit"]):
        raise ValueError("fixture_qualification_mismatch")
    return profile


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server-commit", required=True)
    args = parser.parse_args()
    profile = build_profile(DESKTOP, args.server_commit)
    destination = DESKTOP / ".artifacts/ci-compatibility.json"
    destination.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
