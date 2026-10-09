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

"""Explicit maintenance of the database selected by Server configuration."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal

import typer
from sqlalchemy.exc import SQLAlchemyError

from powercontext.builtin.persistence.migrations import MigrationError, MigrationPlan, MigrationResult
from powercontext.builtin.persistence.migrations.deployment import MigrationRunner, deployment_runner
from powercontext.server.configuration import (
    ServerConfigurationError,
    resolve_server_environment_file,
    server_settings_context,
)
from powercontext.service.controller import ServiceController
from powercontext.service.model import ServiceError

BackupPolicy = Literal["auto", "manual", "skip"]
EnvFile = Annotated[Path | None, typer.Option(help="Read the same environment file as the deployed Server.")]
EvidenceDir = Annotated[
    Path | None,
    typer.Option(
        help="Persistent maintenance evidence directory; OceanBase Jobs must use the same directory on one fixed host."
    ),
]
LockCoordination = Annotated[
    Literal["single-host"] | None,
    typer.Option(help="OceanBase: run all migrators on one fixed host using the same evidence directory."),
]
ManageService = Annotated[
    bool,
    typer.Option(help="Request local service maintenance; unavailable for the current partial acceptance bundle."),
]
SharedDatabase = Annotated[
    bool,
    typer.Option(help="Declare other Servers share this database; coordinate all writers through deployment tooling."),
]

app = typer.Typer(
    name="db-migrate",
    context_settings={"help_option_names": ("-h", "--help")},
    help="Inspect and explicitly migrate the registered Phase A tables; this does not establish full Server readiness.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Manage schema revisions independently of installing software."""


@app.command()
def status(
    env_file: EnvFile = None, evidence_dir: EvidenceDir = None, lock_coordination: LockCoordination = None
) -> None:
    """Inspect the schema version without creating a database or control tables."""
    _inspect(env_file, evidence_dir=evidence_dir, lock_coordination=lock_coordination, verify=False)


@app.command()
def verify(
    env_file: EnvFile = None, evidence_dir: EvidenceDir = None, lock_coordination: LockCoordination = None
) -> None:
    """Verify the registered bundle's schema and data, not complete Server or cluster readiness."""
    _inspect(env_file, evidence_dir=evidence_dir, lock_coordination=lock_coordination, verify=True)


@app.command()
def plan(
    env_file: EnvFile = None,
    evidence_dir: EvidenceDir = None,
    lock_coordination: LockCoordination = None,
    backup: Annotated[BackupPolicy, typer.Option(help="Bind the selected backup policy to the plan.")] = "auto",
    manage_service: ManageService = False,
    shared_database: SharedDatabase = False,
) -> None:
    """Preview the target, resource digests, backup policy, and maintenance scope."""
    with _operator_errors(), server_settings_context(env_file=_environment_file(env_file)) as settings:
        runner = deployment_runner(settings, evidence_dir=evidence_dir, lock_coordination=lock_coordination)
        service = _service_scope(_environment_file(env_file)) if manage_service else None
        reviewed = runner.plan(backup_policy=backup, service=service, shared_database=shared_database)
        _write_payload(reviewed)


