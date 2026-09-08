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

"""REL-1 regressions with real bwrap and a real dlopen, no model/DB service.

The small worker is an execution-root probe, not Datus/benchmark evidence.
The full native positive path remains in test_execution.py.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from powercontext_datus import execution, paired
from powercontext_datus.freeze import IntegrityError
from powercontext_datus.paired import freeze_plan, run_pair
from powercontext_datus.sandbox import Sandbox


@pytest.fixture
def probe(monkeypatch):
    # Host /tmp is replaced by the worker's private tmpfs. Put the execution
    # roots outside it, as for a provisioned runtime (plan inputs may stay there).
    with tempfile.TemporaryDirectory(prefix=".rel1-probe-", dir=Path.cwd()) as directory:
        yield make_probe(Path(directory), monkeypatch)


def make_probe(tmp_path, monkeypatch):
    if not shutil.which("bwrap"):
        pytest.skip("REL-1 OS probe requires Linux bubblewrap")
    # Resolve actual linked libm, then give the worker a private copy whose bytes
    # can be changed without modifying the host or another task's runtime.
    interpreter = Path(sys.executable).resolve()
    deps = execution.launcher_dependencies(interpreter)
    libm = next(path for path in deps if path.name.startswith("libm.so"))
    library_root = tmp_path / "system-libraries"
    library_root.mkdir()
    library = library_root / "libm.so.6"
    shutil.copyfile(libm.resolve(), library)
    # ctypes' extension dependency (libffi) must be visible in the probe too.
    import _ctypes

    if getattr(_ctypes, "__file__", None):
        deps += execution.launcher_dependencies(Path(_ctypes.__file__))
    mounts = (*(str(path) for path in sorted(set(deps))), str(library_root))
    monkeypatch.setattr(execution, "SYSTEM_MOUNTS", mounts)
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "bin/python").symlink_to(interpreter)
    (runtime / "pyvenv.cfg").write_text(f"home = {interpreter.parent}\ninclude-system-site-packages = false\n")
    bridge = tmp_path / "bridge"
    package = bridge / "powercontext_datus"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (package / "worker.py").write_text(
        "import ctypes, json, sys, time\n"
        "request = json.load(sys.stdin)\n"
        f"library = ctypes.CDLL({str(library)!r})\n"
        "library.cos.argtypes = [ctypes.c_double]\n"
        "library.cos.restype = ctypes.c_double\n"
        "print(json.dumps({'kind': 'effective_config', 'sequence': 1,\n"
        " 'monotonic_ns': time.monotonic_ns(),\n"
        " 'run_id': request['run_id'], 'task_id': request['task']['task_id'],\n"
        " 'attempt_id': request['attempt_id'], 'effective_sha256': 'execution-root-probe',\n"
        " 'loaded_library': library._name, 'cos_zero': library.cos(0.0)}))\n"
    )
    return Sandbox(runtime / "bin/python", bridge), library


@pytest.fixture
def frozen(probe, plan, tmp_path):
    sandbox, library = probe
    plan["public"]["model"]["base_url"] = "http://127.0.0.1:1/v1"
    manifest = freeze_plan(plan, sandbox)
    assert all(run["records"][0]["cos_zero"] == 1.0 for run in manifest["preparation"].values())
    assert all(run["records"][0]["loaded_library"] == str(library) for run in manifest["preparation"].values())
    (tmp_path / "frozen-manifest.json").write_text(json.dumps(manifest, indent=2))
    # Reload the immutable serialized boundary as a separate CLI run would.
    return json.loads(json.dumps(manifest))


@pytest.mark.parametrize("shadow_kind", ["script", "same_binary_symlink"])
def test_rel1_bwrap_shadow_rejected_before_launch(probe, frozen, tmp_path, monkeypatch, shadow_kind):
    sandbox, _ = probe
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    marker = tmp_path / "shadow-executed"
    if shadow_kind == "script":
        (shadow / "bwrap").write_text(f"#!/bin/sh\ntouch {marker}\necho '{{\"state_valid\": true}}'\n")
        (shadow / "bwrap").chmod(0o755)
    else:
        (shadow / "bwrap").symlink_to(frozen["inputs"]["execution"]["launcher"]["resolved"])
    monkeypatch.setenv("PATH", str(shadow) + os.pathsep + os.environ["PATH"])
    output = tmp_path / "shadow-output"
    with pytest.raises(IntegrityError, match="bubblewrap path/content drifted"):
        run_pair(frozen, Sandbox(sandbox.python, sandbox.bridge), output)
    assert not marker.exists() and not output.exists()


def test_rel1_same_path_bwrap_content_replacement(probe, plan, tmp_path, monkeypatch):
    sandbox, _ = probe
    launcher = tmp_path / "launcher"
    launcher.mkdir()
    binary = launcher / "bwrap"
    source = shutil.which("bwrap")
    assert source is not None
    shutil.copy2(source, binary)
    monkeypatch.setenv("PATH", str(launcher) + os.pathsep + os.environ["PATH"])
    plan["public"]["model"]["base_url"] = "http://127.0.0.1:1/v1"
    manifest = freeze_plan(plan, sandbox)
    binary.write_bytes(binary.read_bytes() + b"replacement")
    output = tmp_path / "replaced-output"
    with pytest.raises(IntegrityError, match="bubblewrap path/content drifted"):
        run_pair(manifest, sandbox, output)
    assert not output.exists()


def test_rel1_dynamic_library_change_rejected_before_launch(probe, frozen, tmp_path):
    sandbox, library = probe
    original_stat = library.stat()
    # Change real ELF bytes, retaining file size and mtime to defeat stat caches.
    data = bytearray(library.read_bytes())
    data[-1] ^= 1
    library.write_bytes(data)
    os.utime(library, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    output = tmp_path / "library-output"
    with pytest.raises(IntegrityError, match="host execution roots drifted"):
        run_pair(frozen, Sandbox(sandbox.python, sandbox.bridge), output)
    assert not output.exists()


def test_rel1_pinned_command_and_unchanged_pair(probe, frozen, plan, tmp_path, monkeypatch):
    sandbox, _ = probe
    pinned = sandbox.bind_execution(frozen["inputs"]["execution"])
    # An irrelevant PATH directory does not select a new executable. argv still
    # uses the frozen resolved entity, not the lexical symlink returned by which.
    monkeypatch.setenv("PATH", str(tmp_path / "empty") + os.pathsep + os.environ["PATH"])
    command = pinned.command(
        common=Path(plan["common_file"]), skills=Path(plan["arms"]["native"]["skill_root"]), cpuinfo_fd=0
    )
    assert command[0] == frozen["inputs"]["execution"]["launcher"]["resolved"]
    report = run_pair(frozen, sandbox, tmp_path / "unchanged")
    assert report["state_valid"]
    # A successful environment probe is not correct/grounded Datus QA evidence.
    assert not any(arm["accepted"] for arm in report["arms"].values())


@pytest.mark.parametrize(
    ("boundary", "position"),
    [("freeze", i) for i in range(2)]
    + [("before_case", i) for i in range(4)]
    + [("during_worker", 0)]
    + [("after_case", i) for i in range(4)]
    + [("final", 3)],
)
def test_rel1_root_drift_at_each_boundary_invalidates_pair(probe, plan, tmp_path, monkeypatch, boundary, position):
    sandbox, library = probe
    plan["public"]["model"]["base_url"] = "http://127.0.0.1:1/v1"
    plan["tasks"].append({"task_id": "fixture-2", "question": "Repeat the synthetic query."})
    oracle = Path(plan["oracle_file"])
    oracles = json.loads(oracle.read_text())
    oracles["fixture-2"] = oracles["fixture-1"]
    oracle.write_text(json.dumps(oracles))
    admission = Path(plan["admission_file"])
    approval = json.loads(admission.read_text())
    approval.update(roster_sha256=paired.digest_json(plan["tasks"]), oracle_sha256=paired.file_hash(oracle))
    admission.write_text(json.dumps(approval))
    manifest = {} if boundary == "freeze" else freeze_plan(plan, sandbox)
    original_run = paired.run_one
    original_write = paired.write_json
    original_process = subprocess.run
    original_library = library.read_bytes()
    starts = []

    def drift():
        library.write_bytes(library.read_bytes() + b"execution-root-drift")

    def run(*args, **kwargs):
        starts.append(args[3]["task_id"])
        result = original_run(*args, **kwargs)
        if boundary in {"freeze", "after_case"} and len(starts) == position + 1:
            drift()
        if boundary == "during_worker":
            library.write_bytes(original_library)
        return result

    def process(command, **kwargs):
        result = original_process(command, **kwargs)
        if boundary == "during_worker" and command[0] == manifest["inputs"]["execution"]["launcher"]["resolved"]:
            drift()
        return result

    def write(path, value):
        original_write(path, value)
        previous = "manifest.json" if position == 0 else f"case-{position - 1:04d}.json"
        if (boundary == "before_case" and path.name == previous) or (
            boundary == "final" and path.name == "case-0003.json"
        ):
            drift()

    monkeypatch.setattr(paired, "run_one", run)
    monkeypatch.setattr(paired, "write_json", write)
    monkeypatch.setattr(subprocess, "run", process)
    if boundary == "freeze":
        with pytest.raises(IntegrityError, match="host execution roots drifted"):
            freeze_plan(plan, sandbox)
        assert starts == ["prepare"] * (position + 1)
    else:
        output = tmp_path / boundary
        report = run_pair(manifest, sandbox, output)
        assert report["state_valid"] is False
        assert not any(arm["accepted"] for arm in report["arms"].values())
        assert (
            len(starts)
            == {"before_case": position, "during_worker": 1, "after_case": position + 1, "final": 4}[boundary]
        )
        assert not json.loads((output / "report.json").read_text())["state_valid"]
        verify_raw_retained(output, boundary)


def verify_raw_retained(output, boundary):
    if boundary == "during_worker":
        evidence = json.loads((output / "case-0000.json").read_text())
        assert evidence["control_failure"] == "leakage/state_drift"
        assert evidence["state_valid"] is False
        assert evidence["records"][0]["cos_zero"] == 1.0


def test_execution_tree_hashes_links_bytecode_modes_and_empty_directories(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    pyc = root / "cached.pyc"
    pyc.write_bytes(b"a")
    original = execution.tree_digest(root)
    pyc.write_bytes(b"b")
    assert execution.tree_digest(root) != original
    original = execution.tree_digest(root)
    (root / "empty").mkdir()
    assert execution.tree_digest(root) != original
    original = execution.tree_digest(root)
    pyc.chmod(0o400)
    assert execution.tree_digest(root) != original
    link = root / "link"
    link.symlink_to("missing")
    original = execution.tree_digest(root)
    link.unlink()
    link.symlink_to("different")
    assert execution.tree_digest(root) != original


def test_old_manifest_requires_refreeze(probe, plan, tmp_path):
    sandbox, _ = probe
    plan["public"]["model"]["base_url"] = "http://127.0.0.1:1/v1"
    with pytest.raises(IntegrityError, match="freeze again"):
        run_pair({"version": 1, "plan": plan, "inputs": {}}, sandbox, tmp_path / "old")
    assert not (tmp_path / "old").exists()


def test_system_python_cannot_reintroduce_untracked_broad_usr_mount(tmp_path):
    python = tmp_path / "python"
    python.symlink_to("/usr/bin/python3")
    with pytest.raises(IntegrityError, match="dedicated standalone"):
        Sandbox(python, tmp_path)


@pytest.mark.parametrize("existing", [False, True])
def test_rel1_loader_input_change_rejected_before_launch(probe, plan, tmp_path, monkeypatch, existing):
    sandbox, _ = probe
    loader = tmp_path / "ld.so.preload"
    if existing:
        loader.write_text("frozen loader input")
    monkeypatch.setattr(execution, "LOADER_INPUTS", (*execution.LOADER_INPUTS, str(loader)))
    plan["public"]["model"]["base_url"] = "http://127.0.0.1:1/v1"
    manifest = freeze_plan(plan, sandbox)
    loader.write_text("changed loader input")
    output = tmp_path / "loader-output"
    with pytest.raises(IntegrityError, match="host execution roots drifted"):
        run_pair(manifest, sandbox, output)
    assert not output.exists()
