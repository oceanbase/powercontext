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

"""Checkout-local configuration must be refused before host code can load it."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize("local_dotenv", [False, True])
def test_subprocess_rejects_checkout_dotenv_before_host_import(tmp_path, local_dotenv):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    dotenv = checkout / ".env"
    credential = "synthetic-checkout-credential-2438"
    if local_dotenv:
        dotenv.write_text(f"OPENAI_API_KEY={credential}\n", encoding="utf-8")
    host_marker = tmp_path / "host-imported"
    package = checkout / "agent"
    package.mkdir()
    # An import marker makes refusal observable without installing or simulating Hermes.
    (package / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(host_marker)!r}).touch()\n", encoding="utf-8"
    )
    observer = tmp_path / "observer"
    observer.mkdir()
    observed = tmp_path / "observed.json"
    (observer / "sitecustomize.py").write_text(
        f"""import atexit
import json
import os
import sys
from pathlib import Path
state = {{"dotenv_opened": False, "credential_applied": False}}
def observe(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        if Path(os.fsdecode(args[0])).resolve() == Path({str(dotenv)!r}).resolve():
            state["dotenv_opened"] = True
    elif event == "os.putenv" and os.fsdecode(args[0]) == "OPENAI_API_KEY":
        if os.fsdecode(args[1]) == {credential!r}:
            state["credential_applied"] = True
sys.addaudithook(observe)
atexit.register(lambda: Path({str(observed)!r}).write_text(json.dumps(state), encoding="utf-8"))
""",
        encoding="utf-8",
    )
    home = tmp_path / "home"
    home.mkdir()
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "SYSTEMDRIVE"}
    }
    environment.update({
        "HOME": str(home),
        "USERPROFILE": str(home),
        "PYTHONPATH": os.pathsep.join(map(str, (observer, ROOT, checkout))),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    })
    output = tmp_path / "result.json"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "tests.integration_conformance.hermes.run_manager",
            str(tmp_path),
            str(output),
            str(checkout),
        ],
        cwd=home,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert result.returncode == 1
    assert report["real-manager"] == {
        "status": "failed",
        "executed": False,
        "failure_category": "checkout_local_config" if local_dotenv else "dependency_error",
    }
    assert host_marker.exists() is not local_dotenv
    assert json.loads(observed.read_text(encoding="utf-8")) == {
        "dotenv_opened": False,
        "credential_applied": False,
    }
    assert credential not in output.read_text(encoding="utf-8") + result.stdout + result.stderr
    if local_dotenv:
        assert dotenv.read_text(encoding="utf-8") == f"OPENAI_API_KEY={credential}\n"
