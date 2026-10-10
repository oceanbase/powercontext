# Work-continuity (Rollover Handoff) evaluation harness

This delivery adds a bounded, deterministic harness that compares **Rollover Handoff**
against **transcript compaction** and **informal summaries** on long tasks. It was written
against the benchmark request in issue #1791: define the tasks, baselines, and scoring
rubric; report injected bytes separately from task success; include failure analysis; and
use the findings to sharpen the Handoff quality requirements.

The harness is deliberately narrow. It runs no model, calls no network, and ships an
authored fixture task set plus a synthetic recorded-attempt fixture. Everything it produces
is labeled as harness validation, never as a benchmark result about a real host.

## What the harness measures

Six metrics are produced per method, per task. They are reported in two tables that are
never merged:

| Group | Metric | Definition |
| --- | --- | --- |
| Injected | `injected_bytes` | Exact UTF-8 size of the assembled continuation context. The host's own system prompt, tool schemas, repository instructions, and workspace files are outside this measurement. |
| Outcome | `task_success` | The recorded attempt performed the task's declared next action, relying on the facts that action depends on and not on superseded ones. |
| Outcome | `time_to_recover_state` | Step index at which recovery happened. An attempt that never recovers is recorded as a sentinel, not as zero. |
| Outcome | `incorrect_assumptions` | Steps that relied on a superseded fact. |
| Outcome | `missing_evidence` | Required facts the context never delivered. |
| Outcome | `unverifiable_claims` | Steps that relied on a fact whose evidence is declared unavailable. |
| Outcome | `user_correction_burden` | Recorded steps that were explicit user corrections. |

`AttemptScore.outcome_rank` orders outcomes using only the outcome metrics. Injected bytes
are deliberately absent from it: the ranking exists to find cases where the treatment
underperforms, not to reward a method for injecting less.

## Continuation arms

An arm is a frozen experiment identity. Every arm receives the same declared byte ceiling
(`DEFAULT_ASSEMBLY_MAX_BYTES = 16_000`), so the ceiling is part of the protocol rather than
of any method, and injected bytes measure the method instead of the allowance.

| Arm ID | Method | Baseline | What it carries |
| --- | --- | :---: | --- |
| `full-transcript-v1` | full-transcript | yes | The entire transcript, under the shared ceiling. |
| `compacted-transcript-v1` | compacted-transcript | yes | The first 2 and last 4 turns; the middle is dropped. |
| `informal-summary-v1` | informal-summary | yes | The last turn only. |
| `rollover-handoff-v1` | rollover-handoff | no (treatment) | State facts with evidence pointers, the next action, and the omissions. Superseded facts are excluded. |

`ensure_comparable_work_continuity_runs` rejects a comparison whose task set digest, task
selection, byte ceiling, PowerContext/integration revisions, or recorded execution
configuration differ, and rejects a pair of runs that share no continuation arm at all — two
runs pairing disjoint methods have no method in common for an observed difference to be
attributed to. The host *name* is deliberately not part of that check: a host is a
declared dimension of this evaluation, and the arm is exactly the intended difference. What is
part of it is the model and host revision that produced the recordings, **and how the
recordings were allocated between them**: a run that put model A on one host and model B on
nine is not comparable to one that reversed that split, because the mix alone moves the success
counts while both runs list the same two models. A manifest whose execution configuration
cannot show its allocation — a missing block, a missing attempt count, a non-positive count, or
a task entry that is a bare count rather than a per-method one — is refused rather than assumed
equal.

The gate is reachable from the command line, not only from the library:
`work-continuity compare --baseline <run> --treatment <run>` applies exactly
these checks to two published run directories and exits non-zero with the disagreements it
found.

## Task set and ground truth

`evaluation/coding/work_continuity/locks/work-continuity-v1.tasks.json` pins six tasks (four coding, two
documentation; 73 turns total) under the schema `powercontext.work-continuity-task-lock.v1`.
Each task declares:

- the long session it continues, as numbered turns every evidence pointer resolves against;
- the state facts a fresh session must hold to continue safely, each with `turn:N` evidence;
- the expected next action, plus the facts that action depends on;
- the facts that were true earlier and were later superseded;
- the known omissions, and the facts whose evidence cannot be produced here.

The loader is fail-closed: a task that cannot be scored is rejected rather than scored. It
also rejects a task *set* that cannot exercise the comparison it claims to — the set must
cover both coding and documentation work and must declare at least one superseded fact, one
unavailable-evidence case, and one known omission.

Ground truth here is declared, not harvested. That is what makes the harness runnable
without a model, and it is also why no number in the report is a claim about real sessions.

## Recorded attempts

