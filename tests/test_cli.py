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

import json
import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from types import TracebackType
from typing import Self
from unittest.mock import Mock

import pytest
from click import unstyle
from pydantic import ValidationError
from typer.testing import CliRunner

import powercontext.client.cli as client_cli
from powercontext.cli.app import create_cli
from powercontext.client import ServerResponseError
from powercontext.client.receiver_service import ReceiverServiceInstallation
from powercontext.client.settings import ClientSettings
from powercontext.client.skill_receiver import ReceiverSyncResult, RemoteSkillReceiverConfig
from powercontext.http import (
    ArtifactReference,
    EnrollRemoteSkillTargetRequest,
    ExperienceProposal,
    ExternalSkillImportMode,
    GeneratedCandidateResponse,
    GeneratedCandidateStatus,
    GenerateExperienceRequest,
    GenerateSkillRequest,
    GetSkillRequest,
    GetStatsRequest,
    HealthResponse,
    ImportExternalSkillRequest,
    ListRemoteSkillTargetsRequest,
    ListRemoteSkillTargetsResponse,
    PublishRemoteSkillRequest,
    ReadinessResponse,
    RemoteAgentKind,
    RemoteSkillPublication,
    RemoteSkillTarget,
    RemoteSkillTargetCredential,
    ReviseArtifactCandidateRequest,
    RevokeRemoteSkillTargetRequest,
    ScopedStats,
    SkillArtifact,
    SkillGenerationOrigin,
    SkillProposal,
    SkillValidationItem,
    UnpublishRemoteSkillRequest,
)
from powercontext.server.cli import app as server_app


def _empty_inventory() -> dict[str, object]:
    return {
        "sources": {"total": 0, "memory_processed": 0, "memory_pending": 0},
        "artifacts": {"total": 0, "by_family": []},
        "candidates": {"total": 0, "pending": 0, "approved": 0, "rejected": 0, "by_family": []},
        "memory": {"entries": {"total": 0, "active": 0, "inactive": 0, "by_kind": []}},
    }


def _stats_response() -> ScopedStats:
    inventory = _empty_inventory()
    usage = {
        "period": {
            "preset": "today",
            "start_date": "2026-08-04",
            "end_date": "2026-08-04",
            "timezone": "UTC",
        },
        "totals": {
            "generation": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
            "embedding": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
        },
        "by_purpose": [],
        "daily": [
            {
                "date": "2026-08-04",
                "generation": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
                "embedding": {"requests": 0, "input_tokens": 0, "output_tokens": 0},
                "by_purpose": [],
            }
        ],
    }
    recall = {
        "period": {
            "preset": "today",
            "start_date": "2026-08-04",
            "end_date": "2026-08-04",
            "timezone": "UTC",
        },
        "estimator": {"estimator_id": "character:weighted", "version": "1"},
        "totals": {
            "preparations": 3,
            "ready_preparations": 2,
            "comparable_preparations": 1,
            "baseline_tokens": 100,
            "recalled_tokens": 40,
            "token_reduction": 60,
        },
        "daily": [
            {
                "date": "2026-08-04",
                "preparations": 3,
                "ready_preparations": 2,
                "comparable_preparations": 1,
                "baseline_tokens": 100,
                "recalled_tokens": 40,
                "token_reduction": 60,
            }
        ],
    }
    return ScopedStats.model_validate({
        "selection": {"mode": "exact", "scope_ids": ["project"]},
        "scope_ids": ["project"],
        "as_of": "2026-08-04T12:00:00Z",
        "inventory": inventory,
        "usage": usage,
        "recall": recall,
        "by_scope": [{"scope_id": "project", "inventory": inventory, "usage": usage, "recall": recall}],
    })


@pytest.mark.parametrize(
    "arguments",
    [
        ["-h"],
        ["--help"],
        ["experience", "--help"],
        ["skill", "--help"],
        ["external-skill", "--help"],
    ],
)
def test_cli_help_exits_successfully(arguments: list[str]) -> None:
    cli = create_cli([server_app])

    result = CliRunner().invoke(cli, arguments)

    assert result.exit_code == 0


def test_skill_cli_exposes_the_target_based_export_command() -> None:
    cli = create_cli([])
    runner = CliRunner()

    skill_help = runner.invoke(cli, ["skill", "--help"])
    export_help = runner.invoke(cli, ["skill", "export", "--help"])

    assert skill_help.exit_code == 0
    assert "export" in unstyle(skill_help.output)
    assert export_help.exit_code == 0
    export_help_text = unstyle(export_help.output)
    assert "--target" in export_help_text
    assert "codex" in export_help_text


