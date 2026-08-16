import pytest
from pydantic import ValidationError

from powercontext_eval.web.batches import BatchStatus
from powercontext_eval.web.controls import (
    BatchControlIntent,
    BatchPauseReason,
    BatchPreviewRequest,
    derive_controlled_batch_status,
)
from powercontext_eval.web.models import TaskStatus


def test_visible_batch_lifecycle_waits_for_running_task_before_pausing() -> None:
    assert (
        derive_controlled_batch_status(
            intent=BatchControlIntent.PAUSE,
            task_statuses=(TaskStatus.RUNNING, TaskStatus.QUEUED),
        )
        is BatchStatus.PAUSING
    )
    assert (
        derive_controlled_batch_status(
            intent=BatchControlIntent.PAUSE,
            task_statuses=(TaskStatus.SUCCEEDED, TaskStatus.QUEUED),
        )
        is BatchStatus.PAUSED
    )


def test_visible_batch_lifecycle_waits_for_running_task_before_cancelling() -> None:
    assert (
        derive_controlled_batch_status(
            intent=BatchControlIntent.CANCEL,
            task_statuses=(TaskStatus.RUNNING, TaskStatus.QUEUED),
        )
        is BatchStatus.CANCELLING
    )
    assert (
        derive_controlled_batch_status(
            intent=BatchControlIntent.CANCEL,
            task_statuses=(TaskStatus.SUCCEEDED, TaskStatus.CANCELLED),
        )
        is BatchStatus.CANCELLED
    )


def test_run_intent_preserves_existing_queue_running_and_completion_meaning() -> None:
    assert (
        derive_controlled_batch_status(
            intent=BatchControlIntent.RUN,
            task_statuses=(TaskStatus.QUEUED, TaskStatus.QUEUED),
        )
        is BatchStatus.QUEUED
    )
    assert (
        derive_controlled_batch_status(
            intent=BatchControlIntent.RUN,
            task_statuses=(TaskStatus.SUCCEEDED, TaskStatus.QUEUED),
        )
        is BatchStatus.RUNNING
    )
    assert (
        derive_controlled_batch_status(
            intent=BatchControlIntent.RUN,
            task_statuses=(TaskStatus.SUCCEEDED, TaskStatus.FAILED),
        )
        is BatchStatus.COMPLETED
    )


def test_preview_request_defaults_to_eighty_percent() -> None:
    request = BatchPreviewRequest(powercontext_ref="latest")

    assert request.usage_pause_percent == 80


@pytest.mark.parametrize("value", [0, 101, True, 80.5, "80"])
def test_preview_request_strictly_rejects_invalid_threshold(value: object) -> None:
    with pytest.raises(ValidationError):
        BatchPreviewRequest(powercontext_ref="latest", usage_pause_percent=value)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("value", ["main", "branch:main", "commit:abc"])
def test_preview_request_reuses_the_web_revision_boundary(value: str) -> None:
    with pytest.raises(ValidationError):
        BatchPreviewRequest(powercontext_ref=value)


def test_pause_reasons_are_stable_public_values() -> None:
    assert [reason.value for reason in BatchPauseReason] == [
        "user",
        "usage_threshold",
        "usage_unavailable",
        "quota_limit",
        "infrastructure_failure",
        "codex_capacity",
        "resource_pressure",
    ]
