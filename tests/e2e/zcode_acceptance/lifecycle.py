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

"""Real host lifecycle and fault injection around a real persistent PowerContext Server."""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from .host import NativeHost
from .protocol import CaptureResponseGate, strings

if TYPE_CHECKING:
    from .runner import AcceptanceRun


def runtime_faults(run: AcceptanceRun) -> None:
    with run.scenario(7) as evidence:
        marker = "Synthetic late capture " + run.run_id
        prompt_text = marker + ". Reply briefly without calling tools."
        gate = CaptureResponseGate(prompt_text)
        run.observer.capture_response_gate = gate
        try:
            result = run.invoke(prompt_text)
        finally:
            gate.release()
            run.observer.capture_response_gate = None
        accepted = gate.accepted.wait(max(0, min(5, run.budget - time.monotonic())))
        records = run.observations(result["sessionId"])
        prompt = next(item for item in records if item["event"] == "UserPromptSubmit")
        evidence.append(
            run.evidence(
                "late-capture-attempt",
                {
                    "capture": prompt["stages"]["capture"],
                    "accepted_receipt_within_budget": accepted,
                    "response_gate": gate.evidence(),
                },
            )
        )
        assert accepted, "late_capture_fault_did_not_hold_accepted_response"
        assert not gate.release_timed_out, "late_capture_response_gate_expired"
        assert prompt["stages"]["capture"]["state"] == "unknown", "late_write_not_observed_as_unknown"
        sources = run.client.get(f"/v1/scopes/{run.scope_id}/sources").json()["items"]
        captures = [item for item in sources if marker in item["content"]]
        assert len(captures) == 1, "late_capture_server_identity_missing"
        assert gate.receipt == {
            "source": {"name": "content", "source_id": captures[0]["source_id"]},
            "position": captures[0]["position"],
        }, "late_capture_gate_receipt_identity_mismatch"
        before = len(run.wire.received_paths)
        status = run.process([
            str(run.node),
            str(run.installed / "scripts/status.mjs"),
            "--cwd",
            str(run.workspace),
            "--session-id",
            result["sessionId"],
            "--data-dir",
            str(run.data_dir),
        ])
        observed = json.loads(status.stdout)
        assert status.returncode == 0 and observed["status"] == "observed"
        # Count request arrivals: an earlier bounded flush may finish during this read-only query.
        assert not any(path.startswith("/v1/") for path in run.wire.received_paths[before:]), (
            "status_requested_server_api"
        )
        assert observed["selection"] == "session_exact"

        # Both are actual independent CLI processes; controlled model emits no tool calls in this phase.
        if run.model:
            run.model.plan()
        args = [
            str(run.node),
            str(run.cli),
            "--cwd",
            str(run.workspace),
            "--mode",
            "plan",
            "--no-color",
            "--output-format",
            "stream-json",
            "--prompt",
            "Synthetic concurrent capture " + run.run_id + ". Reply briefly without tools.",
        ]
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run.process, args) for _ in range(2)]
            concurrent = [future.result() for future in futures]
        sessions = []
        for completed in concurrent:
            assert completed.returncode == 0, "concurrent_host_failed"
            events = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith("{")]
            sessions.append(next(event["sessionId"] for event in events if event.get("type") == "result"))
        assert len(set(sessions)) == 2
        assert all(
            any(
                item["event"] == "UserPromptSubmit" and item["completed_at"] is not None
                for item in run.observations(session)
            )
            for session in sessions
        )

        runtime = run.data_dir / "runtime"
        saved = run.data_dir / "runtime-acceptance-saved"
        runtime.rename(saved)
        runtime.write_text("synthetic non-directory fault", encoding="utf-8")
        try:
            marker = "Synthetic runtime storage unavailable " + run.run_id
            run.invoke(marker + ". Reply briefly without tools.")
            sources = run.client.get(f"/v1/scopes/{run.scope_id}/sources").json()["items"]
            assert any(marker in item["content"] for item in sources), "capture_blocked_by_observation_storage"
        finally:
            runtime.unlink()
            saved.rename(runtime)
        evidence.append(
            run.evidence(
                "runtime-faults",
                {
                    "late_response_injection": "accepted_receipt_held_until_host_turn_completed",
                    "capture_state": "unknown",
                    "accepted_source_readback": {"name": "content", "source_id": captures[0]["source_id"]},
                    "status_read_only": True,
                    "concurrent_sessions_distinct": True,
                    "ordinary_task_and_capture_survive_runtime_directory_fault": True,
                    "storage_fault_kind": "runtime_path_is_file",
                },
            )
        )


