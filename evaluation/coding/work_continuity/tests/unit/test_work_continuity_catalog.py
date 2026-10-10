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

import json
from pathlib import Path

import pytest

from powercontext_eval_work_continuity.arms import get_continuation_arm
from powercontext_eval_work_continuity.assembly import assemble_context
from powercontext_eval_work_continuity.attempts import AttemptInputError, load_attempts
from powercontext_eval_work_continuity.catalog import (
    TaskCatalog,
    WorkContinuityEnvironmentError,
    WorkContinuityInputError,
)

from .work_continuity_fixtures import (
    ATTEMPTS_SCHEMA,
    attempts_protocol,
    coding_task,
    documentation_task,
    shipped_locks_dir,
    standard_lock,
    valid_attempt,
    write_attempts,
    write_lock,
)

# The shipped lock is the benchmark's ground truth, so its digest is pinned here as
# a golden anchor: an edit to the authored tasks has to be an intentional edit to
# this constant too, and every number the documentation quotes moves with it.
SHIPPED_TASK_LOCK_SHA256 = "76f26cb6a0292e6cbce8996e315ce6d6e5b8bfc7815e697a62c42e4453b38cf1"


def test_catalog_loads_both_tasks_and_fixes_their_evidence(tmp_path: Path) -> None:
    catalog = TaskCatalog.load(standard_lock(tmp_path))

    assert catalog.task_ids == ("t-coding", "t-doc")
    assert catalog.task_set_id == "test-set"
    assert len(catalog.content_sha256) == 64
    coding = catalog.require("t-coding")
    assert coding.turn_numbers == (1, 2, 3, 4, 5)
    assert coding.obsolete_source_turns == (2,)
    assert coding.required_fact_ids == ("f1", "f2")
    doc = catalog.require("t-doc")
    assert doc.unavailable_fact_ids == ("g3",)
    assert doc.known_omissions == ("The docs build was never run.",)


def test_catalog_rejects_a_lock_that_is_not_the_pinned_schema(tmp_path: Path) -> None:
    path = write_lock(tmp_path, [coding_task(), documentation_task()], schema="other")
    with pytest.raises(WorkContinuityInputError, match="schema is unsupported"):
        TaskCatalog.load(path)


def test_catalog_rejects_a_domain_error_field_in_the_lock(tmp_path: Path) -> None:
    task = coding_task()
    task["unexpected"] = True
    with pytest.raises(WorkContinuityInputError, match="must declare exactly"):
        TaskCatalog.load(write_lock(tmp_path, [task, documentation_task()]))


def test_catalog_rejects_a_transcript_whose_turns_are_not_numbered_in_order(tmp_path: Path) -> None:
    task = coding_task()
    task["transcript"] = [
        {"turn": 1, "role": "user", "text": "a"},
        {"turn": 3, "role": "assistant", "text": "b"},
        {"turn": 4, "role": "assistant", "text": "c"},
        {"turn": 5, "role": "assistant", "text": "d"},
    ]
    with pytest.raises(WorkContinuityInputError, match="numbered 1..n in order"):
        TaskCatalog.load(write_lock(tmp_path, [task, documentation_task()]))


def test_catalog_rejects_evidence_pointing_at_a_turn_that_does_not_exist(tmp_path: Path) -> None:
    task = coding_task()
    task["required_state_facts"] = [
        {"fact_id": "f1", "text": "x", "evidence": "turn:99"},
        {"fact_id": "f2", "text": "y", "evidence": "turn:5"},
    ]
    with pytest.raises(WorkContinuityInputError, match="turn:N pointer"):
        TaskCatalog.load(write_lock(tmp_path, [task, documentation_task()]))


def test_catalog_rejects_a_next_action_that_depends_on_an_undeclared_fact(tmp_path: Path) -> None:
    task = coding_task()
    task["expected_next_action"] = {"action_id": "a1", "text": "do it", "required_fact_ids": ["f1", "f9"]}
    with pytest.raises(WorkContinuityInputError, match="unknown state fact f9"):
        TaskCatalog.load(write_lock(tmp_path, [task, documentation_task()]))


def test_catalog_rejects_an_obsolete_fact_that_reuses_a_required_fact_id(tmp_path: Path) -> None:
    task = coding_task()
    task["obsolete_facts"] = [{"fact_id": "f1", "text": "stale", "evidence": "turn:2", "superseded_by": "f1"}]
    with pytest.raises(WorkContinuityInputError, match="reuses a required state fact id"):
        TaskCatalog.load(write_lock(tmp_path, [task, documentation_task()]))


