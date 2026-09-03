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

# Receiver tests intentionally inspect synchronous target-local filesystem effects inside compact async scenarios.
# ruff: noqa: ASYNC240

import asyncio
import base64
from pathlib import Path
from typing import Literal

import pytest
from pydantic import SecretStr

import powercontext.client.skill_receiver as receiver_module
from powercontext.builtin.artifacts.skill import (
    AgentEnvironmentProfile,
    SkillContent,
    build_instruction_skill_package,
    capture_skill_directory,
)
from powercontext.client.errors import ServerResponseError, TransportError
from powercontext.client.skill_receiver import ReceiverSyncResult, RemoteSkillReceiver, RemoteSkillReceiverConfig
from powercontext.http import (
    ArtifactReference,
    ReconcileRemoteSkillsResponse,
    RemoteSkillAction,
    RemoteSkillFailureState,
    RemoteSkillObservation,
    RemoteSkillOperation,
    SkillPackageDownload,
)


class _FakeRemoteClient:
    def __init__(self) -> None:
        self.actions: list[list[RemoteSkillAction]] = []
        self.packages: dict[str, SkillPackageDownload] = {}
        self.receipts = []
        self.fail_receipts = 0
        self.reconcile_requests = []

    async def reconcile_remote_skills(self, request):
        self.reconcile_requests.append(request)
        actions = self.actions.pop(0)
        return ReconcileRemoteSkillsResponse(scope_id="project:one", target_id="codex-a", actions=actions)

    async def download_remote_skill_package(self, request):
        return self.packages[request.package.tree_digest]

    async def record_remote_skill_receipt(self, request):
        if self.fail_receipts:
            self.fail_receipts -= 1
            raise RuntimeError("simulated Receipt transport loss")  # noqa: TRY003
        self.receipts.append(request)

    async def aclose(self) -> None:
        return None


def _package(instructions: str):
    return build_instruction_skill_package(
        SkillContent(
            name="release-check",
            description="Verify the release.",
            instructions=instructions,
            validation=("The report passes.",),
        )
    )


def _install_action(package, *, revision: int = 1, generation: int = 0, expected=None):
    return RemoteSkillAction(
        operation=RemoteSkillOperation.INSTALL,
        generation=generation,
        artifact=ArtifactReference(family="skill", artifact_id="release-check-artifact", revision=revision),
        tree_digest=package.reference.tree_digest,
        skill_name="release-check",
        package=package.reference.model_dump(mode="json"),
        expected_local=expected,
        blocked_error_code=None,
    )


def _receiver(
    tmp_path: Path,
    client: _FakeRemoteClient,
    *,
    agent_kind: Literal["codex", "claude_code"] = "codex",
) -> RemoteSkillReceiver:
    return RemoteSkillReceiver(
        RemoteSkillReceiverConfig(
            server_url="http://127.0.0.1:8765",
            target_id="codex-a",
            credential=SecretStr("pct_installation-a.super-secret-target-value"),
            agent_kind=agent_kind,
            workspace=tmp_path,
        ),
        client=client,
    )


def _download(package) -> SkillPackageDownload:
    return SkillPackageDownload(
        package=package.reference.model_dump(mode="json"),
        archive_base64=base64.b64encode(package.archive_bytes).decode("ascii"),
    )


def test_receiver_rejects_remote_cleartext_http_without_explicit_permission(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="requires HTTPS"):
        RemoteSkillReceiver(
            RemoteSkillReceiverConfig(
                server_url="http://11.162.218.22:8765",
                target_id="codex-a",
                credential=SecretStr("pct_installation-a.super-secret-target-value"),
                agent_kind="codex",
                workspace=tmp_path,
            ),
            client=_FakeRemoteClient(),
        )


def test_receiver_allows_remote_cleartext_http_with_explicit_permission(tmp_path: Path) -> None:
    receiver = RemoteSkillReceiver(
        RemoteSkillReceiverConfig(
            server_url="http://11.162.218.22:8765",
            target_id="codex-a",
            credential=SecretStr("pct_installation-a.super-secret-target-value"),
            agent_kind="codex",
            workspace=tmp_path,
            allow_insecure_http=True,
        ),
        client=_FakeRemoteClient(),
    )

    assert receiver.config.allow_insecure_http is True


