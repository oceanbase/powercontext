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

"""Read back a native candidate through the real managed-resource consumer.

The cache is seeded with the exact candidate archive; this exercises archive,
catalog, relocation and installed imports without claiming a public download or
native service-manager/clean-machine qualification.
"""

# ruff: noqa: TRY003, S603 - fixed local artifact checks, no service registration

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx
from ci_ops_bundle_smoke import check as check_ops
from prepare_managed_resources import environment, sha256
from produce_managed_resources import _server_probe

from powercontext.ops.local_policy import new_configuration
from powercontext.ops.resources import ResourceCatalog, ResourceSpec, ResourceStore, verify
from powercontext.service.environment import protect_directory, write_protected_file

MAX_DIAGNOSTIC_BYTES = 64 * 1024


@contextmanager
def preserve_failure(evidence: Path, workspace: Path) -> Iterator[None]:
    """Keep bounded fixture logs before temporary workspace cleanup; suppress raw errors."""
    try:
        yield
    except Exception as error:
        saved = evidence / ("failure-" + uuid4().hex)
        saved.mkdir(parents=True)
        captured = []
        for name in ("stdout.log", "stderr.log"):
            source = workspace / "control" / name
            if source.is_file() and not source.is_symlink():
                with source.open("rb") as stream:
                    stream.seek(max(0, source.stat().st_size - MAX_DIAGNOSTIC_BYTES))
                    (saved / name).write_bytes(stream.read(MAX_DIAGNOSTIC_BYTES))
                captured.append(name)
        if isinstance(error, (subprocess.CalledProcessError, subprocess.TimeoutExpired)) and error.stderr:
            content = error.stderr.encode("utf-8") if isinstance(error.stderr, str) else error.stderr
            (saved / "subprocess-stderr.log").write_bytes(content[-MAX_DIAGNOSTIC_BYTES:])
            captured.append("subprocess-stderr.log")
        (saved / "failure.json").write_text(
            json.dumps(
                {"status": "candidate_smoke_failed", "exception_class": type(error).__name__, "fixture_logs": captured},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        raise RuntimeError(f"Candidate smoke failed; bounded diagnostics: {saved}") from None


def _serve(root: Path, spec: ResourceSpec, working: Path, env: dict[str, str]) -> dict[str, object]:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    data = working / "data"
    endpoint = f"http://127.0.0.1:{port}"
    configuration = new_configuration(data, port)
    identity = write_protected_file(working / "server.env", configuration)
    arguments = [
        str(root / spec.python_relative),
        "-I",
        "-B",
        "-m",
        "powercontext.service.launcher",
        "--endpoint",
        endpoint,
        "--data-dir",
        str(data),
        "--env-file",
        str(working / "server.env"),
        "--stdout",
        str(working / "stdout.log"),
        "--stderr",
        str(working / "stderr.log"),
    ]
    for name, value in dataclasses.asdict(identity).items():
        if name != "path" and value is not None:
            arguments.extend(["--env-file-" + name.replace("_", "-"), str(value)])
    with (working / "stdout.log").open("wb") as stdout, (working / "stderr.log").open("wb") as stderr:
        process = subprocess.Popen(arguments, cwd=working, env=env, stdout=stdout, stderr=stderr)
        try:
            deadline = time.monotonic() + 120
            with httpx.Client(trust_env=False, timeout=2) as client:
                while time.monotonic() < deadline:
                    if process.poll() is not None:
                        raise RuntimeError("Bundled service launcher exited before readiness; inspect the smoke logs")
                    try:
                        response = client.get(endpoint + "/health/ready")
                    except httpx.TransportError:
                        time.sleep(0.2)
                        continue
                    if response.status_code == 200:
                        return {"launcher": "passed", "readiness_status": response.json()["status"]}
                    time.sleep(0.2)
            raise RuntimeError("Bundled service launcher did not become ready within the smoke budget")
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def check(candidate: Path, *, serve: bool = False, evidence_dir: Path | None = None) -> dict[str, object]:
    candidate = candidate.resolve()
    evidence = evidence_dir.resolve() if evidence_dir else candidate.with_name(candidate.name + "-evidence")
    with (
        tempfile.TemporaryDirectory(prefix="pc-candidate-readback-") as temporary,
        preserve_failure(evidence, Path(temporary).resolve()),
    ):
        work = Path(temporary).resolve()
        catalog_digest = (candidate / "server/catalog.sha256").read_text(encoding="utf-8").strip()
        control = protect_directory(work / "control", create=True)
        path = control / "catalog.json"
        write_protected_file(path, (candidate / "server/catalog.json").read_bytes())
        catalog = ResourceCatalog.read(path, catalog_digest)
        spec = catalog.select_current()
        cache = protect_directory(work / "cache", create=True)
        shutil.copyfile(candidate / "server/server.tar.gz", cache / (spec.archive_sha256 + ".archive"))
        prepared = ResourceStore(cache).prepare(spec, work / "staging")
        verify(spec, prepared.directory)
        moved = work / "relocated server"
        prepared.directory.rename(moved)
        if os.name != "nt":
            moved.chmod(0o700)
        spec_values = {
            "platform": spec.platform,
            "architecture": spec.architecture,
            "python_version": spec.python_version,
            "python_relative": spec.python_console_relative,
            "server_python_relative": spec.python_relative,
        }
        poison = work / "poison"
        poison.mkdir()
        (poison / "powercontext.py").write_text("raise RuntimeError('checkout import leaked')\n", encoding="utf-8")
        env = {**environment(), "PATH": "", "PYTHONPATH": str(poison), "PYTHONHOME": str(poison / "missing")}
        _server_probe(moved, spec_values, spec.server_version, env)
        service = _serve(moved, spec, control, env) if serve else {"launcher": "help_only"}
        verify(spec, moved)
        check_ops(candidate / "ops")
        result = {
            "status": "candidate_smoke_passed",
            "qualified": False,
            "resource_digest": spec.digest,
            "archive_sha256": sha256(candidate / "server/server.tar.gz"),
            "catalog_sha256": catalog_digest,
            "server": service,
            "ops": "independent_inspection_passed",
            "http_download": "not_executed",
            "native_service_manager_lifecycle": "not_executed",
        }
        print(json.dumps(result, ensure_ascii=False))
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        help="Durable bounded failure logs; defaults to a sibling candidate-evidence directory",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Start the actual launcher with disposable SQLite data; never register a service",
    )
    args = parser.parse_args()
    check(args.candidate, serve=args.serve, evidence_dir=args.evidence_dir)


if __name__ == "__main__":
    main()