@app.command()
def apply(
    env_file: EnvFile = None,
    evidence_dir: EvidenceDir = None,
    lock_coordination: LockCoordination = None,
    acknowledge_previous_run: Annotated[
        str | None,
        typer.Option(
            help="OceanBase only: declare a DBA confirmed previous remote execution and background DDL ended; use its run token."
        ),
    ] = None,
    backup: Annotated[
        BackupPolicy | None,
        typer.Option(help="PC native backup, a user-declared manual backup, or explicit no-backup risk."),
    ] = None,
    plan_id: Annotated[str | None, typer.Option(help="The exact plan accepted by the operator.")] = None,
    yes: Annotated[
        bool, typer.Option("--yes", help="Accept the plan; does not imply backup or maintenance consent.")
    ] = False,
    maintenance_confirmed: Annotated[
        bool,
        typer.Option(
            help="Declare all writers stopped and accept coordinating every affected shared-database node upgrade."
        ),
    ] = False,
    backup_confirmed: Annotated[
        bool, typer.Option(help="Declare a manual backup exists; PC does not inspect or verify it.")
    ] = False,
    accept_no_backup: Annotated[
        bool, typer.Option(help="Explicitly accept that failure may make original data unrecoverable.")
    ] = False,
    backup_ref: Annotated[str | None, typer.Option(help="Optional reference for the operator's manual backup.")] = None,
    manage_service: ManageService = False,
    shared_database: SharedDatabase = False,
) -> None:
    """Review once and migrate the registered bundle after the operator stops all writers.

    Local service switching is unavailable for this partial acceptance bundle.
    """
    with _operator_errors(), server_settings_context(env_file=_environment_file(env_file)) as settings:
        runner = deployment_runner(
            settings,
            evidence_dir=evidence_dir,
            lock_coordination=lock_coordination,
            acknowledge_previous_run=acknowledge_previous_run,
        )
        policy: BackupPolicy = backup or "auto"
        initial = runner.plan(backup_policy=policy, shared_database=shared_database)
        _check_previous_run(initial, acknowledge_previous_run)
        if initial.state == "ready":
            _write_payload(runner.verify())
            return
        service = _service_scope(_environment_file(env_file)) if manage_service else None
        reviewed = runner.plan(backup_policy=policy, service=service, shared_database=shared_database)
        _check_previous_run(reviewed, acknowledge_previous_run)
        if policy == "auto" and not reviewed.backup_available and reviewed.state != "uninitialized":
            raise MigrationError(
                "backup_unsupported",
                "PC automatic backup is unavailable; select --backup manual or --backup skip and review a new plan.",
            )
        if yes:
            if backup is None or plan_id is None:
                raise MigrationError("confirmation_required", "Automation requires explicit --backup and --plan-id.")
        elif _interactive():
            _write_confirmation(reviewed, policy)
            if not typer.confirm("Accept this plan and the declarations above?", default=False):
                raise MigrationError("confirmation_required", "The plan was not accepted.")
            plan_id = reviewed.plan_id
            maintenance_confirmed = True
            backup_confirmed = policy == "manual"
            accept_no_backup = policy == "skip"
        else:
            raise MigrationError("confirmation_required", "Review a plan and provide --plan-id, --backup and --yes.")
        if plan_id != reviewed.plan_id:
            raise MigrationError("plan_changed", "The accepted plan does not match the current options or database.")
        _check_declarations(
            reviewed,
            maintenance_confirmed=maintenance_confirmed,
            backup_confirmed=backup_confirmed,
            accept_no_backup=accept_no_backup,
        )
        result = _apply_reviewed(
            runner,
            reviewed,
            maintenance_confirmed=maintenance_confirmed,
            backup_confirmed=backup_confirmed,
            accept_no_backup=accept_no_backup,
            backup_ref=backup_ref,
            env_file=_environment_file(env_file),
        )
        _write_payload(result)


def _inspect(
    env_file: Path | None,
    *,
    evidence_dir: Path | None,
    lock_coordination: Literal["single-host"] | None,
    verify: bool,
) -> None:
    with _operator_errors(), server_settings_context(env_file=_environment_file(env_file)) as settings:
        runner = deployment_runner(settings, evidence_dir=evidence_dir, lock_coordination=lock_coordination)
        result = runner.verify() if verify else runner.plan()
        _write_payload(result)


def _environment_file(env_file: Path | None) -> Path | None:
    return resolve_server_environment_file(env_file, discover=True)


def _interactive() -> bool:
    return sys.stdin.isatty()


def _write_payload(value: MigrationPlan | MigrationResult, *, indent: int | None = None) -> None:
    typer.echo(
        json.dumps(
            {**value.model_dump(mode="json"), "server_ready": False},
            indent=indent,
        )
    )


def _write_confirmation(reviewed: MigrationPlan, policy: BackupPolicy) -> None:
    _write_payload(reviewed, indent=2)
    typer.echo(
        "This acceptance bundle does not verify complete Server schema, task formats, indexes or startup readiness."
    )
    typer.echo("Maintenance pauses writes. Stop all other Servers, Workers, SDK clients and automatic restarts first.")
    typer.echo("PC cannot automatically discover every node or client connected to this database.")
    if reviewed.coordination and reviewed.coordination.get("mode") == "single-host":
        typer.echo(
            "All migration commands must use this fixed maintenance host and evidence directory. This is not a cross-host database lock."
        )
        if reviewed.coordination.get("pending_run_id"):
            typer.echo(
                "The supplied previous-run token declares a DBA confirmed all previous remote execution and background DDL ended. PC does not verify that declaration."
            )
    if reviewed.shared_database:
        typer.echo("You are responsible for upgrading every affected shared-database node before restoring traffic.")
    if reviewed.service is not None:
        typer.echo(
            "PC manages only the displayed local service; all other writers remain the operator's responsibility."
        )
    if policy == "auto":
        typer.echo("PC will create a native recovery point. Large databases may need considerable time and free space.")
    elif policy == "manual":
        typer.echo("By confirming, you declare that you have backed up the data. PC will not verify that backup.")
    else:
        typer.echo("By confirming, you accept that migration failure may make original data unrecoverable.")
    typer.echo("Old tables and recovery points are retained.")


