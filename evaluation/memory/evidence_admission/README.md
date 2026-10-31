# Source evidence admission evaluation

This suite measures whether oversized or mixed persisted Source evidence needs bounded admission before Atomic Memory
extraction. It creates real, disposable SQLite Scopes and Sources, closes and reopens the database, invokes
`project_atomic_memory_evidence()`, and constructs the production `AtomicMemoryExtractionInput` shape.
It never publishes Atomic Memory, runs reconciliation, changes a production registry, or advances a production cursor.

[中文说明](README.zh.md) · [Experiment protocol](PROTOCOL.md)

The default experiment remains v1. [V2 protocol and paired comparison](V2_PROTOCOL.md) documents the explicitly selected
window-first candidate, additional stress dataset, shared feasibility reference, citation bundles and cumulative cost caps.
[V2 offline results](V2_RESULTS.zh.md) report all eight original combinations fixed, the exact v1 replay check, cost changes,
and the remaining variant failures. Input improvement does not establish generation quality or authorize production work.

[V3 protocol](V3_PROTOCOL.md) adds mechanical field-family allocation and exact-value representative scheduling.
Select `--experiment-version v3` with each of `synthetic-v1`, `validation-v2` and `validation-v3`; the default remains v1.
The original 28 cases produce 1260 five-arm samples; eight additional preimplementation stress cases add 360 samples.
Run `compare-preparation --run-dir PRIMARY --run-dir VALIDATION --run-dir ADDITIONAL --output-file COMPARISON.json`
to bind all three completed runs under the same code/freeze. Missing datasets, known losses or v1/v2 identity regressions
prevent an offline pass. The combined result still reports `insufficient_evidence`; the formal combined live gate has not passed.
See the [Chinese commands](README.zh.md) for a complete example and [V3 results](V3_RESULTS.zh.md) for retention, non-regression and costs. Exact duplicates are deferred, never granted support aliases.

## Install and validate

Run from the repository root:

```bash
uv sync --project evaluation --locked
uv run --project evaluation evidence-admission validate
uv run --project evaluation pytest evaluation/memory/evidence_admission/tests -m "not live" -q
uv run --project evaluation ruff check --no-fix evaluation/memory/evidence_admission
uv run --project evaluation ruff format --check evaluation/memory/evidence_admission
uv run --project evaluation ty check --project evaluation evaluation/memory/evidence_admission/src
```

The shared evaluation development environment includes the local `powercontext[builtin]` runtime needed for Source replay.
Installed wheels expose the optional `evidence-admission` dependency extra and bundle the frozen manifests and fixtures.
The root `make evaluation-unit-test` target also includes these tests. No default test requires credentials or an external service.
See [validation and the existing full-project prerequisite](VALIDATION.md) for the broader evaluation type-check limitation.

## Model-free preparation

```bash
uv run --project evaluation evidence-admission prepare \
  --output-dir .artifacts/evidence-admission/preparation
uv run --project evaluation evidence-admission report \
  --run-dir .artifacts/evidence-admission/preparation
```

The frozen suite has 20 fixtures, three arms, three budgets (2,048, 4,096 and 8,192 estimated input tokens), and three
independent replays: 540 samples. Each replay captures the same logical Source twice to check idempotent identity, then
re-reads it after reopening the database. The three cancellation cases exercise real local task cancellation. The generation
cancellation case uses a controlled waiting generator through the real generation pipeline; it does not call a provider.

`complete-projection-v1` preserves complete evidence. `window-reduction-v1` uses the real Source window trigger and durable
reduction repository, with a private evaluation consumption position. `bounded-fragments-v1` selects UTF-8-safe, independently
identified fragments without reading fact labels. Complete inputs that already fit remain byte-for-byte unchanged.
The optional structured tier arm is not applicable: this suite's Content adapter supplies no trusted ownership tiers.

Output contains the frozen run manifest, per-sample preparation records, generation records, JSON and Markdown reports,
artifact hashes and a `COMPLETE.json` marker. Existing output directories are refused. Readers reject incomplete or modified
runs. The `report` command regenerates and verifies the aggregate from per-sample records under the matching code/configuration.
It prints the regenerated JSON without mutating the saved run.

The saved result under `results/synthetic-v1-preparation/` is model-free. Its manifest records exact code and dataset hashes.
Provider-dependent unsupported-candidate and attribution metrics are null when unmeasured. Neither zero model requests nor
successful preparation establishes that a model will extract correct facts. An uncommitted freeze is disclosed and cannot
authorize a production follow-up.
The report separately summarizes exact-literal retention, generated required-fact recall, duplicate occupancy and reported
token usage. Null means unmeasured; token totals with incomplete usage do not establish total cost. The generation denominator
includes all required facts in scored samples, including omissions and intentionally impossible content.

