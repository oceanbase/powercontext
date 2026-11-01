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

"""Private, versioned managed-Ops entry: admit a snapshot without starting Server."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from dataclasses import asdict
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast

from powercontext.builtin.persistence.migrations.models import MigrationError, digest

_MAX_REQUEST_BYTES = 16 * 1024
_MAX_EVIDENCE_BYTES = 4 * 1024 * 1024


def _recovery_fingerprint(database: Path) -> str:
    """Bind the same original-path journals that full Server admission consumes."""
    root = database.with_name(database.name + ".pc-migration-state")
    database_id = digest({"kind": "sqlite", "path": str(database)})
    paths = [root / "maintenance.json"] + [
        root / operation / database_id / "maintenance.json"
        for operation in ("processing_configuration", "atomic_projection")
    ]
    observations = []
    for path in paths:
        for parent in path.parents:
            if parent == root.parent:
                break
            try:
                item = parent.lstat()
            except FileNotFoundError:
                continue
            if not stat.S_ISDIR(item.st_mode) or getattr(item, "st_file_attributes", 0) & 0x400:
                raise MigrationError("recovery_required", "The maintenance location cannot be inspected safely.")
        try:
            item = path.lstat()
        except FileNotFoundError:
            observations.append(None)
            continue
        if (
            not stat.S_ISREG(item.st_mode)
            or getattr(item, "st_file_attributes", 0) & 0x400
            or item.st_size > _MAX_EVIDENCE_BYTES
        ):
            raise MigrationError("recovery_required", "The maintenance evidence cannot be inspected safely.")
        with path.open("rb") as source:
            before = os.fstat(source.fileno())
            content = source.read(_MAX_EVIDENCE_BYTES + 1)
            after = os.fstat(source.fileno())
        identities = [
            (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
            for value in (item, before, after, path.lstat())
        ]
        if len(content) > _MAX_EVIDENCE_BYTES or len(set(identities)) != 1:
            raise MigrationError("recovery_required", "The maintenance evidence changed during admission.")
        observations.append([identities[0], hashlib.sha256(content).hexdigest()])
    return digest(observations)


def _admit(request: object) -> dict[str, object]:
    from powercontext.builtin.persistence.migrations.deployment import deployment_runner
    from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
    from powercontext.server.configuration import server_settings_context
    from powercontext.service.environment import load_protected_environment_file
    from powercontext.service.model import EnvironmentFileIdentity

    if not isinstance(request, dict):
        raise MigrationError("admission_unsupported", "Unsupported admission request.")
    request = cast(dict[str, Any], request)
    if (
        set(request) != {"protocol_version", "target_version", "configuration", "data_dir", "database", "snapshot"}
        or type(request["protocol_version"]) is not int
        or request["protocol_version"] != 1
    ):
        raise MigrationError("admission_unsupported", "Unsupported admission request.")
    if version("powercontext") != request["target_version"]:
        raise MigrationError("target_version_mismatch", "The target package version differs from its resource.")
    identity = EnvironmentFileIdentity(**request["configuration"])
    loaded = load_protected_environment_file(Path(identity.path), expected=identity)
    database, snapshot, data_dir = (Path(request[key]) for key in ("database", "snapshot", "data_dir"))
    if (
        any(not path.is_absolute() for path in (database, snapshot, data_dir))
        or snapshot.resolve() == database.resolve()
    ):
        raise MigrationError("unsupported_target", "Admission requires a separate private snapshot.")
    with server_settings_context(environment=loaded.values, data_dir=data_dir) as settings:
        runner = deployment_runner(settings)
        if not isinstance(runner, SQLiteMigrationRunner) or runner.database != database.resolve():
            raise MigrationError("unsupported_target", "The configured database differs from the admission target.")
        before = _recovery_fingerprint(runner.database)
        plan = runner.admit_snapshot(snapshot)
        if _recovery_fingerprint(runner.database) != before:
            raise MigrationError("recovery_required", "The maintenance evidence changed during admission.")
    if asdict(load_protected_environment_file(Path(identity.path)).identity) != asdict(identity):
        raise MigrationError("configuration_changed", "The configuration changed during admission.")
    return {
        "state": "ready",
        "reason": "server_ready",
        "fingerprint": digest({"plan": plan.plan_id, "evidence": before}),
    }


def main() -> int:
    """Read one bounded private request and return safe codes, never exception values."""
    from powercontext.server.configuration import ServerConfigurationError

    try:
        content = sys.stdin.buffer.read(_MAX_REQUEST_BYTES + 1)
        if len(content) > _MAX_REQUEST_BYTES:
            raise ValueError  # noqa: TRY301
        result = _admit(json.loads(content))
    except MigrationError as error:
        result = {"state": "rejected", "reason": error.code}
    except ServerConfigurationError:
        result = {"state": "rejected", "reason": "configuration_invalid"}
    except Exception:
        # Import, dependency, malformed-input and unsupported-target failures
        # must not expose configuration, database contents or provider messages.
        result = {"state": "rejected", "reason": "admission_unsupported"}
    sys.stdout.write(json.dumps({"protocol_version": 1, **result}, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