The harness never runs a model. A host integration records what a fresh session actually did
with the continuation context it received, and `load_attempts` validates that recording
against the task lock before anything is scored. Each recorded step names the declared fact
ids it relied on, so scoring stays exact instead of depending on text matching against
free-form model output.

A recording is only evidence about a method if it was produced under the protocol this run
assembles. The artifact therefore declares that protocol and each attempt is bound to the
context it actually received:

| Field | Level | Meaning |
| --- | --- | --- |
| `protocol.task_set_id` | artifact | Must equal the task lock's task set id. |
| `protocol.task_lock_sha256` | artifact | Must equal the task lock's own content digest. |
| `protocol.assembly_max_bytes` | artifact | Must equal the ceiling this run assembles with. |
| `context_sha256` | attempt | SHA-256 of the delivered context text, checked against the assembled context before scoring. |
| `host_revision` | attempt | The host revision that produced the recording. A host may report exactly one. |
| `model` | attempt | The model that produced the recording. A host may report exactly one. |
| `performed_action_id` | step | The action this step performed, when it performed one. |

Those bindings are what stop a run from rescoring old recordings against a context nobody
delivered: scoring the same attempts under `--max-bytes 1` is refused, because the digest of
the one-byte context does not match the digest the recording was taken under. A host that
reports two model or host-revision configurations is rejected before scoring.

`work-continuity validate` applies the same bindings, using the ceiling the
recording itself declares, so the documented preflight refuses what `run` refuses: an artifact
that pins another task set, another task lock, or a context digest this project cannot assemble
fails validation instead of passing a check that only looked at the file's shape.

Recovery also requires the step to have *done* the work. A step counts as recovered only when
it performed the task's declared next action — naming the facts an action depends on is
reading, not continuing, and continuing from a superseded plan is not continuing the declared
one. It also has to have continued from *this* context: a step that performs the expected
action while claiming a fact the context never delivered contradicts its own context, so it is
scored as an evidence conflict rather than as a recovery. Relying on a fact the task declares
unavailable is different in kind — the fact's absence is disclosed, not contradicted — so it is
counted as an unverifiable claim and ranks below a clean recovery instead of vetoing success.

`evaluation/coding/work_continuity/locks/work-continuity-v1.attempts-fixture.json` is a synthetic fixture
(6 tasks × 4 methods × 2 hosts = 48 attempts) that exercises every failure branch. It is
labeled synthetic in the artifact itself, and it carries the protocol block and per-attempt
digests described above.

## Failure analysis

Every finding is classified and mapped back to a specific contract requirement, so failure
analysis closes the loop with the RFC 1783 quality requirements instead of producing an
unactionable list.

| Failure class | Requirement argued | Recommendation |
| --- | --- | --- |
| `budget_truncation` | `QR-state` | Raise the ceiling or make the method's own ordering explicit. |
| `context_absent` | `QR-state` | Deliver the state facts the next action depends on. |
| `stale_state` | `QR-state` | Exclude superseded facts, or label them as superseded. |
| `missing_evidence` | `QR-evidence` | State evidence for every delivered fact. |
| `unverifiable_claim` | `QR-omissions` | Record the omission instead of asserting the fact. |
| `vague_next_action` | `QR-next-action` | Make the next action depend on named facts. |
| `no_recording` | none | Provide a recording; this asks for evidence, not a contract change. |

`budget_truncation` is claimed whenever the byte ceiling removed material a required fact
depends on — a dropped `state:*` item *or* a dropped `turn:N` item that carries the fact's
evidence — and whenever the ceiling removed the delivered next action itself. Without the
second half, a transcript method could never receive this classification at all: its state
facts are not separate items, so a ceiling that dropped every turn holding a required fact's
evidence used to be scored as `context_absent`, blaming the method for material the budget
removed. Without the third, a ceiling that stopped before the next action left every fact the
action needed in place, so the fallback reported a vague action and recommended changing the
Handoff contract for a loss the `--max-bytes` flag caused, while the quality table for the same
context already reported `QR-next-action` as violated.

`QR-*` requirements are the Handoff quality requirements; `IV-*` requirements are the
"invalid or should require correction" content rules from RFC 1783. Both are enforced
deterministically by `check_rollover_quality`, against the fields the ceiling actually
delivered. The complete draft is reported next to that number rather than in place of it, so
a ceiling that emptied a context cannot certify the requirements it never carried. A method
that carries no Handoff draft is reported as *not applicable* rather than as `0 of 0
satisfied`, so "nothing was checked" cannot be read as "checked and found wanting".