def test_skill_cli_exposes_complete_remote_distribution_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(create_cli([]), ["skill", "--help"])
    enrollment_help = runner.invoke(create_cli([]), ["skill", "remote-enroll", "--help"])

    assert result.exit_code == 0
    assert enrollment_help.exit_code == 0
    help_text = unstyle(result.output)
    assert all(
        command in help_text
        for command in (
            "remote-status",
            "remote-target-create",
            "remote-target-rename",
            "remote-target-revoke",
            "remote-enroll",
            "remote-publish",
            "remote-service-install",
            "remote-service-uninstall",
            "remote-unpublish",
            "remote-sync",
            "remote-watch",
        )
    )
    assert "--install-service" in unstyle(enrollment_help.output)
    assert "--allow-insecure-http" in unstyle(enrollment_help.output)


def test_remote_service_install_uses_enrolled_receiver_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_file = tmp_path / "remote-skill-target.json"
    config = Mock(target_id="codex-a")
    unit = ReceiverServiceInstallation(
        unit_name="powercontext-skill-receiver-codex-a.service",
        unit_path=tmp_path / "powercontext-skill-receiver-codex-a.service",
    )
    received: list[tuple[Path, object, float]] = []
    monkeypatch.setattr(client_cli, "_read_receiver_config", lambda path: config if path == config_file else None)
    monkeypatch.setattr(
        client_cli,
        "install_systemd_user_service",
        lambda path, value, *, interval_seconds: received.append((path, value, interval_seconds)) or unit,
    )

    result = CliRunner().invoke(
        create_cli([]),
        ["skill", "remote-service-install", "--config-file", str(config_file), "--interval", "3"],
    )

    assert result.exit_code == 0
    assert received == [(config_file, config, 3)]
    assert unit.unit_name in result.output


def test_remote_enroll_can_install_automatic_service_in_one_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enrolled = RemoteSkillTargetCredential(
        scope_id="project",
        target_id="codex-a",
        agent_kind=RemoteAgentKind.CODEX,
        credential="pct_target.super-secret-target-value",
    )
    unit = ReceiverServiceInstallation(
        unit_name="powercontext-skill-receiver-codex-a.service",
        unit_path=tmp_path / "powercontext-skill-receiver-codex-a.service",
    )
    installed: list[tuple[Path, RemoteSkillReceiverConfig, float]] = []
    enrollment_requests: list[EnrollRemoteSkillTargetRequest] = []

    class EnrollmentClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def enroll_remote_skill_target(
            self,
            request: EnrollRemoteSkillTargetRequest,
        ) -> RemoteSkillTargetCredential:
            enrollment_requests.append(request)
            return enrolled

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: EnrollmentClient())
    monkeypatch.setattr(client_cli.socket, "gethostname", lambda: "build-host-01")
    monkeypatch.setattr(
        client_cli,
        "install_systemd_user_service",
        lambda path, config, *, interval_seconds: installed.append((path, config, interval_seconds)) or unit,
    )
    workspace = tmp_path / "project"

    result = CliRunner().invoke(
        create_cli([]),
        [
            "--server-url",
            "http://127.0.0.1:8765",
            "skill",
            "remote-enroll",
            "--workspace",
            str(workspace),
            "--enrollment-code",
            "e" * 32,
            "--install-service",
            "--watch-interval",
            "3",
        ],
    )

    config_file = workspace / ".powercontext/remote-skill-target.json"
    assert result.exit_code == 0
    assert config_file.stat().st_mode & 0o777 == 0o600
    assert len(installed) == 1
    assert installed[0][0] == config_file
    assert installed[0][1].target_id == enrolled.target_id
    assert installed[0][2] == 3
    assert len(enrollment_requests) == 1
    assert enrollment_requests[0].machine_hostname == "build-host-01"
    assert enrollment_requests[0].workspace_name == "project"
    assert unit.unit_name in result.output


