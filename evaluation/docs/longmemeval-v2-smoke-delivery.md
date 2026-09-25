# LongMemEval-V2 smoke workload delivery

This delivery adds a bounded, reproducible LongMemEval-V2 smoke workload for PowerContext.
It uses the public Source and Memory HTTP interfaces, preserves the pinned upstream prompt and
scoring behavior, and labels every result as a smoke subset rather than a complete benchmark.

## Delivered workflow

The `powercontext-eval longmemeval-v2` command group now supports the complete local workflow:

1. validate the pinned dataset, fixed ten-question manifest, and upstream harness checkout;
2. ingest trajectory evidence through public PowerContext Source and Memory APIs;
3. retrieve cited text/image context from isolated evaluation Scopes;
4. prepare bounded Reader messages with the pinned upstream harness implementation;
5. optionally call an explicitly configured Reader and Judge;
6. score with the pinned upstream evaluation functions;
7. replay saved deterministic and Judge decisions without model or network calls; and
8. produce one machine-readable report and one human-readable report.

`run-smoke` orchestrates these stages into a single fail-closed output directory. An existing
output directory is never overwritten. Infrastructure, retrieval, generation, Judge,
configuration, and integrity failures remain distinct from incorrect answers.

## Experiment arms

Retrieval behaviour is selected through a registered experiment arm, not a free-form search
mode. An arm is a frozen experiment identity — same questions, same context budget, same
projections — and only the knobs the arm declares may differ, so two runs of different arms can
be compared fairly. `arms.py` registers exactly the five implemented arms:

| Arm ID | Strategy | Difference |
| --- | --- | --- |
| `current-memory-fts-v1` | Memory search (`fts`) | Default; identical to the previous `--search-mode fts` behaviour. |
| `current-memory-hybrid-v1` | Memory search (`hybrid`) | Changes only the public Memory search mode. |
| `query-time-compact-v1` | PreparedContext (8,000 bytes) | Keeps ingestion fixed and compacts context at query time through `/v1/context/prepare`. |
| `write-time-l0-l1-v1` | Memory search (`fts`) | Writes bounded deterministic L0 and L1 Memory entries without adding persistent schema fields. |
| `task-lensed-selection-v1` | Memory search (`fts`) | Uses a deterministic question-keyword query projection without label, answer, or Judge access. |

Unregistered IDs (currently the unsupported temporal-filter arm) are rejected before any stage
runs instead of silently falling back to FTS. `--search-mode` was removed from
`retrieval-smoke` and `run-smoke`; the arm is the single source of the search mode.

Guarantees implemented and unit-tested:

- The runner reads `/v1/capabilities` before ingestion and fails as an
  `infrastructure` capability error when the Server lacks the arm's search mode or
  `powercontext.prepared-context.v1` support.
- The adapter sends the arm's `mode` to the public `/v1/memory/search` endpoint, reads the
  executed mode from the response, and records `requested_mode`/`actual_mode` in
  `adapter-audit.jsonl`, in each `retrieval-results.jsonl` row, and through `post_query_hook`.
- The query-time compact arm calls the public PreparedContext endpoint with an 8,000-byte
  budget, validates the response byte count, and records `retrieval_strategy: prepared-context`.
- Every explicitly requested search mode must match the executed mode the Server reports;
  only `auto` accepts the Server's own choice. A mismatch is recorded as an `integrity`
  failure, never as a wrong answer or a silent fallback to another mode.
- Every run manifest, retrieval manifest and summary, and unified report records the full arm
  block, and `report.md` shows the arm ID.
- `ensure_comparable_experiment_runs` refuses to compare two runs whose dataset lock digest,
  question manifest digest, harness commit, processor model or revision, context budget, search
  limit, Reader/Judge configuration, or PowerContext/integration revisions differ beyond the
  arm, and requires both arm records and every comparison field to be present.

## Reproducibility and privacy

- The harness commit, dataset revision and file digests, smoke question order, processor
  revision, model settings, PowerContext revision, and integration revision are recorded.
- The approximately 1.2 GB trajectory input remains streaming; it is not loaded as one string or
  byte array.
