# Prepared-context budget allocation

This suite explains where a prepared-context byte budget goes and compares the production allocation with an
evaluation-only alternative on fixed synthetic candidates. It uses the root Python environment, no network, and no
model. Production never runs the alternative allocator.

The production Builder can report one internal budget record for every candidate that budget fitting tries or that
an entry or section limit stops before it is tried: its exact origin, kind, rank, disposition, and byte sizes.
[RFC 0028](../../../docs/en/rfcs/0028_context_pack.md#internal-budget-records) defines the record for the default
JSON envelope and
[RFC 1489](../../../docs/en/rfcs/1489_prepared_context_text_assembly.md#budget-and-empty-results) adds the explicit
text-assembly specifics. Production does not attach a recorder; this suite does.

## Run

Run from the repository root:

```bash
uv run --locked python evaluation/memory/prepared_context_budget/run.py \
  --manifest evaluation/memory/prepared_context_budget/fixtures/budget-v1.json \
  --output .powercontext/task-2520/evaluation
uv run --locked pytest evaluation/memory/prepared_context_budget/tests -q
```

The run writes `report.json`, `report.md`, and `run-manifest.json` to `--output` and prints one line per case. It
exits 0 for a valid comparison, whether or not an improvement is established, and 1 when any validity check fails.
`report.json` and `report.md` contain no timestamps, so a replay is byte-identical; `run-manifest.json` adds the run
time, command, Git revision, source hashes, and platform. The pinned run is in [reports/budget-v1](reports/budget-v1/).

| File | Role |
| --- | --- |
| [fixtures/budget-v1.json](fixtures/budget-v1.json) | Cases, arms, scoring, decision rule, and limitations |
| [fixtures/budget-v1-definitions.md](fixtures/budget-v1-definitions.md) | Input materialization, deliverable forms, the `waterfill-v1` procedure, and validity checks |
| [manifest.py](manifest.py) | Builds the production inputs of each case |
| [arms.py](arms.py) | Runs the production baseline, binds frozen candidates, and implements `waterfill-v1` |
| [experiment.py](experiment.py) | Scores both arms and applies the decision rule |
| [report.py](report.py), [run.py](run.py) | Reports and the command line |

## Reference

### Parameters

| Parameter | Meaning |
| --- | --- |
| `--manifest PATH` | Required. A versioned manifest. Its definitions file, `<stem>-definitions.md`, must sit beside it; both are hashed into every report. A malformed manifest or a missing definitions file stops the run before any report is written. |
| `--output DIR` | Required. Created when missing; `report.json`, `report.md`, and `run-manifest.json` in it are replaced. |
| exit status | 0 when every validity check passes, whatever the decision; 1 when the decision is `invalid`. |

### Versions

| Field | Value |
| --- | --- |
| manifest `schema_version` | `powercontext.prepared-context-budget-experiment.v1` |
| manifest `dataset_version` | `powercontext.prepared-context-budget.synthetic.v1` |
| manifest `contract_version` | `2026-11-06` |
| report and run-manifest `schema_version` | `powercontext.prepared-context-budget-report.v1` |

### `report.json`

| Field | Content |
| --- | --- |
| `schema_version` | Report schema version |
| `manifest` | `schema_version`, `dataset_version`, `contract_version`, `sha256` of the manifest file, and `definitions_sha256` |
| `arms` | The `baseline` and `candidate` arm descriptions from the manifest |
| `decision` | `outcome` (`invalid`, `improvement_established`, or `improvement_not_established`), `primary_cases`, `mean_coverage_delta`, the thresholds `min_mean_coverage_delta` and `max_top_item_truncation_loss`, `top_item_loss_failures` (case ids), `critical_fact_regressions` (`case: family/artifact: fact`), `invalid_checks` (`case: check`), and `reasons` |
| `sensitivity` | One row per reordering case: `case_id`, `reorders` (the reference case), and the reference and reordered `coverage_delta`, `top_item_truncation_loss`, and number of `fact_regressions` |
| `limitations` | The manifest's limitation statements |
| `cases` | One object per case, below |

Each case has `case_id`, `role` (`primary` or `sensitivity`), `critical`, `sensitivity_of`, `path` (`default` or
`assembly`), `max_bytes`, `input_sha256` (digest of the materialized production inputs), `baseline_content_bytes` and
`candidate_content_bytes` (the complete rendered context), `baseline_coverage`, `candidate_coverage`,
`coverage_delta`, `top_item_truncation_loss`, `facts_declared`, `baseline_facts_retained`,
`candidate_facts_retained`, `fact_regressions` (`family/artifact: fact` lost by the candidate), `checks` (validity
check name to result), `baseline_dispositions` and `candidate_dispositions` (disposition to count), and `items`.
Dispositions are the production ones (`full`, `truncated`, `dropped_below_min_bytes`,
`dropped_no_fitting_truncation`, `entry_limit`, `family_limit`) plus `dropped_minimum_infeasible`, which only
`waterfill-v1` reports.

Each item, in frozen trace order, has `position`, `kind`, `family`, `artifact_id`, `rank` (the production record
rank), `weight`, `display_bytes` (the complete display body), and for each arm `<arm>_disposition`,
`<arm>_delivered_bytes` (the delivered display body including any ellipsis), and `<arm>_retained_fraction`.

`report.md` presents the same decision, cases, sensitivity rows, limitations, and per-case item tables.

### `run-manifest.json`

| Field | Content |
| --- | --- |
| `schema_version` | Report schema version |
| `generated_at` | UTC run time |
| `command` | The `run.py` arguments |
| `manifest_path`, `manifest_sha256` | The manifest as run |
| `definitions_path`, `definitions_sha256` | The definitions as run |
| `git_head`, `git_worktree_dirty` | Git revision and whether `src` or `evaluation/memory` had uncommitted changes |
| `source_sha256` | SHA-256 of both runtime modules and every suite module that produced the report |
| `python`, `platform` | Interpreter version and platform |

## Preregistration

The cases and rules in the manifest and its definitions were fixed before either arm ran. Every report pins the
SHA-256 of both files as run (manifest `f66d20fde6356dee53d63790dbb4f5936f8979e51f1f01ee10452cde80068078`,
definitions `1892d202e9d5d9bda732912991609ecf68224ee096b48bd7269fa746bd3aa5ec`) and one input digest per case.
Weights, thresholds, critical cases, and the case list belong to this manifest version; changing any of them
requires a new manifest version and a fresh run.

The 16 cases cover a large item before small ones, an all-fit control, infeasible minimums at 512 and 640 bytes
(16 candidates cannot each receive a 64-byte minimum once the envelope is counted), long citations, mixed families
with an upstream family cap, CJK, emoji, control characters and line endings, complete items shorter than 64 bytes,
the eight-entry limit, the 2000-byte item quota, four explicit sections, and two reordering cases.

## Arms

- **baseline** is the production `PreparedContextBuilder` with a budget recorder. The harness checks that recording
  leaves the build unchanged, that the trace describes that build, that budget dispositions reconcile with omission
  counters, Receipt counters, and fragments, and that re-rendering the delivered forms named by the trace with the
  production renderer reproduces the production content byte for byte.
- **waterfill-v1** reallocates the same frozen candidates. It admits candidates in frozen order at their minimal
  honest form (complete when short, otherwise the smallest truncation of at least 64 bytes) under the same caps,
  reports a candidate whose minimum does not fit as `dropped_minimum_infeasible`, raises one uniform byte level
  found by exact search on the actual rendering, and finally tops up candidates in frozen order. It has no tunable
  parameter and uses the production renderer and truncation rules.

Candidates removed before fitting, such as the third Experience or a duplicate, are not frozen candidates and are not
scored as budget failures.

## Scoring and decision

Position is the frozen trace order and has weight `1 / log2(position + 1)`. A complete item is worth 1.0; a truncated
item is worth 0.5 × its retained fraction, so a prefix never counts as a complete fact. Coverage is the weighted
value divided by the total weight, and each case reports `delta = candidate - baseline`. The top-item truncation loss
is how much retained fraction position 1 loses under the candidate arm. A synthetic fact counts as retained when its
literal appears in a delivered body.

The comparison is invalid when any validity check fails in any case. Otherwise an improvement is established only
when the mean primary delta is strictly greater than 0.05, no primary case loses more than 0.25 of its top item, and
no critical case loses a fact the baseline delivered. Reordering cases are reported but excluded from the decision.

## Results

Manifest `powercontext.prepared-context-budget.synthetic.v1`: **improvement not established**.

All validity checks pass in all 16 cases, both arms stay within `max_bytes`, and every case replays identically. The
mean primary delta is -0.0265. The top item loses more than 0.25 in 8 of 14 primary cases. Both critical
large-early cases lose the fact at the end of their top item.

| Case | Path | max_bytes | Baseline | waterfill-v1 | Delta | Top-item loss |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| `default-large-early-then-small` (critical) | default | 3000 | 0.4741 | 0.7747 | +0.3007 | 0.7811 |
| `default-all-fit-control` (critical) | default | 8000 | 1.0000 | 1.0000 | 0 | 0 |
| `default-infeasible-512` | default | 512 | 0 | 0 | 0 | 0 |
| `default-infeasible-640` | default | 640 | 0.0347 | 0.0347 | 0 | 0 |
| `default-long-citations` | default | 2000 | 0.4935 | 0.2278 | -0.2657 | 0.2909 |
| `default-mixed-families` | default | 2600 | 0.6257 | 0.2093 | -0.4165 | 0.5500 |
| `default-multibyte-and-controls` | default | 1600 | 0.4704 | 0.2611 | -0.2092 | 0.8744 |
| `default-short-complete-items` (critical) | default | 1500 | 0.1132 | 0.6451 | +0.5319 | 0.7392 |
| `default-entry-cap` | default | 8000 | 0.7763 | 0.7763 | 0 | 0 |
| `default-over-quota` | default | 8000 | 0.6869 | 0.6869 | 0 | 0 |
| `assembly-large-early-then-small` (critical) | assembly | 3000 | 0.5857 | 0.7802 | +0.1944 | 0.5995 |
| `assembly-controls-and-line-endings` | assembly | 1400 | 0.4944 | 0.2939 | -0.2005 | 0.7378 |
| `assembly-infeasible-640` | assembly | 640 | 0.0519 | 0.0519 | 0 | 0 |
| `assembly-four-sections` | assembly | 2400 | 0.4690 | 0.1630 | -0.3060 | 0.8457 |
| `sensitivity-default-large-last` | default | 3000 | 0.9289 | 0.9289 | 0 | 0 |
| `sensitivity-assembly-large-last` | assembly | 3000 | 0.8390 | 0.9217 | +0.0827 | 0 |

The pinned [report](reports/budget-v1/report.md) lists every candidate's disposition and delivered bytes in both arms.

## Interpretation

- Six primary cases deliver the same bodies in both arms. Either everything fits, or the budget, the entry limit,
  or the item quota leaves no room for a different allocation.
- `waterfill-v1` gains where production lets a large early item take most of the budget and then drops later
  candidates, including complete items shorter than 64 bytes. It gains only by truncating that top item, which loses
  60–78% of its retained fraction and, in both critical cases, its closing fact.
- It loses 0.20–0.42 where production already delivers several mid-sized items complete: the uniform level turns
  those complete items into prefixes worth at most half.
- It retains 67 instead of 41 of the 121 declared facts in primary cases, mostly because most synthetic facts sit at
  the start of a body. Fact retention is a secondary check and only critical-case regressions affect the decision.
- Moving the large item last removes or shrinks the gain (0 and +0.08), so the advantage depends on candidate order.

These results do not support replacing the production allocation. Production selection and budgets are unchanged.

## Follow-up

In these cases, keeping the top item intact and making room for later candidates conflict. In
`default-short-complete-items`, production truncates the 1200-byte top item to 991 bytes, which fills the whole
1500-byte budget, so no later candidate fits unless the top item gives up bytes. In
`default-large-early-then-small`, production delivers the top item and the second item complete and truncates the
third item's 150-byte body to 149 bytes. An allocator that keeps the top item in its production form and levels only
the remaining slack can therefore at best match production in both cases.

The mean delta also depends on the fixed modeling choices. Recomputing coverage from the pinned items with other
choices, which are not preregistered and do not affect the decision, gives -0.0034 with a prefix credit of
0.75 × retained fraction and +0.0198 with the full retained fraction. With equal position weights it gives +0.0790
with the preregistered prefix credit and +0.1231 with the full retained fraction. The top-item losses and critical fact
regressions do not depend on these choices.

A follow-up comparison should use a new manifest version with new held-out cases and preregister a scoring
sensitivity grid with a robustness criterion and an explicit bound on how many bytes the top item may give up to
admit later candidates. Whether reallocated bytes improve answers requires an answer-quality evaluation, which this
suite does not perform.

## Limitations

- The suite measures allocation coverage of fixed synthetic candidates under the production renderer. It does not
  measure answer quality, downstream model behavior, or real recall improvement.
- Weights and the half-value credit for prefixes are fixed modeling choices, not measured utility.
- Code-mode preparations (`include_code`) are outside the comparison; code evidence keeps its own reserved share, and
  their budget trace covers only the history share.
- On the explicit text path, a complete body and a truncation of the same size render one byte apart, so rendering
  size is not monotone at that boundary. This can only make `waterfill-v1` less than optimal; validity checks still
  measure the actual rendering.