def test_remote_enroll_requires_and_persists_explicit_cleartext_http_permission(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enrolled = RemoteSkillTargetCredential(
        scope_id="project",
        target_id="codex-a",
        agent_kind=RemoteAgentKind.CODEX,
        credential="pct_target.super-secret-target-value",
    )
    enrollment_calls = 0
    client_options: list[dict[str, object]] = []

    class EnrollmentClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def enroll_remote_skill_target(self, _request: object) -> RemoteSkillTargetCredential:
            nonlocal enrollment_calls
            enrollment_calls += 1
            return enrolled

    def enrollment_client(*_args: object, **kwargs: object) -> EnrollmentClient:
        client_options.append(kwargs)
        return EnrollmentClient()

    monkeypatch.setattr(client_cli, "PowerContextClient", enrollment_client)
    runner = CliRunner()
    workspace = tmp_path / "project"
    arguments = [
        "--server-url",
        "http://11.162.218.22:8765",
        "skill",
        "remote-enroll",
        "--workspace",
        str(workspace),
        "--enrollment-code",
        "e" * 32,
    ]

    rejected = runner.invoke(create_cli([]), arguments)

    assert rejected.exit_code == 2
    assert "requires HTTPS" in rejected.output
    assert enrollment_calls == 0

    accepted = runner.invoke(create_cli([]), [*arguments, "--allow-insecure-http"])

    config = json.loads((workspace / ".powercontext/remote-skill-target.json").read_text(encoding="utf-8"))
    assert accepted.exit_code == 0
    assert enrollment_calls == 1
    assert client_options == [{"timeout": 10.0, "allow_insecure_http": True}]
    assert config["allow_insecure_http"] is True
    assert "WARNING" in accepted.output


def test_remote_enroll_does_not_consume_code_when_credential_destination_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enrollment_calls = 0

    class EnrollmentClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def enroll_remote_skill_target(self, _request: object) -> None:
            nonlocal enrollment_calls
            enrollment_calls += 1

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: EnrollmentClient())
    workspace = tmp_path / "project"
    destination = workspace / ".powercontext/remote-skill-target.json"
    destination.parent.mkdir(parents=True)
    destination.write_text("existing credential\n", encoding="utf-8")

    result = CliRunner().invoke(
        create_cli([]),
        [
            "--server-url",
            "http://127.0.0.1:8765",
            "skill",
            "remote-enroll",
            "--workspace",
            str(workspace),
            "--enrollment-code",
            "e" * 32,
        ],
    )

    assert result.exit_code == 2
    assert enrollment_calls == 0
    assert destination.read_text(encoding="utf-8") == "existing credential\n"


@pytest.mark.parametrize(
    ("arguments", "sync_result", "expected_output"),
    (
        (
            ["skill", "remote-sync"],
            ReceiverSyncResult(requested=1, succeeded=0, failed=1, receipt_pending=0),
            "0 succeeded, 1 failed",
        ),
        (
            ["--json", "skill", "remote-sync"],
            ReceiverSyncResult(requested=1, succeeded=1, failed=0, receipt_pending=1),
            '"receipt_pending": 1',
        ),
        (
            ["skill", "remote-sync"],
            ReceiverSyncResult(requested=0, succeeded=0, failed=0, receipt_pending=1),
            "1 Receipts pending (0 actions)",
        ),
    ),
)
def test_remote_sync_exits_nonzero_when_convergence_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
    sync_result: ReceiverSyncResult,
    expected_output: str,
) -> None:
    class IncompleteReceiver:
        def __init__(self, _config: object) -> None:
            pass

        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def sync(self) -> ReceiverSyncResult:
            return sync_result

    monkeypatch.setattr(client_cli, "_read_receiver_config", lambda _path: Mock())
    monkeypatch.setattr(client_cli, "RemoteSkillReceiver", IncompleteReceiver)

    result = CliRunner().invoke(create_cli([]), arguments)

    assert result.exit_code == 1
    assert expected_output in result.output