def test_receiver_forwards_cleartext_permission_to_its_owned_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_options: list[dict[str, object]] = []

    def owned_client(*_args: object, **kwargs: object) -> _FakeRemoteClient:
        client_options.append(kwargs)
        return _FakeRemoteClient()

    monkeypatch.setattr(receiver_module, "PowerContextClient", owned_client)

    RemoteSkillReceiver(
        RemoteSkillReceiverConfig(
            server_url="http://11.162.218.22:8765",
            target_id="codex-a",
            credential=SecretStr("pct_installation-a.super-secret-target-value"),
            agent_kind="codex",
            workspace=tmp_path,
            allow_insecure_http=True,
        )
    )

    assert client_options == [
        {
            "token": "pct_installation-a.super-secret-target-value",
            "allow_insecure_http": True,
        }
    ]


@pytest.mark.parametrize(
    ("agent_kind", "relative_root"),
    (("codex", ".agents/skills"), ("claude_code", ".claude/skills")),
)
def test_receiver_installs_to_agent_owned_project_root_and_recovers_a_lost_receipt(
    tmp_path: Path,
    agent_kind: Literal["codex", "claude_code"],
    relative_root: str,
) -> None:
    async def exercise() -> None:
        package = _package("Run the release checks.")
        client = _FakeRemoteClient()
        action = _install_action(package)
        client.actions = [[action], []]
        client.packages[package.reference.tree_digest] = _download(package)
        client.fail_receipts = 1
        receiver = _receiver(tmp_path, client, agent_kind=agent_kind)

        first = await receiver.sync()
        destination = tmp_path / relative_root / "release-check"
        assert first.receipt_pending == 1
        assert destination.is_dir()
        inode = destination.stat().st_ino

        second = await receiver.sync()
        assert second.requested == 0
        assert destination.stat().st_ino == inode
        assert client.receipts[-1].observed_tree_digest == package.reference.tree_digest
        assert not list((tmp_path / ".powercontext/skill-receiver/codex-a/journals").glob("*.json"))

    asyncio.run(exercise())


def test_receiver_refuses_foreign_content_and_reports_conflict_without_replacing_it(tmp_path: Path) -> None:
    async def exercise() -> None:
        package = _package("Run the release checks.")
        client = _FakeRemoteClient()
        action = _install_action(package)
        client.actions = [[action]]
        client.packages[package.reference.tree_digest] = _download(package)
        foreign = tmp_path / ".agents/skills/release-check"
        foreign.mkdir(parents=True)
        (foreign / "SKILL.md").write_text("foreign content", encoding="utf-8")

        result = await _receiver(tmp_path, client).sync()

        assert result.failed == 1
        assert (foreign / "SKILL.md").read_text(encoding="utf-8") == "foreign content"
        assert client.receipts[-1].failure_state is RemoteSkillFailureState.CONFLICT

    asyncio.run(exercise())


def test_receiver_keeps_unpublish_quarantine_until_receipt_and_finishes_after_retry(tmp_path: Path) -> None:
    async def exercise() -> None:
        package = _package("Run the release checks.")
        client = _FakeRemoteClient()
        install = _install_action(package)
        client.packages[package.reference.tree_digest] = _download(package)
        client.actions = [[install]]
        receiver = _receiver(tmp_path, client)
        await receiver.sync()

        observation = RemoteSkillObservation(
            artifact=install.artifact,
            tree_digest=install.tree_digest,
            actual_tree_digest=install.tree_digest,
            skill_name=install.skill_name,
            applied_generation=install.generation,
        )
        remove = RemoteSkillAction(
            operation=RemoteSkillOperation.UNPUBLISH,
            generation=1,
            artifact=install.artifact,
            tree_digest=install.tree_digest,
            skill_name=install.skill_name,
            package=None,
            expected_local=observation,
            blocked_error_code=None,
        )
        client.actions = [[remove], []]
        client.fail_receipts = 1

        first = await receiver.sync()
        assert first.receipt_pending == 1
        assert not (tmp_path / ".agents/skills/release-check").exists()
        assert list(tmp_path.glob(".agents/.powercontext-quarantine-*"))

        second = await receiver.sync()
        assert second.requested == 0
        assert not list(tmp_path.glob(".agents/.powercontext-quarantine-*"))
        assert not list((tmp_path / ".powercontext/skill-receiver/codex-a/journals").glob("*.json"))

        republish = _install_action(package, generation=2)
        client.actions = [[republish]]
        republished = await receiver.sync()
        assert republished.succeeded == 1
        assert (tmp_path / ".agents/skills/release-check").is_dir()

    asyncio.run(exercise())


