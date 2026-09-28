# LongMemEval-V2 full run configuration (not executed)

This document records how a **full-tier** LongMemEval-V2 run would be configured and executed.
No full run has been executed. Nothing in this document is a benchmark result, and the pinned
smoke subset described in [../README.md](../README.md) must never be presented as a full result.

## Status

| Item | State |
| --- | --- |
| Full-tier dataset lock | **To be created and reviewed.** `evaluation/locks/` currently contains only the small-tier lock `longmemeval-v2-small-v1.dataset-lock.json`. |
| Full-tier question manifest | **To be created and reviewed.** Only the ten-question smoke manifest `longmemeval-v2-small-v1.smoke.json` exists. |
| Cost guards (`--max-cases`, `--max-estimated-cost-usd`) | **Not implemented.** Must be implemented and reviewed before any paid Reader/Judge run (see [Estimates and spend limits](#estimates-and-spend-limits)). |
| Full run execution | **Not executed and not approved.** Model spend and data egress require separate, explicit user approval. |

## What a full run measures — and what it does not

A full run measures the same pipeline as the smoke subset, over the complete locked question set:
whether the PowerContext Memory adapter retrieves citable evidence through public interfaces under
fixed upstream data and a fixed context budget, and the answer accuracy, latency, context size,
failures, and abstention of the configured Reader and Judge.

It does **not** evaluate Handoff, cross-host recovery, normal Runtime persistence, or Work
Continuity; it does not measure general model capability or product leadership, and it does not
replace LoCoMo, SWE-bench Pro, or real user-task acceptance. Scores must never be improved by
rewriting gold prompts, reference answers, or the Memory schema. See
[../README.md](../README.md#what-the-longmemeval-v2-workload-evaluates) for the full boundary list.

## Prerequisites

1. **Upstream harness** — a detached checkout of
   <https://github.com/xiaowu0162/LongMemEval-V2> at commit
   `2cc8c540bdb87fe6761629b585e727e1c4704520`, plus a Python environment with that commit's
   pinned harness dependencies installed (for example
   `D:\powercontext-eval\venvs\longmemeval-v2-2cc8c540`). The runners refuse any other commit.
2. **Dataset** — the LongMemEval-V2 data root downloaded outside this repository (for example
   `D:\powercontext-eval\cache\longmemeval-v2-data`). The full run streams
   `trajectories.jsonl` (~1.2 GB for the small tier; the full tier is larger), so the disk must
   hold the data root plus the run artifacts. A full-tier dataset lock must pin the dataset
   revision and the SHA-256 of every input file before the run starts.
3. **PowerContext Server** — a local server bound to loopback (for example
   `http://127.0.0.1:18765`) with a bearer token configured. The token is resolved only from the
   `POWERCONTEXT_TOKEN` environment variable and is never written to artifacts.
4. **Model providers** — a DeepSeek API key for the `deepseek-openai` Reader and Judge (or an
   Anthropic-compatible endpoint for the Reader). No GPU is required; all model access is via
   provider APIs.
5. **Network** — access to the local PowerContext Server and to the configured model provider
   endpoints. No other egress is required.
6. **Python tooling** — `uv` and the `evaluation` project environment (`uv sync --project evaluation`).

## Configuration template

Copy [longmemeval-v2-run-config.example.env](../deploy/longmemeval-v2-run-config.example.env) to a protected
location outside this repository, fill in the values, and load it into the shell before running.
The file contains variable names and placeholders only; never commit a filled copy, and never pass
a token as a command-line argument.

| Setting | Example | Notes |
| --- | --- | --- |
| Data root | `D:\powercontext-eval\cache\longmemeval-v2-data` | Passed as `--data-root`. |
| Dataset lock | `evaluation\locks\<full-tier-lock>.json` | **Must be created first**; pins dataset revision and file digests. |
| Question manifest | `evaluation\locks\<full-tier-manifest>.json` | **Must be created first**; pins the full question set. |
| Harness root | `D:\powercontext-eval\cache\LongMemEval-V2` | Detached checkout at the pinned commit. |
| Harness Python | `D:\powercontext-eval\venvs\longmemeval-v2-2cc8c540\Scripts\python.exe` | Environment with harness dependencies. |
| PowerContext base URL | `http://127.0.0.1:18765` | Loopback only; no credentials in the URL. |
| Reader provider / model | `deepseek-openai` / `deepseek-flash` | Or `anthropic-compatible` with `ANTHROPIC_BASE_URL`. |
| Judge provider / model | `deepseek-openai` / `deepseek-flash` | Only `deepseek-openai` is supported for the Judge. |
| Reader / Judge timeouts | `120.0` seconds each | Raise for slower providers; recorded in the run manifest. |
| Output root | `D:\powercontext-eval\runs\longmemeval-v2\` | Every run creates one new timestamped directory. |

## Command template

Once the full-tier lock and manifest exist and are reviewed, a full run uses the same
`run-smoke` command with the full-tier inputs. The paths below are placeholders — **no full-tier
lock exists yet**, so this command must not be run as-is:

```bash
uv run --project evaluation powercontext-eval longmemeval-v2 run-smoke \
  --data-root D:\powercontext-eval\cache\longmemeval-v2-data \
  --dataset-lock evaluation\locks\<full-tier-dataset-lock>.json \
  --smoke-manifest evaluation\locks\<full-tier-question-manifest>.json \
  --harness-root D:\powercontext-eval\cache\LongMemEval-V2 \
  --harness-python D:\powercontext-eval\venvs\longmemeval-v2-2cc8c540\Scripts\python.exe \
  --processor-revision <PROCESSOR_GIT_SHA> \
  --powercontext-revision <POWERCONTEXT_GIT_SHA> \
  --integration-revision <INTEGRATION_GIT_SHA> \
  --powercontext-base-url http://127.0.0.1:18765 \
  --experiment-arm current-memory-fts-v1 \
  --reader-provider deepseek-openai \
  --reader-model deepseek-flash \
  --judge-provider deepseek-openai \
  --judge-model deepseek-flash \
  --output-dir D:\powercontext-eval\runs\longmemeval-v2\full-v1-<YYYYMMDDTHHMMSS>
```

The retrieval behaviour is selected only through `--experiment-arm` (currently
`current-memory-fts-v1`, `current-memory-hybrid-v1`, `query-time-compact-v1`, or
`write-time-l0-l1-v1`, or `task-lensed-selection-v1`; see
[../README.md](../README.md#experiment-arms)). Two full runs may be compared only when
`ensure_comparable_experiment_runs` confirms that every pinned condition except the arm matches.

Credentials come only from the environment (`POWERCONTEXT_TOKEN`, `DEEPSEEK_API_KEY`). Without
them the run fails as a `configuration` failure before any model work, with a non-zero exit code.

## Estimates and spend limits

A full run spends real money on the Reader and Judge. Before any paid phase:

1. **Estimate first, without models.** Run `run-smoke` with `--skip-reader` (or the per-stage
   `retrieval-smoke` + `prepare-smoke` commands). The prepare stage counts the bounded context
   tokens without calling a model; `03-prepare/prepare-summary.json` reports
   `question_count` and `memory_context_tokens`.
2. **Compute the estimate.** Reader input tokens ≈ the reported context tokens plus prompt
   overhead; Reader output tokens ≤ `--reader-max-tokens` × question count; Judge usage is
   reported per question by the provider. Apply the provider's current price table and record
   which price table revision was used.
3. **Get explicit approval.** The operator must confirm the estimated cost and the data egress
   scope (questions, context, and Reader answers are sent to the configured provider) before the
   full Reader/Judge run starts.
4. **Record cost under an explicit price policy.** Pass `--price-policy` (Reader) and
   `--judge-price-policy` (Judge) with the operator's current provider prices to have the run price
   its real Reader and Judge usage:

   ```powershell
   --price-policy '{\"provider\":\"deepseek-openai\",\"model\":\"<READER_MODEL>\",\"currency\":\"USD\",\"input_cache_hit_price_per_million\":<HIT_PRICE>,\"input_cache_miss_price_per_million\":<MISS_PRICE>,\"output_price_per_million\":<OUTPUT_PRICE>,\"price_policy_revision\":\"<PRICE_REVISION>\"}'
   --judge-price-policy '{\"provider\":\"deepseek-openai\",\"model\":\"<JUDGE_MODEL>\",\"currency\":\"USD\",\"input_cache_hit_price_per_million\":<HIT_PRICE>,\"input_cache_miss_price_per_million\":<MISS_PRICE>,\"output_price_per_million\":<OUTPUT_PRICE>,\"price_policy_revision\":\"<PRICE_REVISION>\"}'
   ```

   Prices are never hardcoded, so the operator supplies and versions them. Each policy must name
   both the `provider` and the `model` it prices, and `currency` must be `USD` because the recorded
   amount fields are named `*_usd`. DeepSeek bills cached input far below uncached input, so the
   policy carries separate cache-hit and cache-miss input prices and the run keeps DeepSeek's
   `prompt_cache_hit_tokens`/`prompt_cache_miss_tokens` split; usage that reports no split stays
   `null` rather than being blended. Each stage summary and manifest records the policy identity,
   and `report.json` reports `usage.reader_cost`, `usage.judge_cost`, and the summed
   `usage.estimated_cost_usd` only when every model stage that ran was priced under one policy
   revision.
5. **Enforce hard limits.** `--max-cases` and `--max-estimated-cost-usd` are **not implemented**
   in this scope, and no generic budget-approval system is provided. Any full run must therefore
   be gated by the operator outside the runner: review the step-1 token counts, the step-4 price
   policies, and the approval in step 3 before starting the paid Reader/Judge phases.

`report.json` keeps `estimated_cost_usd` at `null` unless every model stage that actually ran
recorded a usage cost under an explicit price policy. The report decides which stages ran from the
run manifest `modes` and the stage summaries, so a missing, unpriced, provider/model-mismatched,
currency-mismatched, or differently-revisioned stage cost keeps the total `null` with a reason
rather than summing only the stages that are present. Without a policy each stage records
`cost_usd: null` with an `unavailable_reason`, and ingestion — which calls no model — records zero
tokens, `cost_usd: 0.0`, and that reason.

## Output isolation

- All artifacts go to a new directory under `D:\powercontext-eval\runs\longmemeval-v2\` (or a
  directory the operator explicitly passes). Output directories are fail-closed: an existing
  directory aborts the run before any stage starts, and old runs are never overwritten.
- Runs never write to the PowerContext Runtime's normal persistence database; the retrieval
  stage creates isolated child Scopes for the evaluation, and query results are recorded in the
  run's `adapter-audit.jsonl`.
- Run directories are never committed to Git. In particular, `05-score/scoring-inputs.local.jsonl`
  contains reference answers and must stay outside Git, shared reports, and telemetry.

## Result labels

| Label | Meaning |
| --- | --- |
| `full` | A full-tier run over the complete locked question set with all six phases completed. Nothing else may be called full. |
| `smoke-subset` | The fixed ten-question subset. Every smoke artifact carries `classification: "smoke-subset"`. |
| `partial` | Phases were skipped (`--skip-reader` / `--skip-score`) or the run stopped after a failure; no accuracy may be published for a partial run. |
| `failed` | A phase failed; `failures.jsonl` records the phase and error class, and the run is never counted as a benchmark score. |

## Reproducibility

- `run-manifest.json` records the schema, classification, phase layout, input lock and manifest
  SHA-256 digests, harness commit, PowerContext version and URL (without credentials),
  Reader/Judge provider and model names (token variable names only, never values), revisions,
  and all model parameters.
- `run-summary.json` and `report.json` are derived only from saved stage artifacts; regenerating
  a report never calls a model, the provider, or the PowerContext Server.
- `replay-score` re-derives the deterministic scoring inputs and reuses only saved Judge labels
  for LLM-scored cases, so the recorded score can be audited without spending tokens.
- To reproduce a run: pin the same harness commit, dataset revision, model IDs, and parameters
  recorded in the manifest, then re-run into a **new** output directory.

## Failure recovery

- Never overwrite or edit a failed run's directory; it is retained evidence.
- To resume after a failed stage, run the remaining per-stage commands
  (`prepare-smoke`, `reader-smoke`, `score-smoke`, `replay-score`) against the completed stage
  directories of the interrupted run, writing to new output directories. `run-smoke` itself
  always starts from preflight and cannot resume an interrupted run.
- To start over, create a new `run-smoke` output directory; the fail-closed check prevents
  accidental reuse.