def test_catalog_rejects_an_evidence_free_fact_that_is_not_declared_unavailable(tmp_path: Path) -> None:
    task = documentation_task(evidence=None)
    task["unavailable_evidence"] = []
    with pytest.raises(WorkContinuityInputError, match="must match its evidence-free facts exactly"):
        TaskCatalog.load(write_lock(tmp_path, [task, coding_task()]))


def test_catalog_rejects_an_unavailable_entry_for_a_turn_backed_fact(tmp_path: Path) -> None:
    task = documentation_task(evidence="turn:3")
    with pytest.raises(WorkContinuityInputError, match="declared_but_turn_backed=g3"):
        TaskCatalog.load(write_lock(tmp_path, [task, coding_task()]))


def test_catalog_rejects_a_task_set_without_both_task_kinds(tmp_path: Path) -> None:
    second = coding_task()
    second["task_id"] = "t-coding-2"
    with pytest.raises(WorkContinuityInputError, match="must cover both coding and documentation"):
        TaskCatalog.load(write_lock(tmp_path, [coding_task(), second]))


def test_catalog_rejects_a_task_set_without_a_superseded_fact(tmp_path: Path) -> None:
    coding = coding_task()
    coding["obsolete_facts"] = []
    with pytest.raises(WorkContinuityInputError, match="at least one superseded state fact"):
        TaskCatalog.load(write_lock(tmp_path, [coding, documentation_task()]))


def test_catalog_rejects_a_task_set_without_an_unavailable_evidence_case(tmp_path: Path) -> None:
    doc = documentation_task()
    doc["required_state_facts"] = [
        {"fact_id": "g1", "text": "commit compares the expected head", "evidence": "turn:2"},
        {"fact_id": "g2", "text": "the change belongs in docs/guide.md", "evidence": "turn:4"},
    ]
    doc["unavailable_evidence"] = []
    with pytest.raises(WorkContinuityInputError, match="at least one unavailable-evidence case"):
        TaskCatalog.load(write_lock(tmp_path, [coding_task(), doc]))


def test_catalog_require_and_select_report_declared_identifiers(tmp_path: Path) -> None:
    catalog = TaskCatalog.load(standard_lock(tmp_path))

    assert [task.task_id for task in catalog.select(None)] == ["t-coding", "t-doc"]
    assert [task.task_id for task in catalog.select(["t-doc"])] == ["t-doc"]
    with pytest.raises(WorkContinuityInputError, match="unknown work-continuity task"):
        catalog.require("missing")
    with pytest.raises(WorkContinuityInputError, match="Duplicate work-continuity task selection"):
        catalog.select(["t-doc", "t-doc"])


def test_catalog_reports_an_unreadable_lock_as_an_environment_error(tmp_path: Path) -> None:
    with pytest.raises(WorkContinuityEnvironmentError, match="Cannot read"):
        TaskCatalog.load(tmp_path / "absent.json")


def test_catalog_rejects_a_blank_lock(tmp_path: Path) -> None:
    path = tmp_path / "blank.json"
    path.write_text("   \n", encoding="utf-8")
    with pytest.raises(WorkContinuityEnvironmentError, match="is blank"):
        TaskCatalog.load(path)


def test_attempts_load_and_group_by_host(tmp_path: Path) -> None:
    lock = standard_lock(tmp_path)
    catalog = TaskCatalog.load(lock)
    path = write_attempts(
        tmp_path,
        [
            valid_attempt(lock),
            valid_attempt(lock, host="second-host"),
            valid_attempt(lock, arm_id="full-transcript-v1"),
        ],
        lock=lock,
    )

    attempts = load_attempts(path, catalog=catalog)

    assert attempts.hosts == ("fixture-host", "second-host")
    assert attempts.configurations == (("fixture-host@1", "fixture-model"),)  # retained, not only validated
    assert len(attempts.for_task_and_arm("t-coding", "full-transcript-v1")) == 1
    assert attempts.for_task_and_arm("t-coding", "informal-summary-v1") == ()


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"task_id": "nope"}, "unknown work-continuity task"),
        ({"arm_id": "not-an-arm"}, "unknown continuation arm"),
        ({"host": "   "}, "host must be a non-empty string"),
        ({"host_revision": ""}, "host_revision must be a non-empty string"),
        ({"model": " "}, "model must be a non-empty string"),
        ({"context_sha256": "not-a-digest"}, "context_sha256 must be a lowercase hex sha256 digest"),
        ({"steps": []}, "must record at least one step"),
    ],
)
def test_attempts_reject_entries_that_cannot_be_scored(
    tmp_path: Path, overrides: dict[str, object], message: str
) -> None:
    lock = standard_lock(tmp_path)
    catalog = TaskCatalog.load(lock)
    entry = valid_attempt(lock, **overrides)
    with pytest.raises(AttemptInputError, match=message):
        load_attempts(write_attempts(tmp_path, [entry], lock=lock), catalog=catalog)


