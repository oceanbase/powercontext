# Validation

The synthetic preparation matrix uses actual local SQLite persistence and Source projection. It makes no provider requests.
Results and exact code/configuration hashes are in [the saved report](results/synthetic-v1-preparation/REPORT.md).

Validation commands:

```bash
uv run --project evaluation pytest evaluation/memory/evidence_admission/tests -m "not live" -q
uv run --project evaluation pytest -c evaluation/pyproject.toml \
  evaluation/memory/longmemeval_v2/tests evaluation/memory/evidence_admission/tests -m "not live" -q
uv run --project evaluation ruff check evaluation/memory/evidence_admission
uv run --project evaluation ruff format --check evaluation/memory/evidence_admission
uv run --project evaluation ty check --project evaluation evaluation/memory/evidence_admission/src
uv run prek run -a
uv run ty check
```

The evaluation suite has 132 passing tests. The combined LongMemEval-V2 and evidence admission run has 379 passing tests and one
skip: the existing native lineage acceptance requires the `ScopedAtomicMemory.evidence` prerequisite from #1975.
The 24 related production window-recovery, SQLite wire-evidence and extraction-evidence tests pass separately.
SQLite tests in the evaluation environment emit an existing SQLAlchemy 2.1 `Result.tuples()` deprecation warning.

The evaluator package and root project type checks pass. The broader command
`uv run --project evaluation ty check --project evaluation` reports one existing capability mismatch at
`memory/longmemeval_v2/src/powercontext_eval_longmemeval_v2/lineage_run.py`: the checked-out `ScopedAtomicMemory` has no
`evidence` method. This suite does not add that production API or change the unrelated lineage evaluator to hide the gap.

Wheel validation checks that the command and frozen fixture/manifest data are packaged and can be read outside the source
tree. Default tests and provider-adapter construction use no external model. No claim about live provider quality, remote
cancellation, actual host payloads, OceanBase or spawned production workers follows from these checks.

Decision regressions construct controlled gate inputs to exercise all three decisions, all-arm generation coverage,
unresolved control/candidate audits, candidate loss, provenance risk, input non-determinism and an uncommitted freeze.
The CLI integration substitutes a local generator and an in-memory committed flag solely to test run → private output →
human audit → regenerated report. These tests do not establish a real committed freeze, provider execution or model quality.
The generated test follow-up is temporary test output, not a recommendation from the synthetic preparation results.

Interruption tests stop inside a multi-window sample after its first returned response, both with caller cancellation and
request-cap exhaustion. The private journal retains that response and its known usage; no completed aggregate is consumable.
Private output modifications, duplicate call positions, unknown samples and unknown review identities are rejected.
Literal-scoring regressions distinguish a retained literal inside an omitted claim from an identical unrelated occurrence.

See [验收状态与限制](ACCEPTANCE.zh.md) for the distinction between implementation checks and issue completion.

V2 tests protect all eight original fixture/budget regressions, whole-record UTF-8/CRLF round trips, call and cumulative-token
exhaustion, continuing after an oversized Source, exact bundle support, omitted-support rejection, cancellation, versioned
manifest integrity and v1/v2 controlled CLI audit round trips. The comparison regression explicitly rejects a larger retained
count that replaces an old claim/literal identity. See [V2_PROTOCOL.md](V2_PROTOCOL.md) for commands and measurement limits.

The complete v2 preparation runs contain 720 original-matrix samples and 288 variant samples. Their recorded manifests bind the evaluator used for those runs;
they are historical artifacts and cannot be regenerated under a changed evaluator identity. All eight original failing combinations are fixed without observed
claim/literal/status regression; the three v1 arms also replay all 540 historical samples with identical inputs, support,
omissions and existing preparation scores. The variant dataset still has four loss combinations (12 repeated samples),
which remain visible in the [combined results](V2_RESULTS.zh.md). These are zero-provider-request runs, not live results.


V3 adds regression checks for the four known v2 losses at unchanged budgets and call counts, duplicate occurrences without
support aliases, label-independent input selection, missing-support rejection, cost-component accounting, resource caps and
all three cancellation phases. Controlled CLI generation/audit/report tests include v3, whose individual dataset report
remains insufficient evidence. The combined offline command rejects missing/duplicate datasets, known stress losses and
unexpected unavailability even if all sample records exist. Frozen v3 fixture/catalog integrity and all three wheel datasets
are checked separately. Exact-content scheduling performs no fuzzy deduplication or semantic classification.

The current root and evaluator-module type checks pass. The full evaluation check retains the one documented existing
lineage capability diagnostic. Both lock checks and a real wheel build pass; the installed wheel loads all three v3 datasets
outside the checkout. All provider quality and actual usage remain unmeasured.


The live-policy-v1 extension has controlled end-to-end preparation, generation, private-output, audit and combined-report tests.
They cover missing datasets, model/endpoint mismatch, output fact/literal loss despite complete input, missing control output,
incomplete usage, uncommitted freezes, changed audit usage/support, per-dataset request caps and immutable policy integrity.
The separate smoke keeps the full experiment unchanged, produces ten scoped samples/eight local generator calls, persists its
private journal, and regenerates its report. Actual uncommitted CLI preflights stop before provider initialization. Controlled
committed flags and generators are confined to tests and establish no real provider or committed-freeze evidence.
See [LIVE_RUNBOOK.zh.md](LIVE_RUNBOOK.zh.md) for the exact formal execution and manual review steps.