def _service_scope(env_file: Path | None) -> dict[str, object]:
    del env_file
    raise MigrationError(
        "service_unsupported",
        "This partial acceptance bundle cannot establish complete Server readiness or switch a business service.",
    )


def _apply_reviewed(
    runner: MigrationRunner,
    reviewed: MigrationPlan,
    *,
    maintenance_confirmed: bool,
    backup_confirmed: bool,
    accept_no_backup: bool,
    backup_ref: str | None,
    env_file: Path | None,
) -> MigrationResult:
    def migrate() -> MigrationResult:
        return runner.apply(
            plan_id=reviewed.plan_id,
            accepted=True,
            maintenance_confirmed=maintenance_confirmed,
            backup_policy=reviewed.backup_policy,
            backup_confirmed=backup_confirmed,
            accept_no_backup=accept_no_backup,
            backup_ref=backup_ref,
            service=reviewed.service,
            shared_database=reviewed.shared_database,
        )

    if reviewed.service is None:
        return migrate()
    fingerprint = reviewed.service.get("fingerprint")
    if not isinstance(fingerprint, str):
        raise MigrationError("service_unsupported", "The local service does not provide a stable maintenance identity.")
    with ServiceController().maintenance(expected_fingerprint=fingerprint, env_file=env_file) as session:
        result = migrate()
        try:
            session.complete()
        except (ServiceError, OSError):
            _fail(
                "service_start_failed",
                "The database is ready but the service switch or startup failed; inspect service status and logs.",
                database_state="ready",
                service_state="failed",
                revision=result.revision,
            )
        return result


def _check_declarations(
    reviewed: MigrationPlan,
    *,
    maintenance_confirmed: bool,
    backup_confirmed: bool,
    accept_no_backup: bool,
) -> None:
    if not maintenance_confirmed:
        raise MigrationError("maintenance_required", "Stop all writers and provide --maintenance-confirmed.")
    if reviewed.state == "uninitialized":
        return
    if reviewed.backup_policy == "manual" and not backup_confirmed:
        raise MigrationError("confirmation_required", "Manual backup requires --backup-confirmed.")
    if reviewed.backup_policy == "skip" and not accept_no_backup:
        raise MigrationError("confirmation_required", "Skipping backup requires --accept-no-backup.")


def _check_previous_run(reviewed: MigrationPlan, acknowledged: str | None) -> None:
    pending = (reviewed.coordination or {}).get("pending_run_id")
    if pending is not None and acknowledged != pending:
        raise MigrationError(
            "recovery_required",
            "A DBA must confirm previous remote execution and background DDL ended; provide its exact --acknowledge-previous-run token and review a new plan.",
        )
    if pending is None and acknowledged is not None:
        raise MigrationError("stale_ack", "There is no pending run matching --acknowledge-previous-run.")


@contextmanager
def _operator_errors() -> Iterator[None]:
    try:
        yield
    except MigrationError as error:
        _fail(error.code, str(error))
    except ServerConfigurationError:
        _fail("configuration_invalid", "The Server configuration is invalid; check the environment file and settings.")
    except SQLAlchemyError:
        _fail("invalid_target", "The database cannot be inspected; check its format, access and availability.")
    except OSError:
        _fail("target_unavailable", "The maintenance target or its recovery storage cannot be accessed.")
    except ServiceError:
        _fail(
            "service_maintenance_failed", "The local service cannot enter maintenance; inspect service status and logs."
        )


def _fail(code: str, message: str, **details: object) -> None:
    typer.echo(json.dumps({"state": "blocked", "error": code, "message": message, **details}))
    raise typer.Exit(code=1)


__all__ = ["app"]