def test_attempts_reject_an_undeclared_fact_reference(tmp_path: Path) -> None:
    lock = standard_lock(tmp_path)
    catalog = TaskCatalog.load(lock)
    entry = valid_attempt(lock, steps=[{"step": 1, "action_text": "x", "relied_on": ["f1", "f9"]}])
    with pytest.raises(AttemptInputError, match="relies on undeclared fact 'f9'"):
        load_attempts(write_attempts(tmp_path, [entry], lock=lock), catalog=catalog)


def test_attempts_reject_a_performed_action_the_task_does_not_declare(tmp_path: Path) -> None:
    lock = standard_lock(tmp_path)
    catalog = TaskCatalog.load(lock)
    entry = valid_attempt(
        lock,
        steps=[{"step": 1, "action_text": "x", "relied_on": ["f1"], "performed_action_id": "a9"}],
    )
    with pytest.raises(AttemptInputError, match="the only action this task declares is 'a1'"):
        load_attempts(write_attempts(tmp_path, [entry], lock=lock), catalog=catalog)


def test_attempts_accept_a_step_that_declares_the_expected_action(tmp_path: Path) -> None:
    lock = standard_lock(tmp_path)
    catalog = TaskCatalog.load(lock)
    entry = valid_attempt(
        lock,
        steps=[{"step": 1, "action_text": "x", "relied_on": ["f1", "f2"], "performed_action_id": "a1"}],
    )

    attempts = load_attempts(write_attempts(tmp_path, [entry], lock=lock), catalog=catalog)

    assert attempts.attempts[0].steps[0].performed_action_id == "a1"


def test_attempts_reject_a_host_that_reports_two_execution_configurations(tmp_path: Path) -> None:
    """A host whose arms ran on different models cannot support a comparison."""

    lock = standard_lock(tmp_path)
    catalog = TaskCatalog.load(lock)
    entries = [
        valid_attempt(lock, model="model-one"),
        valid_attempt(lock, arm_id="full-transcript-v1", model="model-two"),
    ]

    with pytest.raises(AttemptInputError, match="reports more than one execution configuration"):
        load_attempts(write_attempts(tmp_path, entries, lock=lock), catalog=catalog)


def test_attempts_reject_a_host_that_reports_two_host_revisions(tmp_path: Path) -> None:
    """The revision is half the execution configuration, so it is compared too.

    Varying only the model leaves a host whose runtime changed undetected, which
    would let a runtime difference be read as a method difference.
    """

    lock = standard_lock(tmp_path)
    catalog = TaskCatalog.load(lock)
    entries = [
        valid_attempt(lock, host_revision="runtime-one"),
        valid_attempt(lock, arm_id="full-transcript-v1", host_revision="runtime-two"),
    ]

    with pytest.raises(AttemptInputError, match="reports more than one execution configuration") as error:
        load_attempts(write_attempts(tmp_path, entries, lock=lock), catalog=catalog)

    assert "runtime-one" in str(error.value)
    assert "runtime-two" in str(error.value)


def test_attempts_reject_an_out_of_order_step_number(tmp_path: Path) -> None:
    lock = standard_lock(tmp_path)
    catalog = TaskCatalog.load(lock)
    entry = valid_attempt(
        lock,
        steps=[
            {"step": 1, "action_text": "x", "relied_on": []},
            {"step": 3, "action_text": "y", "relied_on": []},
        ],
    )
    with pytest.raises(AttemptInputError, match="numbered 1..n in order"):
        load_attempts(write_attempts(tmp_path, [entry], lock=lock), catalog=catalog)


def test_attempts_reject_a_duplicate_task_arm_host_triple(tmp_path: Path) -> None:
    lock = standard_lock(tmp_path)
    catalog = TaskCatalog.load(lock)
    entries = [valid_attempt(lock), valid_attempt(lock)]
    with pytest.raises(AttemptInputError, match="repeat task t-coding arm rollover-handoff-v1"):
        load_attempts(write_attempts(tmp_path, entries, lock=lock), catalog=catalog)


