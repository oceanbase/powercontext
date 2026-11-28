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

"""Private, append-only audit evidence survives interruption between model calls."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from .manifests import PACKAGE_ROOT, EvaluationError
from .models import Sample, canonical, digest


def _write_new(path: Path, data: bytes) -> None:
    with path.open("xb") as stream:
        path.chmod(0o600)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


class PrivateAuditJournal:
    def __init__(self, directory: Path, manifest: dict):
        self.directory = directory
        self.manifest = manifest

    def __enter__(self):
        repository = next((parent for parent in PACKAGE_ROOT.parents if (parent / ".git").exists()), None)
        if repository is not None and self.directory.resolve().is_relative_to(repository):
            raise EvaluationError("private_audit_directory_must_be_outside_repository")
        self.directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        _write_new(self.directory / "run-manifest.json", canonical(self.manifest) + b"\n")
        _write_new(self.directory / "events.jsonl", b"")
        self._state("running")
        return self

    def _state(self, status: str) -> None:
        temporary = self.directory / "STATE.tmp"
        _write_new(
            temporary,
            canonical({"schema": "evidence-admission.private-state.v1", "status": status}) + b"\n",
        )
        temporary.replace(self.directory / "STATE.json")

    def _append(self, event: dict) -> None:
        with (self.directory / "events.jsonl").open("ab") as stream:
            stream.write(canonical(event) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())

    def record_output(self, output: dict) -> None:
        self._append({"kind": "output", **output})

    def record_sample(self, sample: Sample) -> None:
        self._append({"kind": "sample", "sample": sample.model_dump(mode="json")})

    def finalize_outputs(self) -> None:
        # Build the audit reader's complete-run format without retaining every raw output in memory.
        temporary = self.directory / "outputs.tmp"
        with temporary.open("xb") as stream, (self.directory / "events.jsonl").open("rb") as events:
            temporary.chmod(0o600)
            header = {"schema": "evidence-admission.private-outputs.v1", "manifest_hash": digest(self.manifest)}
            stream.write(canonical(header)[:-1] + b',"outputs":[')
            separator = b""
            for line in events:
                event = json.loads(line)
                if event.pop("kind") == "output":
                    stream.write(separator + canonical(event))
                    separator = b","
            stream.write(b"]}\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.directory / "outputs.json")

    def __exit__(self, exc_type, exc, traceback):
        status = "complete" if exc_type is None else "failed"
        if isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt)):
            status = "cancelled"
        try:
            self._state(status)
        except OSError:
            if exc_type is None:
                raise
        return False
