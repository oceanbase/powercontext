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

"""Generate paired continuation workloads from MemoryCode dialogues.

MemoryCode (Rakotonirina et al., "From Tools to Teammates: Evaluating LLMs in Multi-Session Coding Interactions",
ACL 2025; https://github.com/Cohere-Labs-Community/MemoryCode, Apache-2.0) gives a mentee coding guidelines across
mentoring sessions, updates some of them, and mixes in unrelated topics. Each dialogue becomes one Harbor task: one
agent session per mentoring session, then a recall session that writes code for the dialogue's history eval queries
and is graded with MemoryCode's own checks against the latest guidelines.

Generate the tasks for the checked-in manifests from a checkout at the pinned revision::

    git clone https://github.com/Cohere-Labs-Community/MemoryCode <dir>
    git -C <dir> checkout 1ab87e119b2f9a498de8075219e1c07f6041b394
    uv run --project e2e/bub python e2e/bub/scripts/memorycode_tasks.py <dir>

The tasks are written to ``e2e/bub/memorycode-tasks``, which git ignores, and the command fails when a generated task's
checksum differs from its manifest. After a change to the task format or the grader, which every task copies, ``--pin``
writes the manifests' new checksums for the same dialogues. ``--sample N`` instead draws N short dialogues of each
mentoring-session count from 1 to 5 with ``--seed`` and replaces the manifests. Both write the manifests only after
every task was generated.
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import subprocess
from collections import defaultdict
from pathlib import Path

from harbor.models.task.task import Task

from powercontext_e2e.catalog import load_tasks

REVISION = "1ab87e119b2f9a498de8075219e1c07f6041b394"
REPOSITORY = Path(__file__).resolve().parents[3]
TASKS = Path("e2e/bub/memorycode-tasks")
MANIFESTS = Path("e2e/bub/paired-tasks/memorycode")
ID_PREFIX = "memorycode-"
# Upstream's evaluate_model_output.py reports dialogues 1-210 as short histories and the rest as long ones.
SHORT_DIALOGUES = range(1, 211)
SESSION_COUNTS = (1, 2, 3, 4, 5)
SEED = 1705
SCRIPTS = Path(__file__).resolve().parent
HEADER = """\
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
"""
SESSION_INSTRUCTION = """\
You are {mentee}, a software engineer at {company}. This is the transcript of today's session between you and your
mentor {mentor}:

{text}

No code is needed today. Reply with a one-sentence acknowledgement.
"""
RECALL_INSTRUCTION = """\
You are {mentee}, a software engineer at {company}. Your mentor {mentor} gave you coding guidelines in your earlier
sessions together. Write the following Python code. You must follow all the latest coding guidelines provided by your
mentor, including any possible updates. Do not provide example usage.

{queries}

Write only these files.
"""
SESSION_TEST = (
    "#!/bin/sh\n"
    + HEADER
    + """
set -eu

# Diagnostic only: the recall step's reward decides the trial.
echo 1 > /logs/verifier/reward.txt

# Harbor keeps the container for later sessions, so clear notes the agent wrote into the workspace: they would
# otherwise stand in for memory of the conversation.
find /workspace -mindepth 1 -delete || echo 'workspace reset incomplete' >&2
"""
)
RECALL_TEST = (
    "#!/bin/sh\n"
    + HEADER
    + """
set -eu

# Harbor reads whatever reward file is here after this script, so one the agent wrote must not survive a grader crash.
rm -f /logs/verifier/reward.json /logs/verifier/reward.txt
python3 /tests/grade.py /tests/expected.json /workspace /logs/verifier
"""
)
TASK_TOML = (
    HEADER
    + """
version = "1.3"
multi_step_reward_strategy = "final"

[agent]
timeout_sec = 600.0

[verifier]
timeout_sec = 60.0

[environment]
build_timeout_sec = 300.0
{steps}"""
)
DOCKERFILE = (
    HEADER
    + """
