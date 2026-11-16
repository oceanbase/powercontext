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

"""One manifest-qualified host pair, real HTTP persistence, controlled inference only."""

from __future__ import annotations

import asyncio
import json
import shlex
import shutil
import sqlite3
import subprocess
import sys
from contextlib import ExitStack
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastapi import FastAPI
from integration_manifest import (
    RECOMMENDED_CAPABILITIES,
    evidence_path_errors,
    load_integration_manifest,
    tool_surface_errors,
)

from powercontext.artifacts import ArtifactRef

from ..zcode_acceptance.protocol import HostModelFixture, ToolAction, strings
from ..zcode_acceptance.runner import REPOSITORY, AcceptanceRun, error_code, serve, write_json
from .pi import PiHost

PI_PLUGIN = REPOSITORY / "integrations/pi/plugins/powercontext"
FIXTURES = Path(__file__).parent / "fixtures"
IMPLEMENTATION = "def increment(value):\n    return value + 1\n"
TRUST_BOUNDARY = (
    "Receiver live-state, capability and execution authorization are untrusted self-attestations. "
    "PowerContext validates exact selection, evidence availability and schema invariants. "
    "Server authentication and API access control are separate from host execution authority. "
    "A Receipt does not grant execution authority, prove host credentials or permissions exist, "
    "or replace current instructions and live permission checks."
)


def objects(value: Any):
    """Decode nested JSON tool result encodings from the native model protocol."""
    if isinstance(value, str):
        try:
            # ZCode renders MCP text followed by a second "Structured content"
            # block. Decode the canonical first JSON value without consuming
            # that display suffix as part of the value.
            decoded, _ = json.JSONDecoder().raw_decode(value.lstrip())
            yield from objects(decoded)
        except ValueError:
            return
    elif isinstance(value, dict):
        yield value
        for item in value.values():
            yield from objects(item)
    elif isinstance(value, list):
        for item in value:
            yield from objects(item)


def model_value(model: HostModelFixture, field: str) -> dict[str, Any]:
    """Copy identities from tool results actually delivered to the host model."""

    # A host may request a title after the main turn; it must not hide the
    # tool result that the actual continuation model already received.
    for request in reversed(model.requests):
        for message in reversed(request.get("messages", [])):
            if message.get("role") == "tool":
                for value in objects(message.get("content")):
                    if field in value:
                        return value
    raise AssertionError("host_delivered_result_missing_" + field)