def session_lifecycle(run: AcceptanceRun) -> None:
    with run.scenario(8) as evidence:
        start = len(run.wire.records)
        initial = run.invoke("Synthetic lifecycle baseline. Reply briefly without calling tools.")
        resumed = run.invoke(
            "Resume this synthetic check. Reply briefly without calling tools.", resume=initial["sessionId"]
        )
        assert resumed["sessionId"] == initial["sessionId"]
        records = run.observations(initial["sessionId"])
        starts = [item for item in records if item["event"] == "SessionStart"]
        assert any(
            item["event_source"] == "startup" and item["stages"]["prepare"]["state"] == "skipped" for item in starts
        )
        restored = next(item for item in starts if item["event_source"] == "resume")
        assert restored["scope_id"] == run.scope_id
        assert restored["stages"]["prepare"]["state"] == "ready"
        assert restored["stages"]["context_output"]["state"] == "emitted"
        assert restored["stages"]["capture"]["state"] == "skipped"
        assert not any(item["path"] == "/v1/memory/flush" for item in run.wire.records[start:])
        if run.model:
            assert any(
                "PowerContext context for this request." in text for text in strings(run.model.requests[-1]["messages"])
            )
        compacted = run.invoke("/compact", resume=initial["sessionId"])
        assert compacted["sessionId"] == initial["sessionId"]
        compact_events = [
            item
            for item in run.observations(initial["sessionId"])
            if item["event"] == "SessionStart" and item["event_source"] == "compact"
        ]
        compact_support = "observed" if compact_events else "unsupported"
        if compact_events:
            assert compact_events[-1]["stages"]["context_output"]["state"] == "emitted"
        else:
            run.summary["limitations"].append(
                "Actual /compact completed without a SessionStart compact Hook; prompt recall remains available."
            )
        run.environment["POWERCONTEXT_ZCODE_BOUNDARY_FLUSH"] = "true"
        skipped_claims = 0
        try:
            for _ in range(3):
                start = len(run.wire.arrivals)
                bounded = run.invoke("Synthetic opt-in Stop boundary. Reply briefly without tools.")
                stop = next(item for item in run.observations(bounded["sessionId"]) if item["event"] == "Stop")
                # The Server may still be processing an accepted request after the bounded client times out.
                flushes = [item for item in run.wire.arrivals[start:] if item["path"] == "/v1/memory/flush"]
                if stop["stages"]["flush"].get("reason") != "claim_busy":
                    break
                assert not flushes, "busy_claim_sent_flush"
                skipped_claims += 1
        finally:
            run.environment.pop("POWERCONTEXT_ZCODE_BOUNDARY_FLUSH")
        assert len(flushes) == 1 and flushes[0]["request"]["scope_id"] == run.scope_id
        assert stop["completed_at"] and stop["completed_at"] - stop["started_at"] <= 1300
        assert stop["stages"]["flush"]["state"] in {"cursor_reached", "pending", "unknown"}
        evidence.append(
            run.evidence(
                "session-lifecycle",
                {
                    "startup_prepare_skipped": True,
                    "resume_context_emitted": True,
                    "resume_capture_skipped": True,
                    "default_flush_count": 0,
                    "compact_hook": compact_support,
                    "compact_completed": True,
                    "opt_in_flush_count": len(flushes),
                    "busy_claim_skips": skipped_claims,
                    "flush_scope_id": run.scope_id,
                    "stop_duration_ms": stop["completed_at"] - stop["started_at"],
                    "flush_state": stop["stages"]["flush"]["state"],
                    "clear_event": "not_run",
                },
            )
        )


def server_recovery(run: AcceptanceRun) -> None:
    with run.scenario(9) as evidence:
        host = NativeHost(run)
        try:
            session = host.create()
            if run.model:
                run.model.plan([("list_scopes", {})])
            start = len(run.wire.records)
            host.prompt(session, "Call PowerContext list_scopes using native MCP only.", operations={"list_scopes"})
            run.wire.result("list_scopes", start)
            run.server.stop()
            if run.model:
                run.model.plan()
            outage = host.prompt(
                session, "In one sentence, what does a deployment rollback marker identify? Do not call tools."
            )
            assert outage["response"], "ordinary_model_task_failed_during_outage"
            run.server.start()
            if run.model:
                run.model.plan([("list_scopes", {})])
            start = len(run.wire.records)
            recovered = host.prompt(
                session, "Call PowerContext list_scopes again using its native MCP tool.", operations={"list_scopes"}
            )
            automatic_recovery = bool(run.wire.calls("list_scopes", start))
            recovery_mode = "automatic"
            if not automatic_recovery:
                host.request(
                    "mcp/list",
                    {
                        "workspace": {"workspacePath": str(run.workspace), "workspaceKey": "acceptance-" + run.run_id},
                        "mode": "connect",
                    },
                )
                if run.model:
                    run.model.plan([("list_scopes", {})])
                start = len(run.wire.records)
                recovered = host.prompt(
                    session,
                    "After the MCP refresh, call PowerContext list_scopes using native MCP only.",
                    operations={"list_scopes"},
                )
                recovery_mode = "native_mcp_refresh"
                if not run.wire.calls("list_scopes", start):
                    host.request("session/close", {"sessionId": session})
                    resumed = host.request(
                        "session/resume",
                        {
                            "sessionId": session,
                            "workspace": {
                                "workspacePath": str(run.workspace),
                                "workspaceKey": "acceptance-" + run.run_id,
                            },
                            "thoughtLevel": run.host_selection["options"]["reasoningLevel"],
                        },
                    )
                    assert resumed["session"]["sessionId"] == session
                    if run.model:
                        run.model.plan([("list_scopes", {})])
                    start = len(run.wire.records)
                    recovered = host.prompt(
                        session,
                        "Call PowerContext list_scopes using native MCP after resuming this same session.",
                        operations={"list_scopes"},
                    )
                    recovery_mode = "session_close_resume"
                run.summary["limitations"].append(
                    "Automatic MCP recovery was unavailable after Server restart; recovery required "
                    + recovery_mode
                    + "."
                )
            inventory = run.wire.result("list_scopes", start)
            assert any(item["scope_id"] == run.scope_id for item in inventory["items"])
            assert outage["sessionId"] == recovered["sessionId"] == session
            evidence.append(
                run.evidence(
                    "server-recovery",
                    {
                        "server_was_stopped": True,
                        "same_host_process": True,
                        "same_session": True,
                        "ordinary_answer_during_outage": True,
                        "native_mcp_after_restart": True,
                        "automatic_mcp_recovery": automatic_recovery,
                        "native_refresh_required": not automatic_recovery,
                        "recovery_mode": recovery_mode,
                    },
                )
            )
        finally:
            host.close()
            if run.server.thread and not run.server.thread.is_alive():
                run.server.start()
