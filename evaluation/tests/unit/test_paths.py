from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from powercontext_eval.models import Arm
from powercontext_eval.paths import EvaluationPaths


def test_evaluation_paths_separate_ephemeral_work_from_retained_artifacts(tmp_path: Path) -> None:
    paths = EvaluationPaths(root=tmp_path, run_id="run-01")

    assert paths.arm_work(Arm.ON) == tmp_path / "work" / "run-01" / "on"
    assert paths.arm_artifacts(Arm.ON) == tmp_path / "runs" / "run-01" / "arms" / "on"
    with pytest.raises(ValueError):
        paths.arm_work(Arm.ON).relative_to(paths.run_artifacts)


@pytest.mark.parametrize(
    "run_id",
    [
        ".",
        "..",
        "C:",
        "C:foo",
        "run/01",
        r"run\01",
        "run..01",
    ],
)
def test_evaluation_paths_reject_unsafe_run_ids(tmp_path: Path, run_id: str) -> None:
    with pytest.raises(ValueError):
        EvaluationPaths(root=tmp_path, run_id=run_id)


def test_evaluation_paths_are_frozen(tmp_path: Path) -> None:
    paths = EvaluationPaths(root=tmp_path, run_id="run-01")

    with pytest.raises(FrozenInstanceError):
        paths.run_id = "run-02"  # ty: ignore[invalid-assignment]


def test_evaluation_paths_do_not_create_directories(tmp_path: Path) -> None:
    root = tmp_path / "evaluation-root"

    EvaluationPaths(root=root, run_id="run-01")

    assert not root.exists()