FROM python:3.12-slim-bookworm
WORKDIR /workspace
"""
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path, help=f"MemoryCode checkout at {REVISION}")
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--pin", action="store_true", help="write the checksums of the checked-in dialogues")
    selection.add_argument(
        "--sample", type=int, metavar="N", help="draw N dialogues per session count; replace manifests"
    )
    parser.add_argument("--seed", type=int, default=SEED, help=f"sampling seed for --sample (default {SEED})")
    args = parser.parse_args()
    if args.sample is not None and args.sample < 1:
        # A sample of zero would skip the manifest replacement and leave the previous sample in place.
        parser.error("--sample must be at least 1")

    revision = checkout_revision(args.source)
    if revision != REVISION:
        parser.error(f"the MemoryCode checkout is at {revision}, not the pinned {REVISION}")
    dataset = args.source / "dataset"
    tasks_dir = REPOSITORY / TASKS
    manifests_dir = REPOSITORY / MANIFESTS
    if args.sample is None:
        expected = {task.id: task.dataset.checksum for task in load_tasks(manifests_dir)}
        dialogue_ids = sorted(int(task_id.removeprefix(ID_PREFIX)) for task_id in expected)
    else:
        expected = {}
        dialogue_ids = sample_dialogues(dataset, per_session_count=args.sample, seed=args.seed)

    manifests: dict[str, str] = {}
    for dialogue_id in dialogue_ids:
        dialogue = load_dialogue(dataset, dialogue_id)
        task_id = f"{ID_PREFIX}{dialogue_id}"
        write_task(tasks_dir / task_id, dialogue, dialogue_id=dialogue_id)
        checksum = Task(tasks_dir / task_id).checksum
        if args.pin or args.sample is not None:
            manifests[task_id] = manifest(task_id, sessions=len(dialogue["sessions"]), checksum=checksum)
        elif expected[task_id] != checksum:
            parser.error(f"{task_id} differs from its manifest; run with --pin if the change is intended")
    if manifests:
        manifests_dir.mkdir(parents=True, exist_ok=True)
        for stale in manifests_dir.glob(f"{ID_PREFIX}*.yaml"):
            stale.unlink()
        for task_id, content in manifests.items():
            (manifests_dir / f"{task_id}.yaml").write_text(content, encoding="utf-8")
    print(f"Wrote {len(dialogue_ids)} MemoryCode tasks to {TASKS}")


def checkout_revision(source: Path) -> str:
    # The pinned revision is what the manifests' checksums were generated from.
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(source), "rev-parse", "HEAD"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def load_dialogue(dataset: Path, dialogue_id: int) -> dict:
    return json.loads((dataset / f"dialogue_{dialogue_id}.json").read_text(encoding="utf-8"))


def sample_dialogues(dataset: Path, *, per_session_count: int, seed: int) -> list[int]:
    """Draw the same number of short dialogues for each mentoring-session count, reproducibly."""

    by_count: dict[int, list[int]] = defaultdict(list)
    for dialogue_id in SHORT_DIALOGUES:
        by_count[len(load_dialogue(dataset, dialogue_id)["sessions"])].append(dialogue_id)
    rng = random.Random(seed)  # noqa: S311 - a reproducible sample, not a secret
    return sorted(dialogue for count in SESSION_COUNTS for dialogue in rng.sample(by_count[count], per_session_count))


def write_task(task_dir: Path, dialogue: dict, *, dialogue_id: int) -> None:
    """Write one agent session per mentoring session, then the graded recall session."""

    if task_dir.exists():
        shutil.rmtree(task_dir)
    names = _names(dialogue)
    step_names = [f"session-{index}" for index in range(1, len(dialogue["sessions"]) + 1)]
    for name, session in zip(step_names, dialogue["sessions"], strict=True):
        instruction = SESSION_INSTRUCTION.format(text=session["text"].strip(), **names)
        _write(task_dir / "steps" / name / "instruction.md", instruction)
        _write(task_dir / "steps" / name / "tests" / "test.sh", SESSION_TEST)
    last = dialogue["sessions"][-1]
    outputs = [{"file": f"solution_{i}.py", "query": query} for i, query in enumerate(last["history_eval_query"], 1)]
    queries = "\n".join(
        f"{i}. Write a {o['query']}. Save it to /workspace/{o['file']}." for i, o in enumerate(outputs, 1)
    )
    _write(task_dir / "steps" / "recall" / "instruction.md", RECALL_INSTRUCTION.format(queries=queries, **names))
    # Harbor uploads a step's tests only for its own verifier, so no earlier session can read the guidelines.
    tests = task_dir / "steps" / "recall" / "tests"
    _write(tests / "test.sh", RECALL_TEST)
    tests.joinpath("grade.py").write_bytes((SCRIPTS / "memorycode_grade.py").read_bytes())
    source = {"repository": "https://github.com/Cohere-Labs-Community/MemoryCode", "revision": REVISION}
    expected = {
        "source": {**source, "dialogue": dialogue_id},
        "outputs": outputs,
        "history_regex": last["history_regex"],
    }
    _write(tests / "expected.json", json.dumps(expected, indent=2) + "\n")
    steps = "".join(f'\n[[steps]]\nname = "{name}"\n' for name in [*step_names, "recall"])
    _write(task_dir / "task.toml", TASK_TOML.format(steps=steps))
    _write(task_dir / "environment" / "Dockerfile", DOCKERFILE)


def manifest(task_id: str, *, sessions: int, checksum: str) -> str:
    return (
        HEADER
        + f"""
schema: powercontext.e2e-task/v1
id: {task_id}
categories:
  - paired
  - memorycode
  - memorycode-sessions-{sessions}
dataset:
  path: {TASKS.as_posix()}
  task_id: {task_id}
  checksum: {checksum}
execution:
  type: bub
  model: true
  max_steps: 30
  max_tokens: 16384
evaluation:
  recall_step: recall
"""
    )


def _names(dialogue: dict) -> dict[str, str]:
    context = dialogue["context"]
    return {"mentee": context["mentee"], "mentor": context["mentor"], "company": context["company"]}


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
