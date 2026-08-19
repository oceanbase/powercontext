from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[2] / "scripts" / "resume_source559_on_m0.sh"


def _claim_observation(status: str, active: int, running: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), "--claim-observation", status, str(active), str(running)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_queued_source_with_active_worker_is_reconciled_after_pause() -> None:
    observed = _claim_observation("queued", 1, 1)

    assert observed.returncode == 0
    assert observed.stdout.strip() == "reconcile"
    assert "other" not in observed.stdout.lower()


def test_source_running_or_succeeded_is_claimed_immediately() -> None:
    for status in ("running", "succeeded"):
        observed = _claim_observation(status, 1, 1)
        assert observed.returncode == 0
        assert observed.stdout.strip() == "claimed"


def test_queued_source_with_idle_worker_keeps_waiting() -> None:
    observed = _claim_observation("queued", 0, 0)

    assert observed.returncode == 0
    assert observed.stdout.strip() == "wait"