## Combined live evaluation

[Live readiness results](LIVE_READY_RESULTS.zh.md) record the final offline replay, all-arm request plan and remaining prerequisites.

The [live contract](LIVE_PROTOCOL.md) and [Chinese runbook](LIVE_RUNBOOK.zh.md) document the frozen live-policy-v1 extension.
`plan-live` counts all five arms and separates the smoke request allowance. `run-smoke` uses an explicit narrower scope;
`compare-live` binds all three preparation runs, original live runs and audited derivatives under one provider/model/endpoint.
The gate checks actual supported output retention, exact literals, coverage, usage and audit identity. Input preparation alone
cannot satisfy it. Smoke and formal live commands retain the committed-freeze prerequisite; no command creates a commit.

## Opt-in live generation

Live runs require the fixture, policy and evaluator code freeze to be committed before inspecting model output. The command
refuses an uncommitted freeze. This check never creates a commit. Run from the source checkout so Git can verify the freeze.
All arms use the same instructions, settings, fixtures and budgets. Only explicitly selected private cases are sent externally.

```bash
export EVIDENCE_ADMISSION_API_KEY=YOUR_KEY
uv run --project evaluation evidence-admission run-live \
  --provider openai-compatible \
  --model MODEL_ID \
  --base-url https://PROVIDER_HOST/v1 \
  --token-env EVIDENCE_ADMISSION_API_KEY \
  --max-requests 1000 \
  --audit-dir /outside-the-repository/evidence-admission-private/run-a \
  --output-dir .artifacts/evidence-admission/live-a
```

`anthropic` is also supported; use its API root as the base URL. Endpoints must use HTTPS without embedded credentials,
queries or fragments. Credentials are read from the named environment variable, never from a manifest or a command argument.
SDK retries and HTTP redirects are disabled. The per-generation request cap is one; the manifest fixes a 60-second timeout,
2,048 output tokens, temperature zero and sequential execution. The explicit global request cap also bounds failed attempts.
No currency estimate is implied: use the chosen provider's prices before selecting the cap. A capped, interrupted or failed
execution must not be treated as full coverage.
Every ordinary sample in every arm must finish generation or produce an expected control budget rejection. Accepted partial
windows still require completed generation. Control failures and unresolved control reviews cannot be hidden by candidate
coverage or by the mere presence of sample records.

Press Ctrl-C to cancel a run. Cancellation propagates through the provider adapter. No completed aggregate is published for
an interrupted run. Local cancellation cannot prove that a remote provider stopped billing or execution.

Raw model output is written only into the explicit private audit directory outside the repository. Aggregate files contain
hashes, ranges, labels and counts, never raw Source text, prompts, model output, credentials or physical Scope identifiers.
Unknown semantic paraphrases remain unresolved until audited; an LLM judge cannot clear a critical gate.

The private directory contains `run-manifest.json`, append-only `events.jsonl` and `STATE.json`. Every successful returned
response and its reported usage are flushed and fsynced before the next call, including responses from unfinished
multi-window samples. Cancellation/request-cap exhaustion retains these records and publishes no completed aggregate.
After a successful run, `outputs.json` is assembled from the journal for audit. Files are mode 0600 and the directory is 0700.
Abrupt process termination can leave a running state or a partial last line. Journals do not support automatic resume and
are not a power-loss recovery guarantee. Inspect incomplete journals privately; the audit command requires a completed run.

## Human audit and replay

Reviewers inspect the private output against the frozen synthetic or private ground truth. A review file contains only hashes:

```json
{
  "manifest_hash": "SHA256_OF_RUN_MANIFEST",
  "reviews": {
    "SAMPLE_HASH": {
      "CANDIDATE_HASH": {
        "verdict": "supported",
        "claim_hashes": ["FROZEN_CLAIM_ID_HASH"]
      }
    }
  }
}
```

Other verdicts are `unsupported` and `false_attribution`. Candidate hashes include the candidate's kind, text and evidence IDs.
Supported reviews must still resolve to admitted claim ranges and preserve exact literals. Audit cannot invent support,
override a known forbidden claim, or erase a literal mismatch.

```bash
uv run --project evaluation evidence-admission audit \
  --run-dir .artifacts/evidence-admission/live-a \
  --private-outputs /outside-the-repository/evidence-admission-private/run-a/outputs.json \
  --reviews /outside-the-repository/evidence-admission-private/reviews.json \
  --output-dir .artifacts/evidence-admission/audited-a
```

