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

"""Reproduce unsupported 1.3.0 upgrades on actual SQLite, embedded seekDB or OceanBase Servers.

This is a negative regression, never positive upgrade acceptance. CI retires
these assertions once the supported-path matrix becomes mandatory.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from ci_release_smoke import _available_port, _stop_process, _wait_until_ready
from upgrade_release_gate import ROOT, installed, load_policy, require, run, sha256, wheel_version


def console(python: Path) -> Path:
    return python.with_name("powercontext.exe" if os.name == "nt" else "powercontext")


def database_environment(backend: str, database: Path, environment: dict[str, str]) -> dict[str, str]:
    if backend == "seekdb":
        return {"POWERCONTEXT_SERVER_DATABASE_KIND": "seekdb", "POWERCONTEXT_SERVER_DATABASE_PATH": str(database)}
    if backend == "oceanbase":
        return {
            "POWERCONTEXT_SERVER_DATABASE_KIND": "oceanbase",
            "POWERCONTEXT_SERVER_DATABASE_URL": environment["PC_UPGRADE_OCEANBASE_URL"],
        }
    return {"POWERCONTEXT_SERVER_DATABASE_URL": f"sqlite+aiosqlite:///{database}"}


def source_database(python: Path, directory: Path, environment: dict[str, str], log: Path) -> dict[str, str | bool]:
    port = _available_port()
    base_url = f"http://127.0.0.1:{port}"
    with log.open("w") as stream:
        process = subprocess.Popen(  # noqa: S603 -- installed source CLI against a disposable database
            [str(console(python)), "server", "run", "--no-env-file", "--host", "127.0.0.1", "--port", str(port)],
            cwd=directory,
            env=environment,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
            text=True,
        )
        try:
            _wait_until_ready(base_url, process, 180)
            with httpx.Client(base_url=base_url, trust_env=False, timeout=30) as client:
                response = client.post(
                    "/v1/scopes",
                    json={
                        "title": "Upgrade fixture",
                        "summary": "Disposable old-release database for upgrade validation.",
                        "idempotency_key": "upgrade-fixture",
                    },
                )
                response.raise_for_status()
                scope_id = response.json()["scope_id"]
                response = client.post(
                    "/v1/memory/remember",
                    json={"scope_id": scope_id, "kind": "fact", "text": "Historical release migration fixture."},
                )
                response.raise_for_status()
                require(response.json().get("records"), "Old Server did not persist the fixture")
                response = client.post(
                    "/v1/memory/search", json={"scope_id": scope_id, "query": "historical migration", "mode": "fts"}
                )
                response.raise_for_status()
                require(response.json().get("hits"), "Old Server cannot retrieve its fixture")
                return {"scope_id": scope_id, "memory_readback": True}
        finally:
            _stop_process(process)


def rejected(
    command: list[str], environment: dict[str, str], directory: Path, log: Path, reasons: tuple[str, ...]
) -> None:
    with log.open("w", encoding="utf-8") as stream:
        process = subprocess.Popen(  # noqa: S603 -- installed target CLI, isolated data only
            command,
            env=environment,
            cwd=directory,
            stdout=stream,
            stderr=subprocess.STDOUT,
            text=True,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        try:
            process.wait(timeout=180)
        finally:
            _stop_process(process)
    output = log.read_text(encoding="utf-8")
    require(
        process.returncode != 0 and any(reason in output for reason in reasons),
        f"Expected rejection {reasons}; inspect {log.name}",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--backend", choices=("sqlite", "seekdb", "oceanbase"), default="sqlite")
    parser.add_argument(
        "--respect-policy",
        action="store_true",
        help="Retire rejection assertions once supported-path acceptance is required",
    )
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    if args.respect_policy and load_policy(ROOT)["mode"] == "supported-upgrades":
        (output / "result.json").write_text(
            json.dumps(
                {
                    "kind": "unsupported-upgrade-regression",
                    "backend": args.backend,
                    "status": "not-applicable",
                    "reason": "Supported upgrades are evaluated by the required acceptance gate.",
                },
                indent=2,
            )
            + "\n"
        )
        print("Historical rejection assertions retired; the supported upgrade matrix must pass instead.")
        return
    if args.backend == "oceanbase":
        url = urlsplit(os.environ.get("PC_UPGRADE_OCEANBASE_URL", ""))
        require(
            url.scheme == "mysql+aoceanbase"
            and url.hostname in {"localhost", "127.0.0.1"}
            and url.path == "/pc_upgrade_test",
            "OceanBase regression requires the dedicated local pc_upgrade_test database",
        )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("POWERCONTEXT_", "PYTEST_")) and key not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}
    }
    environment["NO_PROXY"] = environment["no_proxy"] = "localhost,127.0.0.1"
    # A short pathname avoids the embedded engine's Unix socket length limit.
    with tempfile.TemporaryDirectory(prefix="pc-up-", dir="/tmp" if os.name != "nt" else None) as temporary:  # noqa: S108 -- mkdtemp creates a private random directory
        directory = Path(temporary)
        interpreters = {}
        extras = "cli,server,seekdb" if args.backend == "seekdb" else "cli,server"
        for role, package in {"source": f"powercontext[{extras}]==1.3.0", "target": f"{wheel}[{extras}]"}.items():
            venv = directory / role
            run(
                ["uv", "venv", "--python", "3.12", str(venv)],
                environment=environment,
                cwd=directory,
                log=output / f"{role}-venv.log",
            )
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            run(
                ["uv", "pip", "install", "--python", str(python), package],
                environment=environment,
                cwd=directory,
                log=output / f"{role}-install.log",
            )
            interpreters[role] = python
        metadata = {role: installed(python, environment, directory) for role, python in interpreters.items()}
        require(metadata["source"]["powercontext"] == "1.3.0", "Wrong source package")
        require(metadata["target"]["powercontext"] == wheel_version(wheel), "Wrong target package")
        database = directory / "database"
        database_configuration = database_environment(args.backend, database, environment)
        environment.update({
            "POWERCONTEXT_HOME": str(directory / "home"),
            **database_configuration,
            "POWERCONTEXT_SERVER_ACCESS_MODE": "disabled",
        })
        probe = [
            "-I",
            str(ROOT / "scripts/upgrade_database_probe.py"),
            "--backend",
            args.backend,
            "--database",
            str(database),
        ]
        if args.backend == "oceanbase":
            run(
                [str(interpreters["source"]), *probe, "--output", str(output / "empty-schema.json")],
                environment=environment,
                cwd=directory,
                log=output / "empty-probe.log",
            )
            empty_schema = json.loads((output / "empty-schema.json").read_text())
            require(not empty_schema["tables"], "OceanBase test database must be empty; refusing to overwrite data")
            require(empty_schema["tenant_mode"] == "mysql", "OceanBase MySQL tenant required")
        seed = source_database(interpreters["source"], directory, environment, output / "source.log")
        for role, python in interpreters.items():
            run(
                [str(python), *probe, "--output", str(output / f"{role}-schema.json")],
                environment=environment,
                cwd=directory,
                log=output / f"{role}-probe.log",
            )
        schema = json.loads((output / "source-schema.json").read_text())
        target_schema = json.loads((output / "target-schema.json").read_text())
        tables = set(schema["tables"])
        require(
            {"pc_artifact_candidate_heads", "pc_artifact_candidate_versions", "pc_scopes", "pc_artifacts"} <= tables,
            "Source fixture lacks historical Server objects",
        )
        env_file = directory / "deployment.env"
        env_file.write_text("".join(f"{key}={value}\n" for key, value in database_configuration.items()))
        target = str(console(interpreters["target"]))
        status_command = [target, "server", "db-migrate", "status", "--env-file", str(env_file)]
        if args.backend == "oceanbase":
            status_command.extend(["--evidence-dir", str(output / "maintenance"), "--lock-coordination", "single-host"])
        rejected(
            status_command,
            environment,
            directory,
            output / "status.log",
            ("unknown_baseline", "incompatible_schema") if args.backend != "sqlite" else ("unknown_baseline",),
        )
        rejected(
            [target, "server", "run", "--no-env-file", "--host", "127.0.0.1", "--port", str(_available_port())],
            environment,
            directory,
            output / "legacy-startup.log",
            ("migration_required",),
        )
        # Only after both historical checks, mutate this disposable fixture into
        # a deliberately incomplete versioned state. This is not a migration.
        run(
            [str(interpreters["target"]), *probe, "--add-revision", "--output", str(output / "versioned-schema.json")],
            environment=environment,
            cwd=directory,
            log=output / "versioned-probe.log",
        )
        rejected(
            [target, "server", "run", "--no-env-file", "--host", "127.0.0.1", "--port", str(_available_port())],
            environment,
            directory,
            output / "versioned-startup.log",
            ("migration_framework_not_ready",),
        )
        evidence = {
            "kind": "unsupported-upgrade-regression",
            "backend": args.backend,
            "source_database": {key: value for key, value in schema.items() if key != "tables"},
            "target_database": {key: value for key, value in target_schema.items() if key != "tables"},
            "installed": metadata,
            "wheel_sha256": sha256(wheel),
            "seed": seed,
            "table_count": len(tables),
            "status": "passed",
        }
        (output / "result.json").write_text(json.dumps(evidence, indent=2) + "\n")
    print(f"Actual 1.3.0 {args.backend} database: all three unsupported-upgrade regressions reproduced.")


if __name__ == "__main__":
    main()