def test_attempts_reject_an_unknown_schema_and_retain_the_declared_identity(tmp_path: Path) -> None:
    lock = standard_lock(tmp_path)
    catalog = TaskCatalog.load(lock)
    path = tmp_path / "attempts.json"
    path.write_text(
        json.dumps(
            {
                "schema": "powercontext.work-continuity-attempts.v9",
                "protocol": attempts_protocol(lock),
                "attempts": [valid_attempt(lock)],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(AttemptInputError, match="schema is unsupported"):
        load_attempts(path, catalog=catalog)

    accepted = load_attempts(
        write_attempts(tmp_path, [valid_attempt(lock, model="m", host_revision="r")], lock=lock),
        catalog=catalog,
    )
    assert accepted.attempts[0].model == "m"
    assert accepted.attempts[0].host_revision == "r"
    assert accepted.attempts[0].configuration == ("r", "m")
    assert accepted.configurations == (("r", "m"),)


def test_attempts_reject_an_artifact_without_a_protocol(tmp_path: Path) -> None:
    """A recording that does not pin a protocol cannot be bound to any context."""

    lock = standard_lock(tmp_path)
    catalog = TaskCatalog.load(lock)
    path = tmp_path / "attempts.json"
    path.write_text(
        json.dumps({"schema": ATTEMPTS_SCHEMA, "attempts": [valid_attempt(lock)]}),
        encoding="utf-8",
    )

    with pytest.raises(AttemptInputError, match="must contain only schema, protocol, and attempts"):
        load_attempts(path, catalog=catalog)


@pytest.mark.parametrize(
    ("protocol", "message"),
    [
        ({"task_set_id": "", "task_lock_sha256": "a" * 64, "assembly_max_bytes": 1}, "task_set_id"),
        ({"task_set_id": "s", "task_lock_sha256": "zz", "assembly_max_bytes": 1}, "task_lock_sha256"),
        ({"task_set_id": "s", "task_lock_sha256": "a" * 64, "assembly_max_bytes": 0}, "assembly_max_bytes"),
    ],
)
def test_attempts_reject_a_malformed_protocol(tmp_path: Path, protocol: dict[str, object], message: str) -> None:
    lock = standard_lock(tmp_path)
    catalog = TaskCatalog.load(lock)
    path = tmp_path / "attempts.json"
    path.write_text(
        json.dumps({"schema": ATTEMPTS_SCHEMA, "protocol": protocol, "attempts": [valid_attempt(lock)]}),
        encoding="utf-8",
    )

    with pytest.raises(AttemptInputError, match=message):
        load_attempts(path, catalog=catalog)


def test_attempts_reject_a_step_without_a_non_empty_action_text(tmp_path: Path) -> None:
    lock = standard_lock(tmp_path)
    catalog = TaskCatalog.load(lock)
    entry = valid_attempt(lock, steps=[{"step": 1, "action_text": "  ", "relied_on": []}])
    with pytest.raises(AttemptInputError, match="action_text must be a non-empty string"):
        load_attempts(write_attempts(tmp_path, [entry], lock=lock), catalog=catalog)


def test_shipped_fixture_lock_and_attempts_validate() -> None:
    """The checked-in fixture must stay loadable and internally consistent."""

    locks = shipped_locks_dir()
    lock = locks / "work-continuity-v1.tasks.json"
    catalog = TaskCatalog.load(lock)
    attempts = load_attempts(locks / "work-continuity-v1.attempts-fixture.json", catalog=catalog)

    assert len(catalog.tasks) >= 6
    assert any(task.unavailable_evidence for task in catalog.tasks.values())
    assert any(task.obsolete_facts for task in catalog.tasks.values())
    assert len(attempts.attempts) == len(catalog.tasks) * 4 * len(attempts.hosts)
    assert attempts.protocol.task_lock_sha256 == catalog.content_sha256
    assert attempts.protocol.task_set_id == catalog.task_set_id


def test_the_shipped_task_lock_is_pinned_by_its_digest() -> None:
    """The authored ground truth is anchored, not only read.

    Every published number in `evaluation/coding/work_continuity/docs/work-continuity-benchmark.md` is a
    property of this file, so an edit to it must be a deliberate edit to the
    documented numbers as well.
    """

    catalog = TaskCatalog.load(shipped_locks_dir() / "work-continuity-v1.tasks.json")

    assert catalog.content_sha256 == SHIPPED_TASK_LOCK_SHA256


def test_shipped_fixture_binds_every_recording_to_the_context_it_names() -> None:
    """The fixture is only reusable with the protocol and contexts it was made for."""

    locks = shipped_locks_dir()
    lock = locks / "work-continuity-v1.tasks.json"
    catalog = TaskCatalog.load(lock)
    attempts = load_attempts(locks / "work-continuity-v1.attempts-fixture.json", catalog=catalog)

    for attempt in attempts.attempts:
        context = assemble_context(
            catalog.require(attempt.task_id),
            get_continuation_arm(attempt.arm_id),
            max_bytes=attempts.protocol.assembly_max_bytes,
        )
        assert attempt.context_sha256 == context.content_sha256
