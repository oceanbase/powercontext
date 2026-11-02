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

"""Exercise batch isolation and result collection with real disposable virtualenvs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/ci_installation_roles.py"
pytestmark = pytest.mark.skipif(os.name == "nt", reason="The Ubuntu batch job probe uses a POSIX uv executable")


@pytest.fixture
def installation_probe(tmp_path):
    # Simulate only uv's downloads/installations, so the orchestration contract
    # runs offline; environment creation and isolated Python smoke processes are real.
    uv = tmp_path / "uv"
    uv.write_text(
        f"#!{sys.executable}\n"
        """
import pathlib
import subprocess
import sys
import venv

args = sys.argv[1:]
if 'venv' in args:
    venv.EnvBuilder(with_pip=False).create(args[-1])
else:
    python = args[args.index('--python') + 1]
    site = pathlib.Path(subprocess.check_output(
        [python, '-I', '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
    if args[-1].endswith((']', '.whl')):
        extras = args[-1].split('[')[1].removesuffix(']') if args[-1].endswith(']') else 'core'
        assert extras != 'core' or args[-1].endswith('.whl')
        (site / 'installed_role.py').write_text('ROLE = ' + repr(extras))
    else:
        for name in ('sqlalchemy', 'fastapi'):
            (site / (name + '.py')).write_text('')
""",
        encoding="utf-8",
    )
    uv.chmod(0o755)
    smoke = tmp_path / "smoke.py"
    smoke.write_text(
        """
import importlib.util
import json
import os
import pathlib
import sys
import tempfile

import installed_role

role = sys.argv[1]
assert sys.flags.isolated
assert sys.prefix != sys.base_prefix
assert installed_role.ROLE == role
root = pathlib.Path.cwd()
names = ('POWERCONTEXT_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME', 'XDG_CACHE_HOME', 'XDG_STATE_HOME')
for name in names:
    directory = pathlib.Path(os.environ[name])
    assert directory.parent == root
    marker = directory / 'owner'
    if marker.exists():
        assert marker.read_text() == role
    marker.write_text(role)
assert pathlib.Path(tempfile.gettempdir()).parent == root
counter = pathlib.Path(os.environ['POWERCONTEXT_HOME']) / 'smoke-count'
attempt = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(attempt))
assert (importlib.util.find_spec('fastapi') is not None) == (role == 'cli' and attempt == 2)
with open(os.environ['PROBE_REPORT'], 'a') as report:
    report.write(json.dumps({'role': role, 'venv': sys.prefix, 'cwd': str(root), 'attempt': attempt}) + '\\n')
if role == os.environ.get('FAIL_ROLE') and attempt == int(os.environ.get('FAIL_ATTEMPT', '1')):
    raise RuntimeError('deliberate role failure')
""",
        encoding="utf-8",
    )
    wheel = tmp_path / "fixture.whl"
    wheel.touch()
    return [sys.executable, str(SCRIPT), "--wheel", str(wheel), "--smoke-script", str(smoke)]


@pytest.mark.parametrize(("failed_role", "failed_attempt"), [("", 1), ("core", 1), ("builtin", 1), ("cli", 2)])
def test_batch_keeps_all_roles_isolated_and_reports_every_failure(
    installation_probe, tmp_path, failed_role, failed_attempt
):
    output = tmp_path / "results"
    evidence = tmp_path / "smokes.jsonl"
    environment = {
        **os.environ,
        "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"],
        "PROBE_REPORT": str(evidence),
        "FAIL_ROLE": failed_role,
        "FAIL_ATTEMPT": str(failed_attempt),
    }
    result = subprocess.run(
        [*installation_probe, "--output-dir", str(output)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert result.returncode == (1 if failed_role else 0), result.stdout + result.stderr
    report = json.loads((output / "summary.json").read_text())
    assert report["status"] == ("failure" if failed_role else "success")
    roles = {row["extras"]: row for row in report["roles"]}
    assert set(roles) == {"core", "builtin", "cli", "builtin,cli", "server,cli"}
    for role, row in roles.items():
        assert row["status"] == ("failure" if role == failed_role else "success")
        assert all(stage["status"] == "success" for stage in row["stages"]) == (role != failed_role)
        assert (output / "logs" / f"{role.replace(',', '-')}.log").is_file()
    records = [json.loads(line) for line in evidence.read_text().splitlines()]
    assert {record["role"] for record in records} == set(roles)
    assert len({record["venv"] for record in records}) == 5
    assert len({record["cwd"] for record in records}) == 5
    assert [record["attempt"] for record in records if record["role"] == "cli"] == [1, 2]
    assert all(role in (output / "summary.md").read_text() for role in roles)

    # A repeated invocation must not accidentally pass with environments from the first run.
    rerun = subprocess.run(
        [*installation_probe, "--output-dir", str(output)],
        env=environment,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert rerun.returncode != 0
    assert [json.loads(line) for line in evidence.read_text().splitlines()] == records