This replays persisted Sources and the real pipeline with recorded output, verifies input/output hashes, preserves original
provider usage and timing, and publishes a separately identified audited report. It makes no new model requests.
Unknown sample/review identities, duplicate call positions and modified private output are rejected before publication.

## Private corpus

Use `manifests/private-manifest.example.json` as the external manifest format. Each entry names an environment variable
pointing to an external JSON `Case`, its exact file SHA-256 and a content-safe fixture label. The external Case uses the same
source recipes and claim schema as the synthetic suite. Store the Case and review files outside the repository.

```bash
export EVIDENCE_ADMISSION_PRIVATE_CASE_A=/outside-the-repository/private-case.json
uv run --project evaluation evidence-admission prepare \
  --private-manifest /outside-the-repository/private-manifest.json \
  --output-dir .artifacts/evidence-admission/private-preparation
```

Pass the same `--private-manifest` to live, audit and report commands. Private runs use the pinned decision policy, remain
separate from synthetic results, and cannot satisfy the committed synthetic-suite production gate.

## Citation protocol experiment

[Experiment v4](V4_PROTOCOL.md) keeps the v3 selection algorithm while versioning its model-facing encoding, uniform citation instructions, sample/report records and reviewed smoke gate. Select `--experiment-version v4` explicitly on preparation, comparison, planning, smoke, audit and live commands. New bundles expose only the outer evidence ID as a citation identity. An invalid returned citation remains a failed generation, with its known usage preserved. Legacy configurations keep their original encodings.

Original smoke and `audited_smoke` are separate artifacts; both remain excluded from formal comparisons. New formal runs require matching original/reviewed smoke directories and `ready_for_formal=true`. Full commands and review constraints are in the [Chinese runbook](V4_RUNBOOK.zh.md). Neither offline retention nor smoke passing establishes issue acceptance.

[Experiment v5](V5_PROTOCOL.md) separates frozen v3 selection-envelope costs from the model-facing citation encoding. It preserves original selected ranges for every arm, removes internal citation-like IDs only after selection, and uses a shorter explicit citation instruction. Actual prepared-input costs must not exceed conservative selection costs. Select `--experiment-version v5` for every command in the [runbook](V5_RUNBOOK.zh.md). Experiment v4 remains available with its disclosed v2-control regression; its artifacts are not replaced.

## Current acceptance status

Historical v5 live results and the two-request v9 first pair are available. The v9 candidate failed its atomicity gate;
its executor called the SDK generator directly, so the pair does not establish the issue's real Pipeline acceptance item.
The evaluation-only v10 adapter executes through `AtomicMemoryGenerationPipeline.extract()` in local tests, separates
unchanged historical scoring from parent/atom diagnostics, and preserves original control-window boundaries.
V11 adds protocol-aware Source-count reduction to the window control using the real durable repository. It reexecutes
324 window samples and inherits 1296 unchanged, hash-verified arm samples. All six historical protocol rejections recover,
with no observed input-coverage regression. Both versions have no live command. See the [acceptance matrix](ACCEPTANCE.zh.md),
[v11 protocol](V11_PROTOCOL.zh.md), and [offline results](V11_RESULTS.zh.md). The overall decision remains
`insufficient_evidence`; no production change is authorized.


[V12 Pipeline smoke protocol](V12_PROTOCOL.zh.md) provides an isolated, opt-in two-case SDK executor, with actual
HEAD-blob verification, per-plan budget authorization and explicit output review before advancing. Its offline preflight
uses controlled copy responses through the actual Pipeline; this does not establish live model quality or formal coverage.
Historical v9 failures and v10/v11 results remain unchanged. No default command accesses credentials or a provider.


The committed v12 first live request stopped with `finish_reason=length`: all 2048 output tokens were reasoning and
final content was empty. The Unicode case was not run. See [the failed live diagnostic](V12_RESULTS.zh.md);
`insufficient_evidence` remains the decision.

## Explicit thinking diagnostic

The [v13 execution contract](V13_PROTOCOL.zh.md) pins disabled thinking, a 2048-token output cap, exact HTTP request validation,
separate Pipeline entry/return states, and the carried conditional budget. Its CLI provides SDK mock preflight, recorded-response
failure replay, actual Git freeze checks, one-case live steps, bound reviews, and redacted reports. The literal case must pass
review before Unicode can run; any failure stops without retries. Even two passing diagnostic cases do not establish formal
all-arm coverage or authorize production changes.

The [v13 live result](V13_RESULTS.zh.md) returned a structured candidate but retained zero of four exact literals.
The frozen quality gate failed; Unicode and the formal matrix remain unrun. A recorded-response replay reproduces the
failure without additional provider calls. The overall decision remains `insufficient_evidence`.