def test_receiver_reports_server_blocked_drift_without_touching_the_package(tmp_path: Path) -> None:
    async def exercise() -> None:
        package = _package("Run the release checks.")
        client = _FakeRemoteClient()
        install = _install_action(package)
        client.packages[package.reference.tree_digest] = _download(package)
        client.actions = [[install]]
        receiver = _receiver(tmp_path, client)
        await receiver.sync()
        skill_markdown = tmp_path / ".agents/skills/release-check/SKILL.md"
        skill_markdown.write_text("user modified", encoding="utf-8")

        blocked = install.model_copy(update={"blocked_error_code": "drifted"})
        client.actions = [[blocked]]
        result = await receiver.sync()

        assert result.failed == 1
        assert skill_markdown.read_text(encoding="utf-8") == "user modified"
        assert client.receipts[-1].failure_state is RemoteSkillFailureState.DRIFTED

    asyncio.run(exercise())


def test_receiver_recovers_an_interrupted_atomic_update_from_its_signed_journal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def exercise() -> None:
        first_package = _package("Run the release checks.")
        second_package = _package("Run the stricter release checks.")
        client = _FakeRemoteClient()
        first = _install_action(first_package)
        client.packages[first_package.reference.tree_digest] = _download(first_package)
        client.packages[second_package.reference.tree_digest] = _download(second_package)
        client.actions = [[first]]
        receiver = _receiver(tmp_path, client)
        await receiver.sync()

        observed = RemoteSkillObservation(
            artifact=first.artifact,
            tree_digest=first.tree_digest,
            actual_tree_digest=first.tree_digest,
            skill_name=first.skill_name,
            applied_generation=first.generation,
        )
        update = _install_action(second_package, revision=2, generation=1, expected=observed)
        client.actions = [[update], []]
        original_replace = receiver_module.os.replace

        def interrupt_new_package(source, destination) -> None:
            source_path = Path(source)
            destination_path = Path(destination)
            if (
                source_path.name == "release-check"
                and source_path.parent.name.startswith(".powercontext-stage-")
                and destination_path == tmp_path / ".agents/skills/release-check"
            ):
                raise OSError("simulated interruption after quarantining the old package")  # noqa: TRY003
            original_replace(source, destination)

        monkeypatch.setattr(receiver_module.os, "replace", interrupt_new_package)
        interrupted = await receiver.sync()
        assert interrupted.failed == 1
        assert not (tmp_path / ".agents/skills/release-check").exists()
        assert list(tmp_path.glob(".agents/.powercontext-stage-*"))
        assert list(tmp_path.glob(".agents/.powercontext-quarantine-*"))

        monkeypatch.setattr(receiver_module.os, "replace", original_replace)
        recovered = await receiver.sync()
        assert recovered.requested == 0
        assert "stricter" in (tmp_path / ".agents/skills/release-check/SKILL.md").read_text(encoding="utf-8")
        assert client.reconcile_requests[-1].observations[0].actual_tree_digest == second_package.reference.tree_digest
        assert not list(tmp_path.glob(".agents/.powercontext-stage-*"))
        assert not list(tmp_path.glob(".agents/.powercontext-quarantine-*"))

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("agent_kind", "expected_state"),
    (("codex", RemoteSkillFailureState.INCOMPATIBLE), ("claude_code", None)),
)
def test_receiver_validates_agent_specific_package_rules_before_installing(
    tmp_path: Path,
    agent_kind: Literal["codex", "claude_code"],
    expected_state: RemoteSkillFailureState | None,
) -> None:
    async def exercise() -> None:
        package = build_instruction_skill_package(
            SkillContent(
                name="release-check",
                description="Review <untrusted> release input.",
                instructions="Run the release checks.",
                validation=("The report passes.",),
            )
        )
        client = _FakeRemoteClient()
        action = _install_action(package)
        client.actions = [[action]]
        client.packages[package.reference.tree_digest] = _download(package)
        workspace = tmp_path / agent_kind

        result = await _receiver(workspace, client, agent_kind=agent_kind).sync()

        destination_root = ".agents/skills" if agent_kind == "codex" else ".claude/skills"
        if expected_state is None:
            assert result.succeeded == 1
            assert (workspace / destination_root / "release-check").is_dir()
        else:
            assert result.failed == 1
            assert client.receipts[-1].failure_state is expected_state
            assert not (workspace / destination_root / "release-check").exists()

    asyncio.run(exercise())