def test_remote_distribution_cli_resolves_cas_generations_and_prints_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = RemoteSkillTarget.model_validate({
        "scope_id": "project",
        "target_id": "codex-a",
        "display_name": "Hangzhou build machine",
        "agent_kind": "codex",
        "installation_scope": "project",
        "delivery_mode": "agent_pull",
        "installation_id": "workspace-a",
        "state": "active",
        "receiver_version": "0.1.0",
        "environment_fingerprint": None,
        "machine_hostname": "build-host-01",
        "workspace_name": "powercontext",
        "last_seen_at": "2026-08-24T12:00:00Z",
        "generation": 3,
    })
    publication = RemoteSkillPublication.model_validate({
        "scope_id": "project",
        "target_id": "codex-a",
        "artifact_id": "release-check",
        "desired_state": "published",
        "desired_revision": 1,
        "desired_tree_digest": "a" * 64,
        "observed_revision": 1,
        "observed_tree_digest": "a" * 64,
        "observed_generation": 7,
        "state": "current",
        "last_error_code": None,
        "observed_at": "2026-08-24T12:00:00Z",
        "generation": 7,
    })
    status = ListRemoteSkillTargetsResponse.model_validate({
        "targets": [
            {
                "target": target,
                "publications": [
                    publication,
                    {
                        **publication.model_dump(mode="json"),
                        "artifact_id": "pending-check",
                        "observed_revision": None,
                        "observed_tree_digest": None,
                        "observed_generation": None,
                        "state": "pending",
                        "observed_at": None,
                    },
                ],
            }
        ]
    })
    received: list[object] = []

    class RemoteAdminClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def list_remote_skill_targets(
            self,
            request: ListRemoteSkillTargetsRequest,
        ) -> ListRemoteSkillTargetsResponse:
            received.append(request)
            return status

        async def publish_remote_skill(self, request: PublishRemoteSkillRequest) -> RemoteSkillPublication:
            received.append(request)
            return RemoteSkillPublication.model_validate({
                **publication.model_dump(mode="json"),
                "desired_revision": 2,
                "state": "pending",
                "generation": 8,
            })

        async def unpublish_remote_skill(self, request: UnpublishRemoteSkillRequest) -> RemoteSkillPublication:
            received.append(request)
            return RemoteSkillPublication.model_validate({
                **publication.model_dump(mode="json"),
                "desired_state": "unpublished",
                "state": "pending",
                "generation": 8,
            })

        async def revoke_remote_skill_target(self, request: RevokeRemoteSkillTargetRequest) -> RemoteSkillTarget:
            received.append(request)
            return RemoteSkillTarget.model_validate({
                **target.model_dump(mode="json"),
                "state": "revoked",
                "generation": 4,
            })

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: RemoteAdminClient())
    cli = create_cli([])
    runner = CliRunner()

    shown = runner.invoke(cli, ["skill", "remote-status", "--scope-id", "project"])
    published = runner.invoke(
        cli,
        [
            "skill",
            "remote-publish",
            "--scope-id",
            "project",
            "--target-id",
            "codex-a",
            "--revision",
            "2",
            "release-check",
        ],
    )
    unpublished = runner.invoke(
        cli,
        [
            "skill",
            "remote-unpublish",
            "--scope-id",
            "project",
            "--target-id",
            "codex-a",
            "release-check",
        ],
    )
    revoked = runner.invoke(
        cli,
        ["skill", "remote-target-revoke", "--scope-id", "project", "codex-a"],
    )

    assert all(result.exit_code == 0 for result in (shown, published, unpublished, revoked))
    assert "release-check: desired=published revision 1, observed=revision 1, state=current" in shown.output
    assert "pending-check: desired=published revision 1, observed=not reported, state=pending" in shown.output
    publish_request = next(item for item in received if isinstance(item, PublishRemoteSkillRequest))
    assert publish_request.artifact == ArtifactReference(family="skill", artifact_id="release-check", revision=2)
    assert publish_request.expected_generation == 7
    unpublish_request = next(item for item in received if isinstance(item, UnpublishRemoteSkillRequest))
    assert unpublish_request.expected_generation == 7
    revoke_request = next(item for item in received if isinstance(item, RevokeRemoteSkillTargetRequest))
    assert revoke_request.expected_generation == 3
    assert "next remote-sync" in published.output
    assert "state=revoked" in revoked.output


def test_cli_version_reports_the_installed_distribution() -> None:
    installed_version = CliRunner().invoke(create_cli([]), ["--version"])

    assert installed_version.exit_code == 0
    assert installed_version.output == f"{version('powercontext')}\n"


def test_cli_exposes_installed_role_commands() -> None:
    result = CliRunner().invoke(create_cli(), ["--help"])

    assert result.exit_code == 0
    assert all(command in result.output for command in ("capabilities", "candidate", "stats", "service", "server"))
    assert "builtin" not in result.output
    assert "client" not in result.output


def test_service_command_provider_requires_the_complete_server_role() -> None:
    script = """
import builtins

real_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == "fastapi" or name.startswith("fastapi."):
        raise ModuleNotFoundError("blocked server dependency", name="fastapi")
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
try:
    import powercontext.service.cli  # noqa: F401
except ModuleNotFoundError as error:
    if error.name != "fastapi":
        raise
else:
    raise AssertionError("service command loaded without the complete Server role")
"""

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_client_settings_load_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "https://memory.example/api/")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_TIMEOUT", "3.5")

    settings = ClientSettings()

    assert settings.server_url == "https://memory.example/api"
    assert settings.timeout == 3.5
    assert ClientSettings(server_url="https://override.example/").server_url == "https://override.example"


def test_client_settings_reject_invalid_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "not-a-url")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_TIMEOUT", "0")

    with pytest.raises(ValidationError):
        ClientSettings()


@pytest.mark.parametrize(
    ("environment", "arguments", "expected_host", "expected_port"),
    [
        (
            {"POWERCONTEXT_SERVER_HTTP_PORT": "8123"},
            ["--host", "192.0.2.1"],
            "192.0.2.1",
            8123,
        ),
        (
            {"POWERCONTEXT_SERVER_HTTP_HOST": "192.0.2.2"},
            ["--port", "8124"],
            "192.0.2.2",
            8124,
        ),
    ],
)
def test_server_command_layers_partial_cli_overrides_over_environment_settings(
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
    arguments: list[str],
    expected_host: str,
    expected_port: int,
) -> None:
    run_server = Mock()
    tracing = Mock()
    monkeypatch.setattr("powercontext.server.cli._run_server", run_server)
    monkeypatch.setattr("powercontext.server.cli.configure_server_logging", lambda _config: None)
    monkeypatch.setattr("powercontext.server.cli.configure_server_tracing", lambda _config: tracing)
    monkeypatch.setenv("POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK", "true")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    result = CliRunner().invoke(
        create_cli([server_app]),
        ["server", "run", *arguments],
    )

    assert result.exit_code == 0
    run_server.assert_called_once()
    assert run_server.call_args.kwargs["host"] == expected_host
    assert run_server.call_args.kwargs["port"] == expected_port
    tracing.shutdown.assert_called_once_with()