- The adapter, retrieval, preparation, and Reader stages do not read question type, reference
  answers, or Judge data. Only local scoring reads the locked reference answers.
- API credentials are resolved from named environment variables. Secret values are not written
  to manifests, reports, failure evidence, examples, or Git.
- `scoring-inputs.local.jsonl` contains local replay evidence and must remain outside Git,
  shared reports, and telemetry.
- Evaluation artifacts remain outside normal Runtime persistence and outside this repository.

## Validation evidence

A real one-command, model-free run completed the preflight, retrieval, and prompt-preparation
stages against a local PowerContext Server:

| Measurement | Result |
| --- | --- |
| Classification | `smoke-subset` |
| Status | `partial` (Reader, Judge, and replay intentionally skipped) |
| Questions | 10 |
| Failed stages | 0 |
| Retrieved context items | 100 |
| Prepared context tokens | 239,765 |
| Ingestion latency | 121,838.524 ms |
| Published accuracy | unavailable, as required for a model-free run |

The generated `report.json` and `report.md` contained no credential values. The local server was
stopped after validation, and the run artifacts were not added to Git.

A separate paid smoke execution of the saved ten Reader inputs produced 4 correct answers out of
10 with the configured DeepSeek Reader/Judge path. This is a smoke-subset observation only. It is
not a full LongMemEval-V2 result and does not establish product or model leadership. The saved
score was also reproduced by the offline replay path with zero Reader and Judge calls.

The experiment-arm framework was verified on the same local setup. The Server's
`/v1/capabilities` reported `search_modes: ["auto", "fts"]` (no embedding model is configured on
this machine), so hybrid execution could not be exercised locally; per the agreed scope, the
machine-level verification is FTS success plus hybrid capability rejection:

| Verification | Result |
| --- | --- |
| Model-free FTS arm run (`--experiment-arm current-memory-fts-v1 --skip-reader`) | `preflight`, `retrieval`, `prepare` completed; 10/10 questions succeeded; all ten queries show `requested: "fts"`, `actual: "fts"` in the audit and per-question results. |
| Hybrid arm run (`--experiment-arm current-memory-hybrid-v1 --skip-reader`) | Failed before ingestion: `RetrievalCapabilityError`, error class `infrastructure`, phase `retrieval`; no retrieval artifacts and no ingestion; the manifest and report still record the hybrid arm. |
| Query-time compact run (`--experiment-arm query-time-compact-v1 --skip-reader`) | `preflight`, `retrieval`, and `prepare` completed; 10/10 questions succeeded through public PreparedContext; 10 context items were bounded to 80,000 bytes / 24,623 tokens in total, with no model calls. |

Arm verification run directories (kept outside the repository under the external runs root):
`smoke-v1-arm-fts-model-free-20260920T191032` and
`smoke-v1-arm-hybrid-capability-check-20260920T191032`, plus
`smoke-v1-query-time-compact-model-free-20260920T2345`.

The scoped verification for this delivery completed with:

- 182 LongMemEval-V2 unit tests passing;
- Ruff lint and format checks passing;
- targeted type checks for the arm registry, adapter, retrieval runner, orchestrator, report,
  CLI, and their tests passing;
- `git diff --check` passing; and
- secret-pattern scans of changed files and the real model-free runs returning no matches.

## Explicitly not executed

No full-tier dataset run was executed. The repository does not yet contain an approved full-tier
dataset lock or question manifest, and hard case/cost guards are not implemented. A paid full run
therefore requires separate implementation review and explicit approval for cost and data egress.
See [longmemeval-v2-full-run.md](longmemeval-v2-full-run.md) for the recorded prerequisites.

Hybrid retrieval was not executed against a hybrid-capable Server: this machine's Server
advertises only `auto` and `fts` because no embedding model is configured. A temporal-filter
arm is intentionally not registered because the pinned public dataset has ordered states but no
timestamps, and the public Memory search contract exposes no time-range filter. It must fail
loudly rather than treating haystack order as fabricated time provenance.

This workload does not validate Handoff, cross-host recovery, normal Runtime persistence, or
Work Continuity. It does not replace LoCoMo, SWE-bench Pro, or PowerContext-native acceptance
tests.
