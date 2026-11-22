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

"""Private, one-shot Intel Ops startup observation; never a release qualification."""

# ruff: noqa: S101, S607, C901 - private diagnostic assertions and standard Popen signature

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import produce_managed_resources
from typing_extensions import override

SOURCE = "aa8435fda29c0b0f58512e71a1cf52ae881e137e"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--original-inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    args = parser.parse_args()
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    original = json.loads(args.original_inputs.read_bytes())
    inputs = args.inputs.resolve()
    current = json.loads((inputs / "inputs.json").read_bytes())
    assert (platform.system(), platform.machine()) == ("Darwin", "x86_64")
    assert original["source"]["commit"] == current["source"]["commit"] == SOURCE
    equal_fields = (
        "target",
        "server_version",
        "interpreter",
        "interpreter_lock_sha256",
        "source",
        "build_tools",
        "upstream_artifacts",
        "upstream_requirements_sha256",
        "normalized_runtime_aliases",
        "intel_build_pins_sha256",
    )
    comparisons = {key: original[key] == current[key] for key in equal_fields}
    comparisons["producer_sources"] = original["preparer"]["files_sha256"] == current["preparer"]["files_sha256"]
    old_wheels = {item["name"]: item for item in original["wheels"]}
    wheels = {item["name"]: item for item in current["wheels"]}
    comparisons["wheel_names_versions"] = {name: item["version"] for name, item in old_wheels.items()} == {
        name: item["version"] for name, item in wheels.items()
    }
    changed_wheels = [name for name in wheels if wheels[name]["sha256"] != old_wheels[name]["sha256"]]
    # Locally compiled wheels and the source wheel are rebuilt on this new host.
    comparisons["prebuilt_wheel_bytes"] = set(changed_wheels) <= {"cryptography", "jieba", "powercontext"}
    write(
        evidence / "identity.json",
        {
            "source": SOURCE,
            "diagnostic_revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "host": {"system": platform.system(), "machine": platform.machine(), "release": platform.release()},
            "parent_python": platform.python_version(),
            "original_inputs_sha256": digest(args.original_inputs),
            "new_inputs_sha256": digest(inputs / "inputs.json"),
            "comparisons": comparisons,
            "rebuilt_wheels_with_changed_bytes": changed_wheels,
            "boundary": "Rebuilt pinned inputs, not the historical failed runtime; observation adds importtime and phase hooks. Original help subprocess timeout stays 60 seconds. No qualification or deterministic-output claim.",
        },
    )
    assert all(comparisons.values()), comparisons

    def event(stage: str, **details: object) -> None:
        with (evidence / "parent.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps({
                    "utc": datetime.now(UTC).isoformat(),
                    "monotonic": time.monotonic(),
                    "stage": stage,
                    **details,
                })
                + "\n"
            )

    original_popen = subprocess.Popen
    observations = []

    class ObservedPopen(original_popen[Any]):
        def __init__(self, command, *positional, **kwargs):
            self.observed = (
                isinstance(command, list)
                and len(command) == 6
                and command[1:4] == ["-I", "-S", "-B"]
                and Path(command[4]).name == "entry.py"
                and command[5] == "--help"
            )
            if self.observed:
                assert not observations, "Only one help process is authorized"
                observations.append(True)
                entry = Path(command[4])
                original_entry = entry.read_text(encoding="utf-8")
                (evidence / "entry-original.py").write_text(original_entry, encoding="utf-8")
                phase_path = str(evidence / "child.jsonl")
                prefix = f"""import atexit as _atexit, faulthandler as _fault, json as _json, os as _os, time as _time
from datetime import datetime as _datetime, timezone as _timezone

def _phase(stage, **details):
    with open({phase_path!r}, "a", encoding="utf-8") as _stream:
        _stream.write(_json.dumps(dict(utc=_datetime.now(_timezone.utc).isoformat(), monotonic=_time.monotonic(), pid=_os.getpid(), stage=stage, **details)) + "\\n")

_phase("entry.start")
_atexit.register(lambda: _phase("atexit.final_observer"))
_fault.enable()
_fault.dump_traceback_later(45)
"""
                needle = 'runpy.run_module("powercontext.ops", run_name="__main__")'
                assert original_entry.count(needle) == 1
                replacement = """_phase("runpy.start")
try:
    runpy.run_module("powercontext.ops", run_name="__main__")
except BaseException as _error:
    _phase("runpy.exception", type=type(_error).__name__, code=getattr(_error, "code", None))
    raise
finally:
    _phase("runpy.finally")
"""
                entry.write_text(prefix + original_entry.replace(needle, replacement), encoding="utf-8")
                (evidence / "entry-observed.py").write_text(entry.read_text(), encoding="utf-8")
                command = [command[0], "-X", "importtime", *command[1:]]
                event("help.spawn.begin", timeout_seconds=60)
            super().__init__(command, *positional, **kwargs)
            if self.observed:
                event("help.spawn.returned", pid=self.pid)

        @override
        def communicate(self, input=None, timeout=None):
            if not self.observed:
                return super().communicate(input=input, timeout=timeout)
            assert timeout == 60
            event("help.communicate.begin", pid=self.pid)
            try:
                stdout, stderr = super().communicate(input=input, timeout=timeout)
            except subprocess.TimeoutExpired as error:
                for name, content in (("stdout", error.output), ("stderr", error.stderr)):
                    (evidence / ("help." + name)).write_bytes(
                        content.encode() if isinstance(content, str) else content or b""
                    )
                event("help.timeout", pid=self.pid, observed_returncode=self.poll())
                raise
            else:
                for name, content in (("stdout", stdout), ("stderr", stderr)):
                    (evidence / ("help." + name)).write_bytes(
                        content.encode() if isinstance(content, str) else content or b""
                    )
                event("help.communicate.returned", pid=self.pid, returncode=self.returncode)
                return stdout, stderr

        @override
        def wait(self, timeout=None):
            result = super().wait(timeout=timeout)
            if self.observed:
                event("help.wait.returned", pid=self.pid, returncode=result)
            return result

        @override
        def kill(self):
            if self.observed:
                event("help.kill", pid=self.pid)
            return super().kill()

    event("producer.start")
    subprocess.Popen = ObservedPopen  # ty: ignore[invalid-assignment] - bounded private observation
    try:
        produce_managed_resources.produce(
            argparse.Namespace(
                inputs=inputs,
                inputs_sha256=digest(inputs / "inputs.json"),
                output=args.output.resolve(),
                server_url="https://candidate.invalid/server.tar.gz",
                uv="uv",
            )
        )
    except BaseException:
        (evidence / "exception.txt").write_text(traceback.format_exc(), encoding="utf-8")
        event("producer.failed", help_processes=len(observations))
        raise
    else:
        event("producer.succeeded", help_processes=len(observations))
        assert len(observations) == 1
    finally:
        subprocess.Popen = original_popen


if __name__ == "__main__":
    main()