def test_server_command_uses_env_file_instead_of_stale_shell_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = tmp_path / ".env"
    environment.write_text(
        "POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.2\nPOWERCONTEXT_SERVER_HTTP_PORT=8125\nOPENAI_API_KEY=file-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_HOST", "192.0.2.20")
    monkeypatch.setenv("POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK", "true")
    monkeypatch.delenv("POWERCONTEXT_SERVER_HTTP_PORT", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    received_provider_keys: list[str | None] = []
    received_bindings: list[tuple[str, int]] = []

    def run_server(_application, *, host: str, port: int) -> None:
        received_provider_keys.append(os.environ.get("OPENAI_API_KEY"))
        received_bindings.append((host, port))

    tracing = Mock()
    monkeypatch.setattr("powercontext.server.cli._run_server", run_server)
    monkeypatch.setattr("powercontext.server.cli.configure_server_logging", lambda _config: None)
    monkeypatch.setattr("powercontext.server.cli.configure_server_tracing", lambda _config: tracing)

    result = CliRunner().invoke(
        create_cli([server_app]),
        ["server", "run", "--env-file", str(environment)],
    )

    assert result.exit_code == 0
    assert received_provider_keys == ["file-secret"]
    assert received_bindings == [("127.0.0.2", 8125)]
    assert "OPENAI_API_KEY" not in os.environ
    assert "POWERCONTEXT_SERVER_HTTP_PORT" not in os.environ


def test_server_command_clears_stale_server_values_missing_from_env_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = tmp_path / ".env"
    environment.write_text("POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1\n", encoding="utf-8")
    monkeypatch.setenv("POWERCONTEXT_SERVER_AUTH_ENABLED", "true")
    monkeypatch.delenv("POWERCONTEXT_SERVER_AUTH_TOKEN", raising=False)
    run_server = Mock()
    tracing = Mock()
    monkeypatch.setattr("powercontext.server.cli._run_server", run_server)
    monkeypatch.setattr("powercontext.server.cli.configure_server_logging", lambda _config: None)
    monkeypatch.setattr("powercontext.server.cli.configure_server_tracing", lambda _config: tracing)

    result = CliRunner().invoke(
        create_cli([server_app]),
        ["server", "run", "--env-file", str(environment)],
    )

    assert result.exit_code == 0
    run_server.assert_called_once()
    assert os.environ["POWERCONTEXT_SERVER_AUTH_ENABLED"] == "true"


def test_server_command_reports_a_missing_env_file_without_starting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    run_server = Mock()
    monkeypatch.setattr("powercontext.server.cli._run_server", run_server)

    result = CliRunner().invoke(
        create_cli([server_app]),
        ["server", "run", "--env-file", str(tmp_path / "missing.env")],
    )

    assert result.exit_code == 2
    assert "Invalid value for --env-file" in (result.output + result.stderr)
    run_server.assert_not_called()


def test_server_command_does_not_relabel_runtime_oserror_as_env_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "powercontext.server.cli._run_configured_server",
        Mock(side_effect=OSError("simulated runtime startup failure")),
    )

    result = CliRunner().invoke(create_cli([server_app]), ["server", "run"])

    assert result.exit_code == 1
    assert isinstance(result.exception, OSError)
    assert "Invalid value for --env-file" not in (result.output + result.stderr)


def test_server_command_restores_environment_after_runtime_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment = tmp_path / "server.env"
    environment.write_text("POWERCONTEXT_SERVER_HTTP_PORT=8123\nOPENAI_API_KEY=file-secret\n", encoding="utf-8")
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_PORT", "9000")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def fail_after_configuration(_settings) -> None:
        assert os.environ["POWERCONTEXT_SERVER_HTTP_PORT"] == "8123"
        assert os.environ["OPENAI_API_KEY"] == "file-secret"
        raise OSError("simulated runtime startup failure")  # noqa: TRY003

    monkeypatch.setattr("powercontext.server.cli._run_configured_server", fail_after_configuration)

    result = CliRunner().invoke(
        create_cli([server_app]),
        ["server", "run", "--env-file", str(environment)],
    )

    assert result.exit_code == 1
    assert os.environ["POWERCONTEXT_SERVER_HTTP_PORT"] == "9000"
    assert "OPENAI_API_KEY" not in os.environ


