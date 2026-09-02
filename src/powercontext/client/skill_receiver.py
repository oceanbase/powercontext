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

"""Lightweight Codex/Claude Code Receiver for remote desired-state Skill delivery."""

# Trust-boundary failures retain precise local diagnostics, and the install
# transaction is deliberately linear so its rename ordering stays auditable.
# ruff: noqa: TRY003, TRY203, TRY301

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import hmac
import ipaddress
import json
import os
import platform
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from powercontext.builtin.artifacts.skill.compatibility import (
    SkillCompatibilityState,
    assess_skill_compatibility,
    target_environment_fingerprint,
)
from powercontext.builtin.artifacts.skill.external import AgentEnvironmentProfile, AgentSkillTarget
from powercontext.builtin.artifacts.skill.package import (
    SkillPackageError,
    capture_skill_archive,
    capture_skill_directory,
    materialize_skill_package,
)
from powercontext.client.client import PowerContextClient
from powercontext.client.errors import ClientError, ServerResponseError
from powercontext.http import (
    DownloadRemoteSkillPackageRequest,
    ReconcileRemoteSkillsRequest,
    ReconcileRemoteSkillsResponse,
    RecordRemoteSkillReceiptRequest,
    RemoteSkillAction,
    RemoteSkillFailureState,
    RemoteSkillObservation,
    RemoteSkillOperation,
    RemoteSkillReceiptOutcome,
    SkillPackageDownload,
)

RECEIVER_VERSION = "0.1.0"
_CHECKPOINT_SCHEMA = "powercontext.remote-skill-checkpoint.v1"
_JOURNAL_SCHEMA = "powercontext.remote-skill-pending-action.v1"
_OBSERVED_COMMANDS = ("bash", "node", "pwsh", "ruby")


class SkillReceiverError(RuntimeError):
    """Base failure for target-local Receiver validation or filesystem convergence."""


class SkillReceiverConflictError(SkillReceiverError):
    """Refuse to replace or remove content outside exact checkpoint authority."""


class SkillReceiverStateError(SkillReceiverError):
    """Reject corrupt or credential-mismatched Receiver-private state."""


class RemoteSkillReceiverConfig(BaseModel):
    """One enrolled Receiver identity and its target-local project roots."""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid", frozen=True)

    server_url: str = Field(min_length=1, max_length=2048)
    target_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    credential: SecretStr
    agent_kind: Literal["codex", "claude_code"]
    workspace: Path
    state_root: Path | None = None
    receiver_version: str = Field(default=RECEIVER_VERSION, min_length=1, max_length=64)
    environment_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    allow_insecure_http: bool = False


class RemoteSkillReceiverClient(Protocol):
    """Narrow transport surface needed by the filesystem Receiver."""

    async def reconcile_remote_skills(self, request: ReconcileRemoteSkillsRequest) -> ReconcileRemoteSkillsResponse: ...

    async def download_remote_skill_package(
        self,
        request: DownloadRemoteSkillPackageRequest,
    ) -> SkillPackageDownload: ...

    async def record_remote_skill_receipt(
        self,
        request: RecordRemoteSkillReceiptRequest,
    ) -> object | None: ...

    async def aclose(self) -> None: ...


class ReceiverCheckpoint(BaseModel):
    """Credential-bound ownership of one complete installed package."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: Literal["powercontext.remote-skill-checkpoint.v1"] = _CHECKPOINT_SCHEMA
    target_id: str
    artifact: dict[str, object]
    tree_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    applied_generation: int = Field(ge=0)


class ReceiverJournal(BaseModel):
    """Crash-recovery record written before any package-directory rename."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_name: Literal["powercontext.remote-skill-pending-action.v1"] = _JOURNAL_SCHEMA
    target_id: str
    action: dict[str, object]
    staging_name: str | None = None
    quarantine_name: str | None = None
    previous: ReceiverCheckpoint | None = None


