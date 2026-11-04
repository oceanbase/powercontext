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

"""Run pinned, unmodified Hermes code in an isolated subprocess; never fall back to a mock."""

from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration_conformance.hermes.run_manager import reject_checkout_dotenv
from tests.integration_conformance.hermes.support import FIXTURE_HASH, FIXTURE_ID

HERMES_SHA = "4131fd95700685444c3522bf1ea07578c4003523"
ROOT = Path(__file__).resolve().parents[3]
ENTRY = "tests.integration_conformance.hermes.run_manager"


def git(checkout, *args):
    return subprocess.run(
        [shutil.which("git") or "git", "-C", str(checkout), *args],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    ).stdout.strip()


def test_real_memory_manager_http_storage(tmp_path, request):
    report = {
        "schema": "powercontext.hermes-conformance",
        "version": 1,
        "status": "skipped",
        "failure_category": "checkout_not_configured",
        "powercontext": {"sha": git(ROOT, "rev-parse", "HEAD"), "dirty": bool(git(ROOT, "status", "--porcelain"))},
        "hermes": {"expected_sha": HERMES_SHA, "sha": None, "version": None},
        "os": {"system": platform.system(), "release": platform.release()},
        "fixture": {"id": FIXTURE_ID, "sha256": FIXTURE_HASH},
        "entry": ENTRY,
        "model": "not-used",
        "hermes_model": "not-used",
        "evidence": {
            "adapter-only": {"status": "unverified", "executed": False},
            "real-manager": {"status": "skipped", "executed": False, "failure_category": "checkout_not_configured"},
            "real-compaction": {
                "status": "unverified",
                "executed": False,
                "failure_category": "compaction_path_not_exercised",
            },
        },
    }
    output = request.config.getoption("hermes_conformance_output")
    stage = "checkout_invalid"
    try:
        configured = os.environ.get("POWERCONTEXT_TEST_HERMES_CHECKOUT")
        if not configured:
            pytest.skip("External Hermes host acceptance NOT executed: POWERCONTEXT_TEST_HERMES_CHECKOUT is unset")
        checkout = Path(configured).resolve(strict=True)
        sha = git(checkout, "rev-parse", "HEAD")
        report["hermes"]["sha"] = sha
        # The commit is also an unambiguous version when this checkout has no release tag.
        report["hermes"]["version"] = sha[:12]
        stage = "version_mismatch"
        assert sha == HERMES_SHA
        stage = "checkout_modified"
        assert not git(checkout, "status", "--porcelain", "--untracked-files=no")
        stage = "checkout_local_config"
        reject_checkout_dotenv(checkout)

        home = tmp_path / "home"
        home.mkdir()
        managed = home / "managed"
        managed.mkdir()
        # Do not inherit model keys, secret scopes, user profiles, proxies or provider overrides.
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE"}
        }
        environment.update({
            "HOME": str(home),
            "USERPROFILE": str(home),
            "HERMES_HOME": str(tmp_path / "scenario" / "manager" / "profile"),
            "HERMES_MANAGED_DIR": str(managed),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_CACHE_HOME": str(home / "cache"),
            "APPDATA": str(home / "appdata"),
            "LOCALAPPDATA": str(home / "localappdata"),
            "TEMP": str(tmp_path),
            "TMP": str(tmp_path),
            "PYTHONPATH": os.pathsep.join(map(str, (ROOT, ROOT / "src", checkout))),
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONUTF8": "1",
            "NO_PROXY": "localhost,127.0.0.1,::1",
        })
        result_path = tmp_path / "child-result.json"
        stage = "subprocess_error"
        report["model"] = "stub"
        report["hermes_model"] = "stub"
        result = subprocess.run(
            [
                os.environ.get("POWERCONTEXT_TEST_HERMES_PYTHON", sys.executable),
                "-m",
                ENTRY,
                str(tmp_path / "scenario"),
                str(result_path),
                str(checkout),
            ],
            cwd=home,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
            check=False,
        )
        stage = "result_invalid"
        assert result_path.stat().st_size < 16_384
        child = json.loads(result_path.read_text(encoding="utf-8"))
        report["evidence"].update(child)
        stage = child["real-manager"].get("failure_category") or "scenario_failed"
        assert child["real-manager"]["status"] == "passed"
        stage = child["adapter-only"].get("failure_category") or "adapter_scenario_failed"
        assert child["adapter-only"]["status"] == "passed"
        stage = child["real-compaction"].get("failure_category") or "compaction_scenario_failed"
        assert child["real-compaction"]["status"] == "passed"
        stage = "subprocess_error"
        assert result.returncode == 0
        report.update(status="passed", failure_category=None)
    except Exception:
        # No raw exceptions, paths, transcripts, request/Source identities or subprocess logs
        # enter the shareable artifact. The fixed category identifies the failing boundary.
        report.update(status="failed", failure_category=stage)
        if report["evidence"]["real-manager"]["status"] == "skipped":
            report["evidence"]["real-manager"] = {
                "status": "failed",
                "executed": False,
                "failure_category": stage,
            }
        pytest.fail(f"Hermes conformance failed: {stage}; see the content-safe JSON result", pytrace=False)
    finally:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