@pytest.fixture
def _wide_error_panel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Render typer's rich error panel wide enough that asserted tokens are never wrapped.

    Under GitHub Actions typer forces ``force_terminal=True``; combined with the runner's
    ``TERM=dumb`` that pins rich's console to 80 columns and ignores ``COLUMNS`` (the dumb-terminal
    branch returns before honouring it), which hyphen-breaks ``--host`` and force-splits the long
    opt-in env var. Neutralise the forced terminal so the captured pipe reads as non-terminal (hence
    non-dumb) and set an explicit width, so the panel stays on wide lines in any environment.
    """

    monkeypatch.setattr("typer.rich_utils.FORCE_TERMINAL", None)
    monkeypatch.setattr("typer.rich_utils.MAX_WIDTH", 10_000)


def test_server_command_rejects_an_unauthenticated_non_loopback_host_override(
    monkeypatch: pytest.MonkeyPatch,
    _wide_error_panel: None,
) -> None:
    run_server = Mock()
    tracing = Mock()
    monkeypatch.setattr("powercontext.server.cli._run_server", run_server)
    monkeypatch.setattr("powercontext.server.cli.configure_server_logging", lambda _config: None)
    monkeypatch.setattr("powercontext.server.cli.configure_server_tracing", lambda _config: tracing)

    result = CliRunner().invoke(
        create_cli([server_app]),
        ["server", "run", "--host", "0.0.0.0"],  # noqa: S104 - exercises the non-loopback guard.
    )

    assert result.exit_code == 2  # typer.BadParameter, not an unhandled traceback.
    run_server.assert_not_called()
    # The operator gets the actionable opt-in lever (the full env var), not pydantic's internal dump.
    assert "--host" in result.output
    assert "POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true" in result.output
    assert "pydantic" not in result.output


def test_server_command_reports_a_friendly_error_when_auth_lacks_a_token(
    monkeypatch: pytest.MonkeyPatch,
    _wide_error_panel: None,
) -> None:
    run_server = Mock()
    tracing = Mock()
    monkeypatch.setattr("powercontext.server.cli._run_server", run_server)
    monkeypatch.setattr("powercontext.server.cli.configure_server_logging", lambda _config: None)
    monkeypatch.setattr("powercontext.server.cli.configure_server_tracing", lambda _config: tracing)
    monkeypatch.setenv("POWERCONTEXT_SERVER_AUTH_ENABLED", "true")
    monkeypatch.delenv("POWERCONTEXT_SERVER_AUTH_TOKEN", raising=False)

    result = CliRunner().invoke(create_cli([server_app]), ["server", "run"])

    assert result.exit_code == 2  # typer.BadParameter, not an unhandled traceback.
    run_server.assert_not_called()
    # The operator gets the concrete token / disable levers, not pydantic's internal dump.
    assert "POWERCONTEXT_SERVER_AUTH_TOKEN" in result.output
    assert "POWERCONTEXT_SERVER_AUTH_ENABLED=false" in result.output
    assert "pydantic" not in result.output


def test_server_command_lets_a_loopback_override_repair_an_unsafe_environment_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The bind policy must validate the address we actually bind, not the environment value alone: a
    # safe ``--host 127.0.0.1`` has to repair an unsafe POWERCONTEXT_SERVER_HTTP_HOST=0.0.0.0.
    run_server = Mock()
    tracing = Mock()
    monkeypatch.setattr("powercontext.server.cli._run_server", run_server)
    monkeypatch.setattr("powercontext.server.cli.configure_server_logging", lambda _config: None)
    monkeypatch.setattr("powercontext.server.cli.configure_server_tracing", lambda _config: tracing)
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP_HOST", "0.0.0.0")  # noqa: S104 - unsafe env value the CLI repairs.

    result = CliRunner().invoke(
        create_cli([server_app]),
        ["server", "run", "--host", "127.0.0.1"],
    )

    assert result.exit_code == 0
    run_server.assert_called_once()
    assert run_server.call_args.kwargs["host"] == "127.0.0.1"


def test_server_command_does_not_load_client_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    run_server = Mock()
    tracing = Mock()
    monkeypatch.setenv("POWERCONTEXT_CLIENT_SERVER_URL", "not-a-url")
    monkeypatch.setenv("POWERCONTEXT_SERVER_AUTH_ENABLED", "false")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DASHBOARD_ENABLED", "true")
    monkeypatch.setattr("powercontext.server.cli._run_server", run_server)
    monkeypatch.setattr("powercontext.server.cli.configure_server_logging", lambda _config: None)
    monkeypatch.setattr("powercontext.server.cli.configure_server_tracing", lambda _config: tracing)

    result = CliRunner().invoke(create_cli([server_app]), ["server", "run"])

    assert result.exit_code == 0
    assert "PowerContext Dashboard: http://127.0.0.1:8000/" in result.stdout


def test_cli_reports_server_errors_with_request_context_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

        async def get_readiness(self) -> ReadinessResponse:
            raise ServerResponseError(status_code=503, request_id="request-123")

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: FailingClient())

    result = CliRunner().invoke(create_cli([]), ["ready"])

    assert result.exit_code == 1
    assert result.output == "PowerContext Server returned HTTP 503 (request ID: request-123)\n"


def test_client_command_prints_human_readable_output_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    class HealthyClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_value: BaseException | None,
            traceback: TracebackType | None,
        ) -> None:
            return None

        async def get_liveness(self) -> HealthResponse:
            return HealthResponse(status="ok")

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: HealthyClient())

    result = CliRunner().invoke(create_cli([]), ["live"])

    assert result.exit_code == 0
    assert result.output == "Status: ok\n"


def test_stats_command_builds_request_and_prints_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[GetStatsRequest] = []

    class StatsClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get_stats(self, request: GetStatsRequest) -> ScopedStats:
            received.append(request)
            return _stats_response()

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: StatsClient())

    result = CliRunner().invoke(
        create_cli([]),
        ["stats", "--scope-id", "project", "--period", "today"],
    )

    assert result.exit_code == 0
    assert received[0].model_dump(mode="json") == {
        "selection": {"mode": "exact", "scope_ids": ["project"]},
        "period": "today",
    }
    assert "Sources: 0 total, 0 memory processed, 0 memory pending" in result.output
    assert "Generation: 0 requests, 0 input tokens, 0 output tokens" in result.output
    assert "Recall token estimator: character:weighted@1" in result.output
    assert (
        "Recall tokens: 3 preparations (2 ready, 1 comparable), 100 baseline, 40 recalled, 60 reduction"
        in result.output
    )


def test_client_generation_commands_build_requests_from_explicit_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[GenerateExperienceRequest | GenerateSkillRequest] = []

    class GeneratingClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def generate_experience(self, request: GenerateExperienceRequest) -> GeneratedCandidateResponse:
            received.append(request)
            return GeneratedCandidateResponse(status=GeneratedCandidateStatus.NO_OP, candidate=None)

        async def generate_skill(self, request: GenerateSkillRequest) -> GeneratedCandidateResponse:
            received.append(request)
            return GeneratedCandidateResponse(status=GeneratedCandidateStatus.NO_OP, candidate=None)

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: GeneratingClient())
    cli = create_cli([])

    experience_result = CliRunner().invoke(
        cli,
        [
            "--json",
            "experience",
            "generate",
            "--scope-id",
            "project",
            "--source-ref",
            "content/task-1",
            "--source-ref",
            "content/task-2",
            "--target",
            "experience/exp-1@2",
            "--reason",
            "incorporate the latest result",
        ],
    )
    skill_result = CliRunner().invoke(
        cli,
        [
            "--json",
            "skill",
            "generate",
            "--scope-id",
            "project",
            "--origin",
            "experience",
            "--artifact-ref",
            "experience/exp-2@1",
        ],
    )

    assert experience_result.exit_code == 0
    assert skill_result.exit_code == 0
    assert [type(request) for request in received] == [GenerateExperienceRequest, GenerateSkillRequest]
    experience = received[0]
    assert isinstance(experience, GenerateExperienceRequest)
    assert [(reference.name, reference.source_id) for reference in experience.source_refs] == [
        ("content", "task-1"),
        ("content", "task-2"),
    ]
    assert [reference.model_dump() for reference in experience.artifact_refs] == [
        {"family": "experience", "artifact_id": "exp-1", "revision": 2}
    ]
    assert experience.target == experience.artifact_refs[0]
    assert experience.reason == "incorporate the latest result"
    skill = received[1]
    assert isinstance(skill, GenerateSkillRequest)
    assert skill.origin is SkillGenerationOrigin.EXPERIENCE
    assert [reference.model_dump() for reference in skill.artifact_refs] == [
        {"family": "experience", "artifact_id": "exp-2", "revision": 1}
    ]


def test_client_candidate_revision_commands_build_typed_proposals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[ReviseArtifactCandidateRequest] = []

    class RevisingClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def revise_artifact_candidate(
            self,
            request: ReviseArtifactCandidateRequest,
        ) -> GeneratedCandidateResponse:
            received.append(request)
            return GeneratedCandidateResponse(status=GeneratedCandidateStatus.NO_OP, candidate=None)

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: RevisingClient())
    instructions_file = tmp_path / "instructions.md"
    instructions_file.write_text("Run both backend acceptance scenarios.", encoding="utf-8")
    cli = create_cli([])

    experience_result = CliRunner().invoke(
        cli,
        [
            "candidate",
            "revise",
            "experience",
            "--scope-id",
            "project",
            "--expected-version",
            "1",
            "--situation",
            "Only one backend was tested.",
            "--action",
            "Run the same scenario on both backends.",
            "--outcome",
            "Both backends passed.",
            "--lesson",
            "Keep acceptance behavior backend-neutral.",
            "--source-ref",
            "content/task-1",
            "candidate-experience",
        ],
    )
    skill_result = CliRunner().invoke(
        cli,
        [
            "candidate",
            "revise",
            "skill",
            "--scope-id",
            "project",
            "--expected-version",
            "2",
            "--name",
            "backend-validation",
            "--description",
            "Validate storage backends consistently.",
            "--instructions-file",
            str(instructions_file),
            "--validation",
            "SQLite passes.",
            "--validation",
            "OceanBase passes.",
            "--target",
            "skill/backend-validation@1",
            "candidate-skill",
        ],
    )

    assert experience_result.exit_code == 0
    assert skill_result.exit_code == 0
    experience = received[0]
    assert isinstance(experience.proposal, ExperienceProposal)
    assert experience.proposal.lesson == "Keep acceptance behavior backend-neutral."
    skill = received[1]
    assert isinstance(skill.proposal, SkillProposal)
    assert skill.proposal.instructions == "Run both backend acceptance scenarios."
    assert [item.root for item in skill.proposal.validation] == ["SQLite passes.", "OceanBase passes."]
    assert skill.target == skill.artifact_refs[0]


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            ["experience", "generate", "--scope-id", "project", "--source-ref", "task-1"],
            "expected TYPE/ID",
        ),
        (
            [
                "skill",
                "generate",
                "--scope-id",
                "project",
                "--origin",
                "source",
                "--artifact-ref",
                "experience/exp-1@1",
            ],
            "source origin requires only Source refs",
        ),
    ],
)
def test_client_generation_commands_reject_invalid_reference_options(
    arguments: list[str],
    message: str,
) -> None:
    result = CliRunner().invoke(create_cli([]), arguments)

    assert result.exit_code == 2
    assert message in result.output


def test_client_external_skill_import_preserves_exact_identity_and_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: list[ImportExternalSkillRequest] = []

    class ImportingClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def import_external_skill(self, request: ImportExternalSkillRequest) -> GeneratedCandidateResponse:
            received.append(request)
            return GeneratedCandidateResponse(status=GeneratedCandidateStatus.NO_OP, candidate=None)

    monkeypatch.setattr(client_cli, "PowerContextClient", lambda *_args, **_kwargs: ImportingClient())

    result = CliRunner().invoke(
        create_cli([]),
        [
            "external-skill",
            "import",
            "--scope-id",
            "project",
            "--fingerprint",
            "a" * 64,
            "--mode",
            "fork",
            "codex:project:repository/friendly-python",
        ],
    )

    assert result.exit_code == 0
    assert received[0].external_skill_id == "codex:project:repository/friendly-python"
    assert received[0].fingerprint == "a" * 64
    assert received[0].mode is ExternalSkillImportMode.FORK


def test_client_skill_export_uses_configured_authentication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received_tokens: list[str | None] = []

    class ExportingClient:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *_args) -> None:
            return None

        async def get_skill(self, request: GetSkillRequest) -> SkillArtifact:
            return SkillArtifact(
                artifact=request.artifact,
                content=SkillProposal(
                    name="safe-skill",
                    description="Use for a bounded task.",
                    instructions="Perform the bounded task.",
                    validation=[SkillValidationItem("The expected result exists.")],
                ),
                source_refs=[],
                artifact_refs=[],
            )

    def client_factory(_server_url: str, *, token: str | None = None, **_kwargs: object) -> ExportingClient:
        received_tokens.append(token)
        return ExportingClient()

    monkeypatch.setenv("POWERCONTEXT_CLIENT_API_TOKEN", "secret-token")
    monkeypatch.setattr(client_cli, "PowerContextClient", client_factory)
    destination = tmp_path / "safe-skill"

    result = CliRunner().invoke(
        create_cli([]),
        [
            "skill",
            "export",
            "--target",
            "codex",
            "--scope-id",
            "project",
            "--revision",
            "1",
            "--destination",
            str(destination),
            "skill-123",
        ],
    )

    assert result.exit_code == 0
    assert received_tokens == ["secret-token"]
    assert "Exported skill-123@1 for codex" in result.output
    assert (destination / "SKILL.md").is_file()