@dataclass(frozen=True)
class ReceiverSyncResult:
    """Bounded outcome of one explicit or Agent-hook sync."""

    requested: int
    succeeded: int
    failed: int
    receipt_pending: int


@dataclass(frozen=True)
class _AppliedAction:
    receipt: RecordRemoteSkillReceiptRequest
    journal_path: Path | None = None
    quarantine: Path | None = None
    staging: Path | None = None


class RemoteSkillReceiver:
    """Reconcile one target without running a PowerContext Server or database remotely."""

    def __init__(
        self,
        config: RemoteSkillReceiverConfig,
        *,
        client: RemoteSkillReceiverClient | None = None,
    ) -> None:
        require_remote_skill_server_url(
            config.server_url,
            allow_insecure_http=config.allow_insecure_http,
        )
        self.config = config
        self.workspace = config.workspace.expanduser().resolve(strict=False)
        self.skill_root = self.workspace / (".agents/skills" if config.agent_kind == "codex" else ".claude/skills")
        configured_state = (
            self.workspace / ".powercontext" / "skill-receiver" / config.target_id
            if config.state_root is None
            else config.state_root
        )
        self.state_root = configured_state.expanduser().resolve(strict=False)
        if _is_relative_to(self.state_root, self.skill_root):
            raise ValueError("Receiver state root must remain outside the Agent Skill package root")
        self._credential = config.credential.get_secret_value()
        self._mac_key = hashlib.sha256(f"powercontext.receiver.v1\0{self._credential}".encode()).digest()
        self._environment = observe_receiver_environment(self.workspace)
        self._environment_fingerprint = target_environment_fingerprint(self._compatibility_target())
        self._owned_client = client is None
        self._client = (
            PowerContextClient(
                config.server_url,
                token=self._credential,
                allow_insecure_http=config.allow_insecure_http,
            )
            if client is None
            else client
        )

    async def __aenter__(self) -> RemoteSkillReceiver:
        return self

    async def __aexit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if self._owned_client:
            await self._client.aclose()

    async def sync(self) -> ReceiverSyncResult:
        """Reconcile, apply safe local actions, and report exact bounded Receipts."""

        self._prepare_private_roots()
        receipt_pending = await self._replay_pending_receipts()
        if receipt_pending:
            return ReceiverSyncResult(requested=0, succeeded=0, failed=0, receipt_pending=receipt_pending)
        observations = self._observations()
        response = await self._client.reconcile_remote_skills(
            ReconcileRemoteSkillsRequest(
                observations=observations,
                receiver_version=self.config.receiver_version,
                environment_fingerprint=self._environment_fingerprint,
            )
        )
        if response.target_id != self.config.target_id:
            raise SkillReceiverStateError("reconcile response target does not match enrolled Receiver")
        succeeded = 0
        failed = 0
        receipt_pending = 0
        for action in response.actions:
            try:
                download = (
                    await self._client.download_remote_skill_package(
                        DownloadRemoteSkillPackageRequest(
                            generation=action.generation,
                            artifact=action.artifact,
                            package=action.package,
                        )
                    )
                    if action.operation is RemoteSkillOperation.INSTALL
                    and action.package is not None
                    and action.blocked_error_code is None
                    else None
                )
                applied = self._apply(action, download)
            except Exception as error:
                failed += 1
                failure = _failure_receipt(action, error, self.config, self._environment_fingerprint)
                try:
                    await self._client.record_remote_skill_receipt(failure)
                except (
                    Exception
                ):  # The desired generation remains retryable; never turn transport loss into a local mutation.
                    receipt_pending += 1
                continue
            try:
                await self._client.record_remote_skill_receipt(applied.receipt)
            except Exception:
                receipt_pending += 1
                continue
            self._finish(applied)
            succeeded += 1
        return ReceiverSyncResult(
            requested=len(response.actions),
            succeeded=succeeded,
            failed=failed,
            receipt_pending=receipt_pending,
        )

    async def _replay_pending_receipts(self) -> int:
        pending_receipts = 0
        for journal_path in sorted(self._journals_root.glob("*.json")):
            journal = self._read_signed(journal_path, ReceiverJournal)
            self._require_target(journal.target_id)
            action = RemoteSkillAction.model_validate(journal.action)
            checkpoint_path = self._checkpoint_path(action.artifact.artifact_id)
            applied = (
                self._resume_install(action, journal, checkpoint_path, journal_path)
                if action.operation is RemoteSkillOperation.INSTALL
                else self._resume_unpublish(action, journal, checkpoint_path, journal_path)
            )
            if applied is None:
                continue
            try:
                await self._client.record_remote_skill_receipt(applied.receipt)
            except Exception:
                pending_receipts += 1
                continue
            self._finish(applied)
        return pending_receipts

    async def watch(
        self,
        *,
        interval_seconds: float = 5,
        max_backoff_seconds: float = 60,
        on_result: Callable[[ReceiverSyncResult], None] | None = None,
        on_error: Callable[[Exception, float], None] | None = None,
    ) -> None:
        """Continuously reconcile, backing off transient failures while preserving Pull semantics."""

        if interval_seconds < 1:
            raise ValueError("remote watch interval must be at least one second")
        if max_backoff_seconds < interval_seconds:
            raise ValueError("remote watch max backoff must not be shorter than the sync interval")
        retry_delay = interval_seconds
        while True:
            try:
                result = await self.sync()
            except ServerResponseError as error:
                if error.status_code in {401, 403}:
                    raise
                if on_error is not None:
                    on_error(error, retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_backoff_seconds)
                continue
            except (ClientError, OSError, ValueError, RuntimeError) as error:
                if on_error is not None:
                    on_error(error, retry_delay)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_backoff_seconds)
                continue
            if on_result is not None:
                on_result(result)
            incomplete = result.failed > 0 or result.receipt_pending > 0
            delay = retry_delay if incomplete else interval_seconds
            retry_delay = min(retry_delay * 2, max_backoff_seconds) if incomplete else interval_seconds
            await asyncio.sleep(delay)

    def _prepare_private_roots(self) -> None:
        self.skill_root.parent.mkdir(parents=True, exist_ok=True)
        self.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_root.chmod(0o700)
        self._checkpoints_root.mkdir(mode=0o700, exist_ok=True)
        self._journals_root.mkdir(mode=0o700, exist_ok=True)

    @property
    def _checkpoints_root(self) -> Path:
        return self.state_root / "checkpoints"

    @property
    def _journals_root(self) -> Path:
        return self.state_root / "journals"

    def _observations(self) -> list[RemoteSkillObservation]:
        observations: list[RemoteSkillObservation] = []
        for path in sorted(self._checkpoints_root.glob("*.json")):
            checkpoint = self._read_signed(path, ReceiverCheckpoint)
            self._require_target(checkpoint.target_id)
            destination = self._destination(checkpoint.skill_name)
            actual_digest = _directory_digest(destination)
            observations.append(
                RemoteSkillObservation(
                    artifact=checkpoint.artifact,
                    tree_digest=checkpoint.tree_digest,
                    actual_tree_digest=actual_digest,
                    skill_name=checkpoint.skill_name,
                    applied_generation=checkpoint.applied_generation,
                )
            )
        return observations

    def _apply(self, action: RemoteSkillAction, download: SkillPackageDownload | None) -> _AppliedAction:
        if action.blocked_error_code is not None:
            if action.blocked_error_code == "drifted":
                raise SkillReceiverConflictError("the managed Skill directory has drifted")
            raise SkillReceiverConflictError("the local ownership checkpoint is not authorized")
        return (
            self._install(action, download)
            if action.operation is RemoteSkillOperation.INSTALL
            else self._unpublish(action)
        )

    def _install(  # noqa: C901
        self,
        action: RemoteSkillAction,
        download: SkillPackageDownload | None,
    ) -> _AppliedAction:
        if action.package is None or download is None:
            raise SkillReceiverStateError("install action is missing its exact package reference")
        checkpoint_path = self._checkpoint_path(action.artifact.artifact_id)
        current = self._read_optional_checkpoint(checkpoint_path)
        journal_path = self._journal_path(action.artifact.artifact_id)
        pending = self._read_optional_journal(journal_path)
        if pending is not None:
            resumed = self._resume_install(action, pending, checkpoint_path, journal_path)
            if resumed is not None:
                return resumed
            current = self._read_optional_checkpoint(checkpoint_path)
        destination = self._destination(action.skill_name)
        if current is not None:
            current_path = self._destination(current.skill_name)
            actual = _directory_digest(current_path)
            if actual != current.tree_digest:
                raise SkillReceiverConflictError("the managed Skill directory no longer matches its checkpoint")
            if (
                current.artifact == action.artifact.model_dump(mode="json")
                and current.tree_digest == action.tree_digest
                and current.skill_name == action.skill_name
            ):
                revised = _checkpoint(self.config.target_id, action)
                self._write_signed(checkpoint_path, revised)
                return _success_receipt(action, self.config, self._environment_fingerprint)
            if action.expected_local is None or not _checkpoint_matches_observation(current, action.expected_local):
                raise SkillReceiverConflictError("the install action does not authorize replacing the local checkpoint")
            if destination != current_path and (destination.exists() or destination.is_symlink()):
                raise SkillReceiverConflictError("the desired Skill directory is occupied by foreign content")
        elif destination.exists() or destination.is_symlink():
            raise SkillReceiverConflictError("the desired Skill directory is occupied by foreign content")

        try:
            archive_bytes = base64.b64decode(download.archive_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise SkillReceiverStateError("downloaded Skill archive encoding is invalid") from error
        package = capture_skill_archive(archive_bytes)
        if package.reference.model_dump(mode="json") != action.package.model_dump(mode="json"):
            raise SkillReceiverStateError("downloaded Skill package reference does not match the action")
        if package.reference.tree_digest != action.tree_digest:
            raise SkillReceiverStateError("downloaded Skill package tree digest does not match the action")
        if package.metadata.name != action.skill_name:
            raise SkillPackageError("downloaded Skill package name does not match the action")
        compatibility = assess_skill_compatibility(package.as_skill_content(), package, self._compatibility_target())
        if compatibility.state is not SkillCompatibilityState.COMPATIBLE:
            reason = compatibility.reasons[0] if compatibility.reasons else compatibility.state.value
            raise SkillPackageError(f"downloaded Skill package is incompatible with this Agent target: {reason}")

        staging = Path(tempfile.mkdtemp(prefix=".powercontext-stage-", dir=self.skill_root.parent))
        staged_package = staging / action.skill_name
        quarantine = self.skill_root.parent / _quarantine_name(action.artifact.artifact_id)
        try:
            materialize_skill_package(package, staged_package)
            if _directory_digest(staged_package) != action.tree_digest:
                raise SkillReceiverStateError("staged Skill package tree digest is invalid")
            journal = ReceiverJournal(
                target_id=self.config.target_id,
                action=action.model_dump(mode="json"),
                staging_name=staging.name,
                quarantine_name=quarantine.name,
                previous=current,
            )
            self._write_signed(journal_path, journal)
            if current is not None:
                if quarantine.exists() or quarantine.is_symlink():
                    raise SkillReceiverStateError("Receiver quarantine is already occupied")
                os.replace(self._destination(current.skill_name), quarantine)
            self.skill_root.mkdir(parents=True, exist_ok=True)
            os.replace(staged_package, destination)
            if _directory_digest(destination) != action.tree_digest:
                raise SkillReceiverStateError("installed Skill package tree digest changed during rename")
            self._write_signed(checkpoint_path, _checkpoint(self.config.target_id, action))
            self._require_quarantine_owned(quarantine, current)
        except BaseException:
            # Signed journal plus exact staging/quarantine state is intentionally retained for inspection/recovery.
            raise
        return _AppliedAction(
            receipt=_success_receipt(action, self.config, self._environment_fingerprint).receipt,
            journal_path=journal_path,
            quarantine=quarantine,
            staging=staging,
        )

    def _resume_install(
        self,
        action: RemoteSkillAction,
        journal: ReceiverJournal,
        checkpoint_path: Path,
        journal_path: Path,
    ) -> _AppliedAction | None:
        self._require_target(journal.target_id)
        pending_action = RemoteSkillAction.model_validate(journal.action)
        if (
            not _same_action_intent(pending_action, action)
            or pending_action.operation is not RemoteSkillOperation.INSTALL
        ):
            raise SkillReceiverConflictError("pending action journal does not match latest install intent")
        if journal.staging_name is None or journal.quarantine_name is None:
            raise SkillReceiverStateError("pending install journal is incomplete")
        staging = self._private_sibling(journal.staging_name)
        staged_package = staging / action.skill_name
        quarantine = self._private_sibling(journal.quarantine_name)
        destination = self._destination(action.skill_name)
        if _directory_digest(destination) == action.tree_digest:
            self._write_signed(checkpoint_path, _checkpoint(self.config.target_id, action))
            self._require_quarantine_owned(quarantine, journal.previous)
            return _AppliedAction(
                receipt=_success_receipt(action, self.config, self._environment_fingerprint).receipt,
                journal_path=journal_path,
                quarantine=quarantine,
                staging=staging,
            )
        if not destination.exists() and _directory_digest(staged_package) == action.tree_digest:
            self.skill_root.mkdir(parents=True, exist_ok=True)
            os.replace(staged_package, destination)
            self._write_signed(checkpoint_path, _checkpoint(self.config.target_id, action))
            self._require_quarantine_owned(quarantine, journal.previous)
            return _AppliedAction(
                receipt=_success_receipt(action, self.config, self._environment_fingerprint).receipt,
                journal_path=journal_path,
                quarantine=quarantine,
                staging=staging,
            )
        previous = journal.previous
        if previous is not None and _directory_digest(self._destination(previous.skill_name)) == previous.tree_digest:
            shutil.rmtree(staging, ignore_errors=True)
            journal_path.unlink(missing_ok=True)
            return None
        if previous is None and not destination.exists() and not quarantine.exists():
            shutil.rmtree(staging, ignore_errors=True)
            journal_path.unlink(missing_ok=True)
            return None
        raise SkillReceiverConflictError("pending install filesystem state is ambiguous")

    def _unpublish(self, action: RemoteSkillAction) -> _AppliedAction:
        checkpoint_path = self._checkpoint_path(action.artifact.artifact_id)
        journal_path = self._journal_path(action.artifact.artifact_id)
        pending = self._read_optional_journal(journal_path)
        if pending is not None:
            return self._resume_unpublish(action, pending, checkpoint_path, journal_path)
        current = self._read_optional_checkpoint(checkpoint_path)
        if action.expected_local is None:
            if current is not None:
                raise SkillReceiverConflictError("unpublish action omitted the Receiver-owned checkpoint")
            destination = self._destination(action.skill_name)
            if destination.exists() or destination.is_symlink():
                raise SkillReceiverConflictError("foreign content occupies the desired Skill directory")
            return _success_receipt(action, self.config, self._environment_fingerprint)
        if current is None or not _checkpoint_matches_observation(current, action.expected_local):
            raise SkillReceiverConflictError("unpublish action does not match the Receiver-owned checkpoint")
        destination = self._destination(current.skill_name)
        if _directory_digest(destination) != current.tree_digest:
            raise SkillReceiverConflictError("the managed Skill directory no longer matches its checkpoint")
        quarantine = self.skill_root.parent / _quarantine_name(action.artifact.artifact_id)
        if quarantine.exists() or quarantine.is_symlink():
            raise SkillReceiverStateError("Receiver quarantine is already occupied")
        journal = ReceiverJournal(
            target_id=self.config.target_id,
            action=action.model_dump(mode="json"),
            quarantine_name=quarantine.name,
            previous=current,
        )
        self._write_signed(journal_path, journal)
        os.replace(destination, quarantine)
        checkpoint_path.unlink()
        return _AppliedAction(
            receipt=_success_receipt(action, self.config, self._environment_fingerprint).receipt,
            journal_path=journal_path,
            quarantine=quarantine,
        )

    def _resume_unpublish(
        self,
        action: RemoteSkillAction,
        journal: ReceiverJournal,
        checkpoint_path: Path,
        journal_path: Path,
    ) -> _AppliedAction:
        self._require_target(journal.target_id)
        pending_action = RemoteSkillAction.model_validate(journal.action)
        if (
            not _same_action_intent(pending_action, action)
            or pending_action.operation is not RemoteSkillOperation.UNPUBLISH
        ):
            raise SkillReceiverConflictError("pending action journal does not match latest unpublish intent")
        if journal.quarantine_name is None:
            raise SkillReceiverStateError("pending unpublish journal is incomplete")
        quarantine = self._private_sibling(journal.quarantine_name)
        if checkpoint_path.exists():
            raise SkillReceiverConflictError("pending unpublish retained an unexpected ownership checkpoint")
        if (
            journal.previous is None
            or _directory_digest(quarantine, expected_name=journal.previous.skill_name) != journal.previous.tree_digest
        ):
            raise SkillReceiverConflictError("pending unpublish quarantine no longer matches the authorized package")
        return _AppliedAction(
            receipt=_success_receipt(action, self.config, self._environment_fingerprint).receipt,
            journal_path=journal_path,
            quarantine=quarantine,
        )

    def _finish(self, applied: _AppliedAction) -> None:
        if applied.journal_path is not None:
            applied.journal_path.unlink(missing_ok=True)
        if applied.quarantine is not None:
            shutil.rmtree(applied.quarantine, ignore_errors=True)
        if applied.staging is not None:
            shutil.rmtree(applied.staging, ignore_errors=True)

    def _destination(self, skill_name: str) -> Path:
        destination = (self.skill_root / skill_name).resolve(strict=False)
        if destination.parent != self.skill_root.resolve(strict=False):
            raise SkillReceiverStateError("Skill name escapes the Agent package root")
        return destination

    def _compatibility_target(self) -> AgentSkillTarget:
        return AgentSkillTarget(
            target_id=self.config.target_id,
            agent_kind=self.config.agent_kind,
            installation_scope="project",
            path=self.skill_root,
            allow_managed_publish=True,
            environment=self._environment,
        )

    def _private_sibling(self, name: str) -> Path:
        if not name.startswith(".powercontext-") or Path(name).name != name:
            raise SkillReceiverStateError("pending action path is invalid")
        return self.skill_root.parent / name

    @staticmethod
    def _require_quarantine_owned(quarantine: Path, previous: ReceiverCheckpoint | None) -> None:
        if previous is None:
            if quarantine.exists() or quarantine.is_symlink():
                raise SkillReceiverConflictError("unexpected content occupies the Receiver quarantine")
            return
        if _directory_digest(quarantine, expected_name=previous.skill_name) != previous.tree_digest:
            raise SkillReceiverConflictError("Receiver quarantine no longer matches the replaced managed package")

    def _checkpoint_path(self, artifact_id: str) -> Path:
        return self._state_path(self._checkpoints_root, artifact_id)

    def _journal_path(self, artifact_id: str) -> Path:
        return self._state_path(self._journals_root, artifact_id)

    @staticmethod
    def _state_path(root: Path, artifact_id: str) -> Path:
        name = hashlib.sha256(artifact_id.encode()).hexdigest()
        return root / f"{name}.json"

    def _read_optional_checkpoint(self, path: Path) -> ReceiverCheckpoint | None:
        return None if not path.exists() else self._read_signed(path, ReceiverCheckpoint)

    def _read_optional_journal(self, path: Path) -> ReceiverJournal | None:
        return None if not path.exists() else self._read_signed(path, ReceiverJournal)

    def _read_signed(self, path: Path, model_type):
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            payload = envelope["payload"]
            signature = str(envelope["hmac_sha256"])
            canonical = _canonical_json(payload)
        except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
            raise SkillReceiverStateError("Receiver-private state is invalid") from error
        expected = hmac.new(self._mac_key, canonical, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            raise SkillReceiverStateError("Receiver-private state credential binding is invalid")
        try:
            return model_type.model_validate(payload)
        except ValueError as error:
            raise SkillReceiverStateError("Receiver-private state payload is invalid") from error

    def _write_signed(self, path: Path, payload: BaseModel) -> None:
        value = payload.model_dump(mode="json")
        canonical = _canonical_json(value)
        envelope = {
            "payload": value,
            "hmac_sha256": hmac.new(self._mac_key, canonical, hashlib.sha256).hexdigest(),
        }
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(envelope, stream, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _require_target(self, target_id: str) -> None:
        if not hmac.compare_digest(target_id, self.config.target_id):
            raise SkillReceiverStateError("Receiver-private state belongs to another target")


def _checkpoint(target_id: str, action: RemoteSkillAction) -> ReceiverCheckpoint:
    return ReceiverCheckpoint(
        target_id=target_id,
        artifact=action.artifact.model_dump(mode="json"),
        tree_digest=action.tree_digest,
        skill_name=action.skill_name,
        applied_generation=action.generation,
    )


def _checkpoint_matches_observation(
    checkpoint: ReceiverCheckpoint,
    observation: RemoteSkillObservation,
) -> bool:
    return (
        checkpoint.artifact == observation.artifact.model_dump(mode="json")
        and checkpoint.tree_digest == observation.tree_digest
        and checkpoint.skill_name == observation.skill_name
        and checkpoint.applied_generation == observation.applied_generation
        and observation.actual_tree_digest == observation.tree_digest
    )


def _same_action_intent(left: RemoteSkillAction, right: RemoteSkillAction) -> bool:
    return (
        left.operation is right.operation
        and left.generation == right.generation
        and left.artifact == right.artifact
        and left.tree_digest == right.tree_digest
        and left.skill_name == right.skill_name
        and left.package == right.package
    )


def _success_receipt(
    action: RemoteSkillAction,
    config: RemoteSkillReceiverConfig,
    environment_fingerprint: str,
) -> _AppliedAction:
    observed = action.tree_digest if action.operation is RemoteSkillOperation.INSTALL else None
    return _AppliedAction(
        receipt=RecordRemoteSkillReceiptRequest(
            operation=action.operation,
            generation=action.generation,
            artifact=action.artifact,
            expected_tree_digest=action.tree_digest,
            observed_tree_digest=observed,
            outcome=RemoteSkillReceiptOutcome.SUCCEEDED,
            failure_state=None,
            error_code=None,
            receiver_version=config.receiver_version,
            environment_fingerprint=environment_fingerprint,
        )
    )


def _failure_receipt(
    action: RemoteSkillAction,
    error: Exception,
    config: RemoteSkillReceiverConfig,
    environment_fingerprint: str,
) -> RecordRemoteSkillReceiptRequest:
    if isinstance(error, SkillReceiverConflictError):
        state = (
            RemoteSkillFailureState.DRIFTED if "drift" in str(error).casefold() else RemoteSkillFailureState.CONFLICT
        )
    elif isinstance(error, SkillPackageError | ValueError):
        state = RemoteSkillFailureState.INCOMPATIBLE
    else:
        state = RemoteSkillFailureState.DELIVERY_FAILED
    return RecordRemoteSkillReceiptRequest(
        operation=action.operation,
        generation=action.generation,
        artifact=action.artifact,
        expected_tree_digest=action.tree_digest,
        observed_tree_digest=None,
        outcome=RemoteSkillReceiptOutcome.FAILED,
        failure_state=state,
        error_code=_error_code(error),
        receiver_version=config.receiver_version,
        environment_fingerprint=environment_fingerprint,
    )


def _error_code(error: Exception) -> str:
    if isinstance(error, SkillReceiverConflictError):
        return "drifted" if "drift" in str(error).casefold() else "local_conflict"
    if isinstance(error, SkillPackageError | ValueError):
        return "incompatible_package"
    return "local_delivery_failed"


def observe_receiver_environment(workspace: Path, /) -> AgentEnvironmentProfile:
    """Observe bounded, secret-free compatibility facts on the Receiver host."""

    system = platform.system().casefold()
    if system == "darwin":
        operating_system: Literal["linux", "macos", "windows", "other"] = "macos"
    elif system == "linux":
        operating_system = "linux"
    elif system == "windows":
        operating_system = "windows"
    else:
        operating_system = "other"
    commands = {"python": platform.python_version()}
    for name in _OBSERVED_COMMANDS:
        if shutil.which(name) is not None:
            commands[name] = "unknown"
    writable_roots = ("workspace",) if _workspace_is_writable(workspace) else ()
    return AgentEnvironmentProfile(
        operating_system=operating_system,
        architecture=platform.machine().strip() or "unknown",
        commands=commands,
        network_policy="unknown",
        writable_roots=writable_roots,
        dependency_install_policy="unknown",
    )


def receiver_environment_fingerprint(
    workspace: Path,
    agent_kind: Literal["codex", "claude_code"],
    /,
) -> str:
    """Return the fingerprint for facts observed on one Receiver host."""

    skill_root = workspace / (".agents/skills" if agent_kind == "codex" else ".claude/skills")
    return target_environment_fingerprint(
        AgentSkillTarget(
            target_id="receiver-environment",
            agent_kind=agent_kind,
            installation_scope="project",
            path=skill_root,
            environment=observe_receiver_environment(workspace),
        )
    )


def _workspace_is_writable(workspace: Path) -> bool:
    candidate = workspace.expanduser().resolve(strict=False)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate.is_dir() and os.access(candidate, os.W_OK)


def _directory_digest(path: Path, *, expected_name: str | None = None) -> str | None:
    if not path.exists() or path.is_symlink():
        return None
    try:
        return capture_skill_directory(path, expected_name=expected_name).reference.tree_digest
    except (OSError, SkillPackageError):
        return None


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def require_remote_skill_server_url(value: str, *, allow_insecure_http: bool = False) -> bool:
    """Validate one Receiver Server URL and report whether it uses cleartext remote HTTP."""

    parsed = urlsplit(value)
    if parsed.scheme.casefold() == "https":
        return False
    if parsed.scheme.casefold() == "http" and parsed.hostname is not None:
        if _loopback_host(parsed.hostname):
            return False
        if allow_insecure_http:
            return True
    raise ValueError(
        "remote Skill Receiver requires HTTPS except for loopback development; "
        "use --allow-insecure-http only on a protected private test network"
    )


def _loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _quarantine_name(artifact_id: str) -> str:
    return f".powercontext-quarantine-{hashlib.sha256(artifact_id.encode()).hexdigest()[:24]}"


__all__ = [
    "RECEIVER_VERSION",
    "ReceiverSyncResult",
    "RemoteSkillReceiver",
    "RemoteSkillReceiverClient",
    "RemoteSkillReceiverConfig",
    "SkillReceiverConflictError",
    "SkillReceiverError",
    "SkillReceiverStateError",
    "observe_receiver_environment",
    "receiver_environment_fingerprint",
]
