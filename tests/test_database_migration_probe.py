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

"""The repository probe requires explicit non-interactive plan acceptance."""

import json
import subprocess
import sys
from pathlib import Path


def test_probe_plan_confirmation_and_noop(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts/database_migration_probe.py"
    database = tmp_path / "probe.sqlite3"

    def run(action: str, *options: str):
        return subprocess.run(
            [sys.executable, str(script), action, "--sqlite-path", str(database), *options],
            input="",
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )

    plan = run("plan")
    assert plan.returncode == 0
    plan_id = json.loads(plan.stdout)["plan_id"]
    assert not database.exists()
    for flags in [(), ("--yes",), ("--plan-id", plan_id)]:
        rejected = run("apply", "--maintenance-confirmed", *flags)
        assert rejected.returncode == 1
        assert json.loads(rejected.stderr)["error"] == "confirmation_required"
        assert not database.exists()
    applied = run("apply", "--maintenance-confirmed", "--yes", "--plan-id", plan_id)
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout)["changed"]
    assert run("verify").returncode == 0
    assert not json.loads(run("apply").stdout)["changed"]