Comparisons are scoped per host *and* per execution configuration. A baseline recorded on one
host can never "beat" the treatment recorded on another, because that comparison would
describe two different integrations rather than two continuation methods; and two runs whose
hosts report different models, host revisions, or configuration allocations are refused
outright. The allocation is compared per task *and* per method, not only in total: a run that
sent `wc-coding-0001` to model A and `wc-coding-0002` to model B is not comparable with one that
swapped them, because a task is scored against its own declared next action, so that swap alone
moves the success counts. The same holds for the arm, which is the unit the benchmark actually
scores: a run that ran `full-transcript-v1` on model A and `rollover-handoff-v1` on model B is
not comparable with one that swapped *those* assignments, because the per-arm successes flip
with the swap while the per-task totals hide it. Host *names* stay outside the comparison, so
renaming a host does not make two runs incomparable.

Recording coverage and comparison coverage are separate states, and the unit of comparison is
one task on one host. A run can record every selected task and method and still hold no
comparison at all: the treatment may not be selected, may have no recording, or may never have
been recorded on the same task *and* host as a baseline. The report
names which of those absences it found instead of printing that the treatment did not rank
below a baseline on a comparison that never happened.

## Running it

```bash
# Validate the pinned inputs without writing anything.
uv run --project evaluation work-continuity validate \
  --task-lock evaluation/coding/work_continuity/locks/work-continuity-v1.tasks.json \
  --attempts evaluation/coding/work_continuity/locks/work-continuity-v1.attempts-fixture.json

# Assemble every method, score the recorded attempts, and write one run directory.
uv run --project evaluation work-continuity run \
  --task-lock evaluation/coding/work_continuity/locks/work-continuity-v1.tasks.json \
  --attempts evaluation/coding/work_continuity/locks/work-continuity-v1.attempts-fixture.json \
  --output-dir /tmp/work-continuity-run \
  --run-id fixture-smoke
```

`run` writes `run-manifest.json`, `assembly.jsonl`, `scores.jsonl`, `run-summary.json`,
`report.json`, and `report.md`. Passing `--attempts` is optional: without it the run reports
assembly only, and every `injected_bytes` number is still produced. An assembly-only run
reports recording coverage and comparison as *unavailable* rather than as clean — it never
prints that every task has a recorded attempt, and it never claims the treatment did not rank
below a baseline, because neither was measured. `run-summary.json` also carries a `comparison`
block (`treatment_selected`, `treatment_recorded`, `baselines_recorded`, `compared_pair_count`,
`compared_pairs`), which is what lets a reader tell a run that held a comparison from one that
only held recordings. Each compared pair is one `(task, host)` unit where the treatment and at
least one baseline were both recorded, so a host that holds the treatment for one task and a
baseline for another contributes nothing rather than counting as a comparison.

`--arm` and `--task-id` narrow a run for focused work. A narrowed run still refuses an
attempt artifact that names a task or arm it did not select, so a partial run cannot be
mistaken for a complete one. Narrowing to a baseline arm alone records no treatment, and the
report says so rather than implying a comparison took place.

## What the fixture run shows

With the shipped synthetic fixture the harness reports, per method:

| Method | Injected bytes | Recovered |
| --- | ---: | ---: |
| `full-transcript-v1` | 8,784 | 7 |
| `compacted-transcript-v1` | 5,007 | 0 |
| `informal-summary-v1` | 1,711 | 0 |
| `rollover-handoff-v1` | 6,697 | 11 |

Findings by class: `context_absent=24`, `stale_state=5`, `unverifiable_claim=1`,
`vague_next_action=1`, all 48 attempts recorded. The treatment underperformed on exactly two
cases — `wc-doc-0003` on host `synthetic-fixture-a` (beaten by `full-transcript-v1`, class
`unverifiable_claim`, requirement `QR-omissions`) and `wc-coding-0006` on host
`synthetic-fixture-b` (beaten by `full-transcript-v1`, class `vague_next_action`, requirement
`QR-next-action`).

**The fixture numbers are not a benchmark result.** They are the output of an authored task
set and a synthetic recording; they establish that the harness measures what it says it
measures, and nothing about production traffic.

## Boundaries

- The checked-in task set is authored fixture material, and its ground truth is declared
  rather than harvested.
- The harness runs no model; a recorded attempt is the only input that describes what a host
  did, and the recorder is responsible for mapping steps onto declared fact ids.
- Injected bytes count the assembled continuation context only.
- Task success is scored against one declared next action per task, so a different but
  equally correct continuation is not credited.
- Handoff quality is checked against the fields the byte ceiling actually delivered, with the
  complete draft reported next to them, so a truncated context cannot pass on material it
  never carried.
- Host names in a run do not imply a verified host integration.

This is a small, runnable validation rather than a complete evaluation. Extending it toward a
real benchmark means recording attempts from actual hosts and models, and expanding the task
set beyond the authored six.
