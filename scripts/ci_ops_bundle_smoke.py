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

"""Exercise a real copied Ops bundle without a system or managed Server Python.

Only isolated fixture files are written. Native service inspection is read-only;
this check does not establish Task Scheduler/LaunchAgent lifecycle acceptance.
"""

# ruff: noqa: TRY003 - acceptance failures describe their observable boundary

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

_PREPARE = """
import json, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from powercontext.service.environment import protect_directory, write_protected_file
import powercontext.ops.runtime
root = Path(sys.argv[2])
bundle = Path(sys.argv[3]).resolve()
module_files = [Path(module.__file__).resolve() for module in list(sys.modules.values())
                if getattr(module, "__file__", None) and not module.__file__.startswith("<")]
outside = [str(path) for path in module_files if not path.is_relative_to(bundle)]
if outside:
    raise RuntimeError("Imports escaped the Ops bundle: " + repr(outside))
protect_directory(root, create=True)
config = root / "server.env"
write_protected_file(config, b"POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1\\n")
binding = dict(format_version=1, target_id="bundle-smoke", package_version="0.0.0",
               python_executable=str(root / "missing-server" / "python.exe"),
               env_file=str(config), configuration_owned=False)
write_protected_file(root / "target.json", json.dumps(binding).encode("utf-8"))
print(json.dumps(dict(audited_module_files=len(module_files), outside_bundle=outside)))
"""


def check(bundle: Path) -> None:
    bundle = bundle.resolve()
    manifest = json.loads((bundle / "bundle.json").read_text(encoding="utf-8"))
    python = bundle / manifest["python_relative"]
    if not python.resolve().is_relative_to(bundle):
        raise ValueError("Bundle interpreter escapes the selected artifact")
    with tempfile.TemporaryDirectory(prefix="powercontext-ops-smoke-") as temporary:
        root = Path(temporary).resolve()
        fixture = root / "fixture"
        poison = root / "untrusted"
        poison.mkdir()
        (poison / "powercontext.py").write_text("raise RuntimeError('untrusted import')\n", encoding="utf-8")
        environment = {
            key: value for key, value in os.environ.items() if not key.upper().startswith(("PYTHON", "POWERCONTEXT"))
        }
        environment.update({
            "PATH": "",
            "PYTHONPATH": str(poison),
            "PYTHONHOME": str(root / "missing-python-home"),
            "POWERCONTEXT_HOME": str(root / "data"),
        })
        invocation = [str(python), "-I", "-S", "-B", str(bundle / "entry.py")]
        subprocess.run(  # noqa: S603 - explicitly selected trusted local artifact
            [*invocation, "--help"],
            cwd=poison,
            env=environment,
            check=True,
            capture_output=True,
            timeout=60,
        )
        args = [*invocation, "--target-file", str(fixture / "target.json"), "--state-dir", str(fixture / "state")]
        invalid = subprocess.run(  # noqa: S603 - fixed read-only protocol rejection
            args,
            input=json.dumps({"protocol_version": 999, "request_id": "invalid", "action": "inspect"}) + "\n",
            cwd=poison,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
        rejection = json.loads(invalid.stdout)
        if invalid.returncode == 0 or rejection.get("error", {}).get("code") != "unsupported_protocol":
            raise RuntimeError("Unsupported protocol was not rejected through the independent entry")
        if (fixture / "state").exists():
            raise RuntimeError("Rejected input created operation state")
        # Native managers and ACL protection need OS utilities, never a developer Python or uv.
        environment["PATH"] = str(Path(os.environ["SYSTEMROOT"]) / "System32") if os.name == "nt" else "/usr/bin:/bin"
        prepared = subprocess.run(  # noqa: S603 - creates only private disposable fixtures through bundled protection code
            [str(python), "-I", "-S", "-B", "-c", _PREPARE, str(bundle / "packages"), str(fixture), str(bundle)],
            cwd=poison,
            env=environment,
            check=True,
            capture_output=True,
            timeout=60,
        )
        inspected = subprocess.run(  # noqa: S603 - actual read-only inspection, never register/start/stop/remove
            args,
            input=json.dumps({"protocol_version": 1, "request_id": "inspect", "action": "inspect"}) + "\n",
            cwd=poison,
            env=environment,
            check=False,
            capture_output=True,
            text=True,
            timeout=90,
        )
        result = json.loads(inspected.stdout)
        if inspected.returncode != 0 or result.get("outcome") != "succeeded":
            raise RuntimeError(f"Independent inspection failed with missing Server Python: {result}")
        if result.get("components", {}).get("support") != "supported":
            raise RuntimeError(f"The bundle could not inspect the native service manager on this host: {result}")
        if result.get("components", {}).get("readiness") != "not_probed":
            raise RuntimeError("Read-only inspection did not preserve unprobed readiness")
        if (fixture / "state").exists():
            raise RuntimeError("Read-only inspection created operation state")
        print(
            json.dumps({
                "bundle": str(bundle),
                "python": manifest["python_version"],
                "imports": json.loads(prepared.stdout),
                "inspection": result,
            })
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    check(parser.parse_args().bundle)


if __name__ == "__main__":
    main()