def test_receiver_rejects_package_name_mismatch_before_installing(tmp_path: Path) -> None:
    async def exercise() -> None:
        package = _package("Run the release checks.")
        client = _FakeRemoteClient()
        action = _install_action(package).model_copy(update={"skill_name": "different-name"})
        client.actions = [[action]]
        client.packages[package.reference.tree_digest] = _download(package)

        result = await _receiver(tmp_path, client).sync()

        assert result.failed == 1
        assert client.receipts[-1].failure_state is RemoteSkillFailureState.INCOMPATIBLE
        assert not (tmp_path / ".agents/skills/different-name").exists()

    asyncio.run(exercise())


def test_receiver_requires_a_compatible_observed_runtime_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        source = tmp_path / "source/release-check"
        (source / "scripts").mkdir(parents=True)
        (source / "SKILL.md").write_text(
            "---\nname: release-check\ndescription: Verify the release.\n---\n\nRun the check.\n",
            encoding="utf-8",
        )
        (source / "scripts/check.py").write_text("print('ok')\n", encoding="utf-8")
        (source / "powercontext.runtime.yaml").write_text(
            "schema: powercontext.skill-runtime.v1\n"
            "variants:\n"
            "  - id: python-check\n"
            "    entrypoint: scripts/check.py\n"
            "    interpreter: python\n"
            "    requirements:\n"
            "      operating_systems: [linux]\n"
            "      commands:\n"
            "        unavailable-command: '>=1'\n",
            encoding="utf-8",
        )
        package = capture_skill_directory(source)
        monkeypatch.setattr(
            receiver_module,
            "observe_receiver_environment",
            lambda _workspace: AgentEnvironmentProfile(
                operating_system="linux",
                architecture="x86_64",
                commands={"python": "3.11.0"},
                writable_roots=("workspace",),
            ),
        )
        client = _FakeRemoteClient()
        action = _install_action(package)
        client.actions = [[action]]
        client.packages[package.reference.tree_digest] = _download(package)

        result = await _receiver(tmp_path / "target", client).sync()

        assert result.failed == 1
        assert client.receipts[-1].failure_state is RemoteSkillFailureState.INCOMPATIBLE
        assert not (tmp_path / "target/.agents/skills/release-check").exists()

    asyncio.run(exercise())


def test_receiver_watch_retries_incomplete_and_transport_failures_with_bounded_backoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        client = _FakeRemoteClient()
        receiver = _receiver(tmp_path, client)
        outcomes: list[ReceiverSyncResult | Exception] = [
            ReceiverSyncResult(requested=1, succeeded=0, failed=1, receipt_pending=0),
            TransportError("/v1/skill/remote/reconcile"),
            ReceiverSyncResult(requested=0, succeeded=0, failed=0, receipt_pending=0),
        ]
        results: list[ReceiverSyncResult] = []
        errors: list[tuple[Exception, float]] = []
        delays: list[float] = []

        async def sync() -> ReceiverSyncResult:
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        async def sleep(delay: float) -> None:
            delays.append(delay)
            if len(delays) == 3:
                raise asyncio.CancelledError

        monkeypatch.setattr(receiver, "sync", sync)
        monkeypatch.setattr(receiver_module.asyncio, "sleep", sleep)

        with pytest.raises(asyncio.CancelledError):
            await receiver.watch(
                interval_seconds=2,
                max_backoff_seconds=8,
                on_result=results.append,
                on_error=lambda error, delay: errors.append((error, delay)),
            )

        assert delays == [2, 4, 2]
        assert [result.requested for result in results] == [1, 0]
        assert len(errors) == 1
        assert isinstance(errors[0][0], TransportError)
        assert errors[0][1] == 4

    asyncio.run(exercise())


def test_receiver_watch_stops_when_target_credential_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        receiver = _receiver(tmp_path, _FakeRemoteClient())

        async def rejected() -> ReceiverSyncResult:
            raise ServerResponseError(status_code=401, request_id=None)

        async def unexpected_sleep(_delay: float) -> None:
            raise AssertionError

        monkeypatch.setattr(receiver, "sync", rejected)
        monkeypatch.setattr(receiver_module.asyncio, "sleep", unexpected_sleep)

        with pytest.raises(ServerResponseError, match="HTTP 401"):
            await receiver.watch()

    asyncio.run(exercise())
