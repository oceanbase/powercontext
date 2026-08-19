from __future__ import annotations

import json
import stat
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

from powercontext_eval.artifacts import ArtifactStore, SecretDetected
from powercontext_eval.benchmarks.swebench_pro.evaluator import (
    OfficialEvaluation,
    TestGroupResult,
)
from powercontext_eval.context_trace import write_context_trace
from powercontext_eval.models import Arm

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RECORDER = REPOSITORY_ROOT / "evaluation" / "scripts" / "record_codex_jsonl.py"


def _write_jsonl(path: Path, values: list[dict[str, object]]) -> Path:
    path.write_text("".join(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n" for value in values))
    return path


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def _parse_utc(value: object) -> datetime:
    assert isinstance(value, str)
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo == UTC
    return parsed


def test_recorder_preserves_stdout_bytes_and_appends_timestamped_events(tmp_path: Path) -> None:
    raw = (
        b'{"type":"thread.started","thread_id":"thread-1"}\n'
        b'{"type":"agent_message","message":"\xe5\xae\x8c\xe6\x88\x90"}\n'
    )
    emitter = tmp_path / "emit.py"
    emitter.write_bytes(b"import sys\nsys.stdout.buffer.write(" + repr(raw).encode() + b")\nsys.stdout.flush()\n")
    sidecar = tmp_path / "observed.jsonl"

    completed = subprocess.run(
        [
            sys.executable,
            str(RECORDER),
            "--sidecar",
            str(sidecar),
            "--",
            sys.executable,
            str(emitter),
        ],
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert completed.stdout == raw
    observed = _read_jsonl(sidecar)
    assert [event["sequence"] for event in observed] == [1, 2]
    assert [event["event"] for event in observed] == [
        {"type": "thread.started", "thread_id": "thread-1"},
        {"type": "agent_message", "message": "完成"},
    ]
    assert all(_parse_utc(event["observed_at"]) for event in observed)
    assert stat.S_IMODE(sidecar.stat().st_mode) == 0o600


def test_context_trace_merges_prompt_codex_injections_and_official_result_in_time_order(tmp_path: Path) -> None:
    codex = _write_jsonl(
        tmp_path / "codex-observed.jsonl",
        [
            {
                "sequence": 1,
                "observed_at": "2026-07-29T08:10:11.100000Z",
                "event": {"type": "item.started", "item": {"type": "command_execution", "command": "rg namespace"}},
            },
            {
                "sequence": 2,
                "observed_at": "2026-07-29T08:10:11.300000Z",
                "event": {"type": "agent_message", "message": "Implemented the fix."},
            },
        ],
    )
    injections = _write_jsonl(
        tmp_path / "injections.jsonl",
        [
            {
                "event_type": "powercontext_injection",
                "observed_at": "2026-07-29T08:10:11.200000Z",
                "query": "fix namespace refresh",
                "injected_text": "PowerContext recalled one decision.",
                "hits": [
                    {
                        "citation": {"entry_id": "decision-1", "revision": 2},
                        "text": "Refresh namespace after writes.",
                        "score": 0.91,
                        "matched_by": ["lexical", "semantic"],
                    }
                ],
                "scope_id": "eval:run-1:on",
                "session_id": "session-1",
                "turn_id": "turn-2",
            }
        ],
    )
    official = OfficialEvaluation(
        instance_id="instance_owner__repo-b",
        resolved=False,
        raw_stdout="",
        raw_stderr="",
        patch_applied=True,
        fail_to_pass=TestGroupResult(0, 1, ("test_fix",)),
        pass_to_pass=TestGroupResult(1, 1, ()),
        log_excerpt="test_fix failed",
    )
    store = ArtifactStore(tmp_path / "result")

    output = write_context_trace(
        store,
        arm=Arm.ON,
        prompt=b"Fix arbitrary instance B",
        codex_sidecar=codex,
        injection_sidecar=injections,
        official=official,
        official_observed_at=datetime(2026, 7, 29, 8, 10, 11, 500000, tzinfo=UTC),
    )

    events = _read_jsonl(output)
    assert [event["sequence"] for event in events] == [1, 2, 3, 4, 5]
    assert [event["actor"] for event in events] == [
        "benchmark",
        "codex",
        "powercontext",
        "codex",
        "official_evaluator",
    ]
    assert [event["event_type"] for event in events] == [
        "benchmark_prompt",
        "item.started",
        "powercontext_injection",
        "agent_message",
        "official_evaluation",
    ]
    assert events[0]["input"] == {"prompt": "Fix arbitrary instance B"}
    assert events[1]["output"] == {
        "event": {"type": "item.started", "item": {"type": "command_execution", "command": "rg namespace"}}
    }
    assert events[2]["input"] == {
        "query": "fix namespace refresh",
        "scope_id": "eval:run-1:on",
        "session_id": "session-1",
        "turn_id": "turn-2",
    }
    assert events[2]["output"] == {
        "hits": [
            {
                "citation": {"entry_id": "decision-1", "revision": 2},
                "text": "Refresh namespace after writes.",
                "score": 0.91,
                "matched_by": ["lexical", "semantic"],
            }
        ],
        "injected_text": "PowerContext recalled one decision.",
    }
    assert events[4]["output"] == {
        "fail_to_pass": {"failed": ["test_fix"], "passed": 0, "total": 1},
        "log_excerpt": "test_fix failed",
        "pass_to_pass": {"failed": [], "passed": 1, "total": 1},
        "patch_applied": True,
        "resolved": False,
    }
    assert [event["elapsed_ms"] for event in events] == [0, 0, 100, 200, 400]
    assert all(event["arm"] == "on" for event in events)


def test_context_trace_rejects_secrets_without_publishing_a_timeline(tmp_path: Path) -> None:
    codex = _write_jsonl(
        tmp_path / "codex-observed.jsonl",
        [
            {
                "sequence": 1,
                "observed_at": "2026-07-29T08:10:11.100000Z",
                "event": {"type": "agent_message", "message": "leaked-eval-secret"},
            }
        ],
    )
    store = ArtifactStore(tmp_path / "result", forbidden_values=("eval-secret",))

    with pytest.raises(SecretDetected):
        write_context_trace(
            store,
            arm=Arm.OFF,
            prompt=b"Fix the task",
            codex_sidecar=codex,
            injection_sidecar=None,
            official=OfficialEvaluation("instance_owner__repo-b", True, "", ""),
            official_observed_at=datetime(2026, 7, 29, 8, 10, 12, tzinfo=UTC),
        )

    assert not (store.root / "context/timeline.jsonl").exists()
