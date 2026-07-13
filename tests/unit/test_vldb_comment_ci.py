import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "vldb-comment.yml"


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _target_validation_script() -> str:
    match = re.search(
        r'python3 - "\$PR_JSON" "\$WORKFLOW_HEAD_REPO" "\$WORKFLOW_HEAD_SHA" <<\'PY\'\n'
        r"(?P<script>.*?)\n          PY",
        _workflow_text(),
        flags=re.DOTALL,
    )
    assert match is not None
    return textwrap.dedent(match.group("script"))


def _run_target_validation(
    tmp_path: Path,
    *,
    workflow_repo: str,
    workflow_sha: str,
    pr_repo: str,
    pr_sha: str,
) -> subprocess.CompletedProcess[str]:
    pr_path = tmp_path / "pr.json"
    pr_path.write_text(
        json.dumps({"head": {"repo": {"full_name": pr_repo}, "sha": pr_sha}}),
        encoding="utf-8",
    )
    return subprocess.run(
        [
            sys.executable,
            "-c",
            _target_validation_script(),
            str(pr_path),
            workflow_repo,
            workflow_sha,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_vldb_comment_skips_when_event_has_no_pr_number() -> None:
    workflow = _workflow_text()

    assert "PR_NUMBER_PATH" not in workflow
    assert "using artifact PR number" not in workflow
    assert "PR number missing from workflow_run event, skip PR comment" in workflow


def test_vldb_comment_accepts_exact_pr_head(tmp_path: Path) -> None:
    result = _run_target_validation(
        tmp_path,
        workflow_repo="oceanbase/powermem",
        workflow_sha="abc123",
        pr_repo="oceanbase/powermem",
        pr_sha="abc123",
    )

    assert result.returncode == 0, result.stderr


def test_vldb_comment_rejects_pr_head_sha_mismatch(tmp_path: Path) -> None:
    result = _run_target_validation(
        tmp_path,
        workflow_repo="oceanbase/powermem",
        workflow_sha="workflow-sha",
        pr_repo="oceanbase/powermem",
        pr_sha="different-pr-sha",
    )

    assert result.returncode != 0
    assert "PR head sha mismatch" in result.stderr


def test_vldb_comment_rejects_pr_head_repo_mismatch(tmp_path: Path) -> None:
    result = _run_target_validation(
        tmp_path,
        workflow_repo="contributor/fork",
        workflow_sha="abc123",
        pr_repo="other/fork",
        pr_sha="abc123",
    )

    assert result.returncode != 0
    assert "PR head repo mismatch" in result.stderr