class CrossHostRun(AcceptanceRun):
    def __init__(self, root: Path, *, case: str) -> None:
        super().__init__(root, live=False, model_config=None, generation_env=None)
        self.case = case
        self.sender_model = HostModelFixture()
        self.sender_session = ""
        self.receiver_session = ""
        self.private_marker = "sender-private-" + uuid4().hex
        self.revision: dict[str, Any] = {}
        self.source: dict[str, Any] = {}
        self.receipt: dict[str, Any] = {}
        self.summary.update(
            schema="powercontext.cross-host-work-continuity.v1",
            case=case,
            host_pair=["pi", "zcode"],
            qualification="master_only",
            trust_boundary=TRUST_BOUNDARY,
            database_profile={"kind": "sqlite", "sqlite_version": sqlite3.sqlite_version, "isolated": True},
            limitations=[
                "Actual Pi RPC and ZCode CLI with controlled model responses; not live-model reasoning qualification.",
                "One master-only pair; no released-host, all-host, desktop or OceanBase parity claim.",
            ],
        )
        self.summary["scenarios"] = [
            {"id": name, "status": "not_run", "evidence_refs": [], "failure_stage": None, "stable_error_code": None}
            for name in ["managed-zcode-install", "manifest", "sender", "receiver", "outcome-and-review"]
        ]

    def manifest(self) -> None:
        with self.scenario(2) as evidence:
            manifest = load_integration_manifest()
            assert not evidence_path_errors(manifest) and not tool_surface_errors(manifest), "capability_manifest_drift"
            hosts = {item.id: item for item in manifest.integrations}
            selected = [hosts[name] for name in self.summary["host_pair"]]
            for host in selected:
                assert host.kind == "agent_host" and host.availability == "master_only", "host_qualification_changed"
                assert set(host.capabilities) >= RECOMMENDED_CAPABILITIES, "host_missing_recommended_capabilities"
            self.summary["hosts"] = [
                {
                    "id": host.id,
                    "kind": host.kind,
                    "availability": host.availability,
                    "integration_revision": self.summary["repository_commit"],
                    "capabilities": list(host.capabilities),
                }
                for host in selected
            ]
            self.summary["hosts"][1].update(
                version=self.summary["host_version"],
                integration_digest=self.summary["plugin_source_digest"],
                executable_digest=sha256(self.cli.read_bytes()).hexdigest(),
            )
            evidence.append(
                self.evidence("manifest", {"schema_version": manifest.schema_version, "hosts": self.summary["hosts"]})
            )

    def sender(self, stack: ExitStack) -> None:
        with self.scenario(3) as evidence:
            cli = PI_PLUGIN / "node_modules/@earendil-works/pi-coding-agent/dist/cli.js"
            assert cli.is_file(), "prerequisite_frozen_pi_dependencies_required"
            home = self.root / "sender-profile"
            home.mkdir()
            model_url = stack.enter_context(serve(self.sender_model.app))
            write_json(
                home / "models.json",
                {
                    "providers": {
                        "continuity": {
                            "baseUrl": model_url + "/v1",
                            "api": "openai-completions",
                            "apiKey": "synthetic-only",
                            "models": [{"id": "fixture", "contextWindow": 65536, "maxTokens": 4096}],
                        }
                    }
                },
            )
            environment = {
                **self.environment,
                "PI_CODING_AGENT_DIR": str(home),
                "POWERCONTEXT_PI_BASE_URL": self.base_url,
                "POWERCONTEXT_PI_ALLOW_INSECURE_HTTP": "true",
                "POWERCONTEXT_PI_AUTHORIZATION": self.environment["POWERCONTEXT_ZCODE_AUTHORIZATION"],
                "POWERCONTEXT_PI_SCOPE_ID": self.scope_id,
                "POWERCONTEXT_PI_CAPTURE_PROMPTS": "false",
            }
            version = self.process([str(self.node), str(cli), "--version"], environment=environment)
            assert version.returncode == 0, "pi_version_unavailable"
            self.summary["hosts"][0]["version"] = version.stdout.strip()
            files = self.process([self.git, "-C", str(REPOSITORY), "ls-files", "integrations/pi/plugins/powercontext"])
            assert files.returncode == 0 and files.stdout.strip(), "pi_integration_files_unavailable"
            digest = sha256()
            for name in files.stdout.splitlines():
                digest.update(name.encode() + b"\0" + (REPOSITORY / name).read_bytes())
            self.summary["hosts"][0]["integration_digest"] = digest.hexdigest()
            shutil.copyfile(FIXTURES / "verify.py", self.workspace / "verify.py")
            shutil.copyfile(FIXTURES / "task.py", self.workspace / "task.py")
            contract = {
                "schema": "powercontext.work-contract.v1",
                "trust": "untrusted_input",
                "objective": "Implement increment and transfer its verification.",
                "facts": [],
                "in_scope": ["Implement task.py and verify it."],
                "exclusions": ["No deployment."],
                "completion_criteria": ["Receiver runs verify.py and records its actual result."],
                "authorization_notes": ["Only this disposable fixture workspace is authorized."],
                "open_questions": [],
            }

            def capture() -> dict[str, Any]:
                # Inspect the file written by Pi, not an assumed successful Write result.
                assert (self.workspace / "task.py").read_text() == IMPLEMENTATION, "sender_partial_work_missing"
                return {
                    "source_id": "implementation-evidence",
                    "content": json.dumps({
                        "file": "task.py",
                        "implementation_digest": sha256((self.workspace / "task.py").read_bytes()).hexdigest(),
                        "next_command": "python verify.py",
                        "verification": "not_run",
                    }),
                    "metadata": {"kind": "acceptance-evidence"},
                }

            def handoff() -> dict[str, Any]:
                self.source = model_value(self.sender_model, "source")["source"]
                citation = {"kind": "source", "source_ref": self.source}
                return {
                    "source_id": "sender-boundary",
                    "handoff": {
                        "schema": "powercontext.current-work-handoff.v1",
                        "trust": "untrusted_input",
                        "objective": contract["objective"],
                        "state": [
                            {
                                "text": "task.py is implemented; receiver verification is pending.",
                                "basis": "verified",
                                "evidence": [citation],
                            }
                        ],
                        "disposition": "continuable",
                        "next_action": {
                            "text": "Run python verify.py in the live workspace.",
                            "basis": "verified",
                            "evidence": [citation],
                        },
                        "omissions": ["Sender has not run verification."],
                    },
                }

            self.sender_model.plan([
                ("pc_work_contract", {"source_id": "sender-contract", "contract": contract}),
                ("write", {"path": "task.py", "content": IMPLEMENTATION}),
                ("pc_capture_source", capture),
                ("pc_handoff_current", handoff),
                ("pc_handoff_commit", lambda: {"handoff": model_value(self.sender_model, "handoff")["handoff"]}),
            ])
            assert self.node is not None
            sender = PiHost(self.node, cli, PI_PLUGIN, self.workspace, environment)
            try:
                self.sender_session = sender.prompt(
                    "Implement increment in task.py, save exact evidence and explicitly commit a durable handoff. "
                    "Do not run verification. Private session-only marker: " + self.private_marker
                )
                assert set(sender.confirmations) == sender.allowed, "pi_write_confirmations_missing"
                self.revision = model_value(self.sender_model, "reference")["reference"]
                assert self.revision["family"] == "handoff", "sender_commit_missing"
                assert not (self.workspace / "receiver-action.json").exists(), "sender_executed_receiver_action"
                assert not list(home.rglob("*.jsonl")), "sender_transcript_persisted"
                evidence.append(
                    self.evidence(
                        "sender",
                        {
                            "scope_id": self.scope_id,
                            "revision": self.revision,
                            "evidence_source": self.source,
                            "partial_work_digest": sha256((self.workspace / "task.py").read_bytes()).hexdigest(),
                            "durable_write_confirmations": sender.confirmations,
                            "verification": "not_run",
                            "transcript_persisted": False,
                        },
                    )
                )
            finally:
                sender.close()

    def coverage(self) -> dict[str, Any]:
        # Inspect the public Runtime projection on the actual Server's owning loop.
        # Work Continuity does not currently expose an HTTP read operation.
        assert self.observer.event_loop is not None
        application = cast(FastAPI, self.observer.app).state.application
        value = asyncio.run_coroutine_threadsafe(
            application.work.for_scope(self.scope_id).continuity(ArtifactRef.model_validate(self.revision)),
            self.observer.event_loop,
        ).result(timeout=15)
        return value.coverage.model_dump(mode="json")

    def receiver(self) -> None:
        with self.scenario(4) as evidence:
            assert self.model is not None
            if self.case == "diverged":
                (self.workspace / "task.py").write_text(IMPLEMENTATION.replace("+ 1", "- 1"), encoding="utf-8")
            elif self.case == "unavailable":
                # Deliberate storage-loss fault in this disposable SQLite fixture only.
                with sqlite3.connect(self.root / "server.db") as database:
                    database.execute(
                        "DELETE FROM pc_sources WHERE scope_id = ? AND source_type = ? AND source_id = ?",
                        (self.scope_id, self.source["name"], self.source["source_id"]),
                    )
            execution_requested = self.case != "unauthorized"
            request = (
                f"Continue scope {self.scope_id} from exact handoff {json.dumps(self.revision)}. "
                "Re-resolve evidence and inspect the live workspace before acknowledging. "
                + (
                    "This current request authorizes running verify.py only in this fixture workspace."
                    if execution_requested
                    else "This current request forbids executing verify.py; record a declined receipt."
                )
            )
            start = len(self.wire.records)
            first = self.invoke(
                request,
                write=True,
                actions=[
                    ("continue_handoff", {"scope_id": self.scope_id, "selection": "latest"}),
                    ("Read", {"file_path": str(self.workspace / "task.py")}),
                ],
            )
            self.receiver_session = first["sessionId"]
            resolution = model_value(self.model, "evidence_checks")
            assert resolution == self.wire.result("continue_handoff", start), "receiver_resolution_changed_in_delivery"
            frozen = resolution["selected_revision"]
            assert frozen == self.revision, "latest_not_frozen_to_transferred_revision"
            delivered = "\n".join(strings(self.model.requests))
            authorized = (
                "This current request authorizes running verify.py only in this fixture workspace." in delivered
            )
            live_matches = "return value + 1" in delivered and "return value - 1" not in delivered
            available = all(check["status"] == "available" for check in resolution["evidence_checks"])
            status = (
                "accepted"
                if available and live_matches and authorized
                else "needs_clarification"
                if authorized
                else "declined"
            )
            checks = {
                "live_state": "confirmed" if live_matches else "mismatch",
                "capability": "confirmed",
                "authorization": "confirmed" if authorized else "insufficient",
            }
            principal = self.client.get("/v1/access/me")
            principal.raise_for_status()
            acknowledgement = {
                "scope_id": self.scope_id,
                "selection": "exact",
                "revision": frozen,
                "source_id": "receiver-receipt",
                "receiver": principal.json()["principal"]["id"],
                "receiver_checks": checks,
                "status": status,
                "message": None
                if status == "accepted"
                else "Live workspace, evidence or current execution authorization does not permit continuation.",
            }
            actions: list[ToolAction] = []
            if not available:
                actions.append((
                    "acknowledge_handoff",
                    {**acknowledgement, "source_id": "rejected-acceptance", "status": "accepted"},
                ))
            actions.append(("acknowledge_handoff", acknowledgement))
            receipt_start = len(self.wire.records)
            self.invoke(
                "Record the receiver decision for the exact inspected revision.",
                write=True,
                resume=self.receiver_session,
                actions=actions,
            )
            if not available:
                rejected = self.wire.calls("acknowledge_handoff", receipt_start)[0]
                assert rejected["response"]["result"].get("isError"), "unavailable_evidence_accepted"
            self.receipt = self.wire.result("acknowledge_handoff", receipt_start)["receipt"]["source"]
            actual = self.client.get(f"/v1/scopes/{self.scope_id}/sources/content/{self.receipt['source_id']}")
            actual.raise_for_status()
            stored = json.loads(actual.json()["content"])
            assert stored["status"] == status and stored["receiver_checks"] == checks, (
                "receiver_observations_not_preserved"
            )
            assert stored["trust"] == "untrusted_observation", "receiver_checks_trusted"
            expected = {
                "accepted": "accepted",
                "diverged": "needs_clarification",
                "unavailable": "needs_clarification",
                "unauthorized": "declined",
            }[self.case]
            assert status == expected, "receiver_did_not_react_to_live_observations"
            pending = self.coverage()
            assert pending["transfer_state"] == status
            assert pending["outcome_state"] == ("awaiting_outcome" if status == "accepted" else "not_expected")
            assert not (self.workspace / "receiver-action.json").exists(), "acknowledgement_executed_next_action"
            evidence.append(
                self.evidence(
                    "receiver",
                    {
                        "revision": frozen,
                        "receipt": self.receipt,
                        "status": status,
                        "receiver_checks": checks,
                        "trust": stored["trust"],
                        "coverage": pending,
                        "evidence_status": stored["evidence_status"],
                        "message": stored["message"],
                        "unavailable_acceptance_rejected": not available,
                        "next_action_executed": False,
                    },
                )
            )

    def outcome(self) -> None:
        with self.scenario(5) as evidence:
            assert self.model is not None
            if self.case == "accepted":
                value = {
                    "schema": "powercontext.task-outcome.v1",
                    "trust": "untrusted_observation",
                    "objective": "Implement increment and transfer its verification.",
                    "status": "unknown",
                    "summary": "An unlinked result cannot complete this transfer.",
                    "observations": [{"text": "No receiver action has run yet.", "basis": "declared", "evidence": []}],
                    "checks": [],
                    "produced_artifacts": [],
                    "remaining_work": ["Run verification."],
                }
                self.invoke(
                    "Record this unlinked observation without claiming completion.",
                    write=True,
                    resume=self.receiver_session,
                    actions=[
                        (
                            "record_task_outcome",
                            {"scope_id": self.scope_id, "source_id": "unlinked-outcome", "outcome": value},
                        )
                    ],
                )
                assert self.coverage()["outcome_state"] == "awaiting_outcome", "unlinked_outcome_covered_receipt"
                # The native host executes the actual command; the fixture never runs it itself.
                # The native shell may resolve a different Python after login-shell setup.
                # Forward slashes and shell quoting also work in the pinned host's Windows Git Bash.
                command = shlex.join([Path(sys.executable).as_posix(), "verify.py"])

                def outcome() -> dict[str, Any]:
                    assert self.model is not None
                    result_path = self.workspace / "receiver-action.json"
                    assert result_path.is_file(), "receiver_action_result_missing"
                    result = json.loads(result_path.read_text())
                    assert result == {
                        "status": "passed",
                        "implementation_digest": sha256((self.workspace / "task.py").read_bytes()).hexdigest(),
                    }, "receiver_action_result_invalid"
                    actual = model_value(self.model, "implementation_digest")
                    assert actual["status"] == "passed", "native_command_result_not_delivered"
                    return {
                        "scope_id": self.scope_id,
                        "source_id": "receiver-outcome",
                        "outcome": {
                            **value,
                            "status": "succeeded",
                            "summary": "Receiver ran verification in the live workspace.",
                            "handoff_receipt_ref": self.receipt,
                            "observations": [
                                {"text": "All three increment assertions passed.", "basis": "declared", "evidence": []}
                            ],
                            "checks": [
                                {"name": "native verify.py", "status": "passed", "basis": "declared", "evidence": []},
                                {
                                    "name": "live model qualification",
                                    "status": "skipped",
                                    "basis": "declared",
                                    "evidence": [],
                                },
                            ],
                            "remaining_work": [],
                        },
                    }

                self.invoke(
                    "Perform the currently authorized next action and record its actual result.",
                    write=True,
                    resume=self.receiver_session,
                    actions=[("Bash", {"command": command}), ("record_task_outcome", outcome)],
                )
                assert self.coverage()["outcome_state"] == "covered", "linked_outcome_did_not_cover_receipt"
                result = self.wire.result("record_task_outcome")["source"]
                candidate = self.post(
                    "/v1/experience/propose",
                    {
                        "scope_id": self.scope_id,
                        "proposal": {
                            "situation": "Work changes hosts without a transcript.",
                            "action": "Inspect exact evidence and live state.",
                            "outcome": "The authorized verification completed.",
                            "lesson": "Accept and record the exact transfer before linking its result.",
                        },
                        "source_refs": [result],
                        "artifact_refs": [],
                    },
                )
                assert candidate["result_artifact"] is None, "candidate_was_approved"
                evidence.append(
                    self.evidence(
                        "outcome",
                        {
                            "outcome_source": result,
                            "receipt": self.receipt,
                            "coverage": self.coverage(),
                            "candidate_id": candidate["candidate_id"],
                            "candidate_status": candidate["status"],
                            "approved_artifact": None,
                            "checks": ["passed", "skipped"],
                            "publication_installation_execution": "not_requested",
                        },
                    )
                )
            else:
                assert not (self.workspace / "receiver-action.json").exists(), "rejected_handoff_executed_old_action"
                assert not self.wire.calls("record_task_outcome"), "rejected_handoff_claimed_completion"
                evidence.append(
                    self.evidence("no-execution", {"next_action_executed": False, "outcome_recorded": False})
                )
            assert self.sender_session != self.receiver_session, "sender_session_reused"
            assert self.private_marker not in json.dumps(self.model.requests), "sender_transcript_leaked_to_receiver"
            sources = self.client.get(f"/v1/scopes/{self.scope_id}/sources")
            sources.raise_for_status()
            assert self.private_marker not in sources.text, "sender_transcript_captured_as_evidence"
            evidence.append(
                self.evidence(
                    "transcript-isolation",
                    {
                        "different_hosts": True,
                        "different_sessions": True,
                        "sender_transcript_persisted": False,
                        "private_marker_in_receiver_requests": False,
                        "private_marker_in_powercontext_sources": False,
                    },
                )
            )

    def run(self) -> Path:
        self.root.mkdir(parents=True, exist_ok=False)
        try:
            with ExitStack() as stack:
                self.setup(stack)
                self.manifest()
                self.sender(stack)
                self.receiver()
                self.outcome()
            assert all(item["status"] == "passed" for item in self.summary["scenarios"]), (
                "cross_host_scenarios_incomplete"
            )
            self.summary["overall_status"] = "passed"
        except Exception as error:
            code = error_code(error)
            status = "failed"
            if isinstance(error, subprocess.TimeoutExpired) or "timeout" in code or "deadline_exceeded" in code:
                status = "timed_out"
            elif code.startswith("prerequisite_"):
                status = "unavailable"
            failed = next((item for item in self.summary["scenarios"] if item["status"] == "failed"), None)
            if failed:
                failed["status"] = status
            self.summary.update(
                overall_status=status, stable_error_code=code, failure_stage=failed["id"] if failed else "prerequisites"
            )
            raise
        finally:
            self.summary["finished_at"] = datetime.now(UTC).isoformat()
            write_json(self.root / "summary.json", self.summary)
        return self.root / "summary.json"
