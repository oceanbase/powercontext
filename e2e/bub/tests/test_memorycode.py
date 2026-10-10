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

from __future__ import annotations

import json
import runpy
import sys
import tomllib
from collections import Counter
from pathlib import Path

import pytest
from harbor.models.task.task import Task as HarborTask

from powercontext_e2e.catalog import load_tasks

_REPOSITORY = Path(__file__).resolve().parents[3]
_SCRIPTS = _REPOSITORY / "e2e" / "bub" / "scripts"
_GRADER = runpy.run_path(str(_SCRIPTS / "memorycode_grade.py"))
_GENERATOR = runpy.run_path(str(_SCRIPTS / "memorycode_tasks.py"))

_RULES = [["function argument", "^j_.*"], ["function decorator", ["validate", True]]]
_FOLLOWS_RULES = (
    "from pedantic import validate\n\n@validate\ndef dot(j_a, j_b):\n    return sum(x * y for x, y in zip(j_a, j_b))\n"
)
_IGNORES_RULES = "def dot(a, b):\n    return sum(x * y for x, y in zip(a, b))\n"


def _grade(tmp_path: Path, files: dict[str, str], rules: list = _RULES, outputs: int = 2) -> dict:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    for name, content in files.items():
        (workspace / name).write_text(content, encoding="utf-8")
    expected = tmp_path / "expected.json"
    expected.write_text(
        json.dumps({
            "outputs": [{"file": f"solution_{i}.py", "query": "function"} for i in range(1, outputs + 1)],
            "history_regex": rules,
        }),
        encoding="utf-8",
    )
    _GRADER["main"](expected, workspace, tmp_path)
    return json.loads((tmp_path / "reward.json").read_text(encoding="utf-8"))


def test_memorycode_passes_only_when_every_output_follows_every_latest_guideline(tmp_path: Path) -> None:
    assert _grade(tmp_path / "all", {"solution_1.py": _FOLLOWS_RULES, "solution_2.py": _FOLLOWS_RULES}) == {
        "reward": 1,
        "memorycode_score": 1.0,
        "applicable_checks": 4,
        "passed_checks": 4,
    }
    # The upstream macro score keeps partial credit; the trial reward does not.
    assert _grade(tmp_path / "half", {"solution_1.py": _FOLLOWS_RULES, "solution_2.py": _IGNORES_RULES}) == {
        "reward": 0,
        "memorycode_score": 0.5,
        "applicable_checks": 4,
        "passed_checks": 2,
    }


def test_memorycode_scores_a_missing_solution_as_code_that_follows_nothing(tmp_path: Path) -> None:
    # Upstream scores an answer without parseable code 0 on every guideline; a missing file is that answer here.
    assert _grade(tmp_path, {"solution_1.py": _FOLLOWS_RULES}) == {
        "reward": 0,
        "memorycode_score": 0.5,
        "applicable_checks": 4,
        "passed_checks": 2,
    }


def test_memorycode_does_not_score_a_guideline_for_objects_the_code_lacks(tmp_path: Path) -> None:
    # Upstream leaves out a guideline about methods for code without methods instead of failing it.
    rules = [*_RULES, ["method", "^m_.*"]]
    assert _grade(tmp_path / "functions", {"solution_1.py": _FOLLOWS_RULES}, rules=rules, outputs=1)["reward"] == 1

    # Code that defines none of the guidelines' objects has nothing scored, which does not count as a pass.
    # Upstream leaves such a dialogue out of its macro score, so the reward file leaves the score out.
    result = _grade(tmp_path / "constant", {"solution_1.py": "LIMIT = 3\n"}, rules=[["method", "^m_.*"]], outputs=1)
    assert result == {"reward": 0, "applicable_checks": 0, "passed_checks": 0}


def test_memorycode_grades_a_whole_file_even_when_a_docstring_holds_a_fenced_example(tmp_path: Path) -> None:
    # Upstream grades the first fenced block of a chat answer; a file that parses is graded whole.
    documented = _FOLLOWS_RULES.replace(
        "def dot(j_a, j_b):\n",
        'def dot(j_a, j_b):\n    """Dot product.\n\n    ```python\n    dot([1], [2])\n    ```\n    """\n',
    )
    fenced = f"```python\n{_FOLLOWS_RULES}```\n"

    assert _grade(tmp_path / "docstring", {"solution_1.py": documented}, outputs=1)["reward"] == 1
    assert _grade(tmp_path / "fenced", {"solution_1.py": fenced}, outputs=1)["reward"] == 1


def _dialogue(*sessions: str) -> dict:
    last = {
        "text": sessions[-1],
        "history_eval_query": ["function that adds two numbers", "Stack class with push and pop methods"],
        "history_regex": _RULES,
    }
    return {
        "context": {"mentee": "Pablo", "mentor": "Yuichi", "company": "DEVS"},
        "sessions": [{"text": text, "history_eval_query": [], "history_regex": []} for text in sessions[:-1]] + [last],
    }


def test_each_mentoring_session_is_an_agent_session_before_the_graded_recall(tmp_path: Path) -> None:
    task_dir = tmp_path / "memorycode-7"
    _GENERATOR["write_task"](
        task_dir, _dialogue("Yuichi: Start argument names with j_.", "Yuichi: Lunch?"), dialogue_id=7
    )

    steps = [step["name"] for step in tomllib.loads((task_dir / "task.toml").read_text())["steps"]]
    assert steps == ["session-1", "session-2", "recall"]
    assert "Start argument names with j_." in (task_dir / "steps" / "session-1" / "instruction.md").read_text()
    recall = (task_dir / "steps" / "recall" / "instruction.md").read_text()
    assert "/workspace/solution_1.py" in recall
    assert "/workspace/solution_2.py" in recall
    assert "j_" not in recall
    # Only the recall step's tests, which Harbor uploads for its verifier, hold the guidelines being checked.
    holding_rules = sorted(
        path.relative_to(task_dir).as_posix() for path in task_dir.rglob("*") if "^j_" in _text(path)
    )
    assert holding_rules == ["steps/recall/tests/expected.json"]


def test_generated_tasks_are_reproducible_so_manifests_can_pin_them(tmp_path: Path) -> None:
    dialogue = _dialogue("Yuichi: Start argument names with j_.", "Yuichi: Lunch?")
    for name in ("first", "second"):
        _GENERATOR["write_task"](tmp_path / name, dialogue, dialogue_id=7)

    assert HarborTask(tmp_path / "first").checksum == HarborTask(tmp_path / "second").checksum


def test_the_sample_draws_the_same_short_dialogues_for_every_session_count(tmp_path: Path) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    for dialogue_id in range(1, 361):
        sessions = 1 + dialogue_id % 6 if dialogue_id <= 210 else 30
        payload = {"sessions": [{} for _ in range(sessions)]}
        (dataset / f"dialogue_{dialogue_id}.json").write_text(json.dumps(payload), encoding="utf-8")

    sample = _GENERATOR["sample_dialogues"](dataset, per_session_count=3, seed=11)

    assert sample == _GENERATOR["sample_dialogues"](dataset, per_session_count=3, seed=11)
    assert all(dialogue <= 210 for dialogue in sample)
    assert Counter(1 + dialogue % 6 for dialogue in sample) == dict.fromkeys(range(1, 6), 3)


def test_an_empty_sample_is_rejected_before_touching_the_manifests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["memorycode_tasks.py", str(tmp_path), "--sample", "0"])

    with pytest.raises(SystemExit) as excinfo:
        _GENERATOR["main"]()

    assert excinfo.value.code == 2
    assert "--sample must be at least 1" in capsys.readouterr().err


def test_checked_in_manifests_draw_ten_dialogues_for_each_session_count() -> None:
    tasks = load_tasks(_REPOSITORY / "e2e" / "bub" / "paired-tasks" / "memorycode")

    assert len(tasks) == 50
    for count in range(1, 6):
        assert sum(f"memorycode-sessions-{count}" in task.categories for task in tasks) == 10


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.is_file() else ""
