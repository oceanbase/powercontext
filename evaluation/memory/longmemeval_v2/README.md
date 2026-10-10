# LongMemEval-V2 memory evaluation

Source, data locks, configuration, documentation, and tests for the LongMemEval-V2 suite live here.
The suite is installed through the shared `evaluation/` environment and provides the independent `longmemeval-v2` command.

## LongMemEval-V2 smoke workload

The LongMemEval-V2 commands run a fixed ten-question smoke subset end to end: preflight
validation of the pinned inputs, real PowerContext HTTP retrieval, prompt preparation, an
optional Reader, upstream scoring, offline score replay, and a one-command orchestration
(`run-smoke`) that writes one run directory with a unified report. A full-tier run has not been
executed; its recorded configuration lives in
[docs/longmemeval-v2-full-run.md](docs/longmemeval-v2-full-run.md).

Prepare a detached upstream checkout at the pinned harness commit and download
the matching LongMemEval-V2 data root outside this repository. The checked-in
small-tier lock and smoke manifest fix the data revision, three core data-file
hashes, ten source-ordered questions, all five published abilities, and both
published domains. The dataset lock has this shape:

```json
{
  "schema": "powercontext.longmemeval-v2-dataset-lock.v1",
  "upstream": {
    "repository": "https://github.com/xiaowu0162/LongMemEval-V2",
    "harness_commit": "2cc8c540bdb87fe6761629b585e727e1c4704520"
  },
  "dataset_revision": "DATASET_REVISION",
  "tier": "small",
  "files": {
    "questions.jsonl": "SHA256",
    "trajectories.jsonl": "SHA256",
    "haystacks/lme_v2_small.json": "SHA256"
  }
}
```

The smoke manifest fixes question IDs, their upstream source order, and coverage
of the published ability categories. Run the preflight against a new output
directory:

```bash
uv run --project evaluation longmemeval-v2 smoke \
  --harness-root /path/to/LongMemEval-V2 \
  --data-root /path/to/longmemeval-v2-data \
  --dataset-lock evaluation/memory/longmemeval_v2/locks/longmemeval-v2-small-v1.dataset-lock.json \
  --smoke-manifest evaluation/memory/longmemeval_v2/locks/longmemeval-v2-small-v1.smoke.json \
  --output-dir /path/to/new-smoke-artifacts
```

The command refuses a harness checkout at a different commit, mismatched input
hashes, invalid or incomplete smoke coverage, and an existing output directory.
It writes `manifest.json` and `subset.json`, both labelled as a smoke subset.

### PowerContext Memory adapter bootstrap

Run the bootstrap with a Python environment containing the pinned upstream
harness dependencies. It validates the clean harness commit, registers
`memory_type: powercontext` in memory, and then forwards all remaining arguments
to the unchanged upstream harness. From a source checkout, the script makes
the suite package available to that interpreter:

```bash
python evaluation/memory/longmemeval_v2/scripts/run_longmemeval_v2_harness.py \
  --harness-root /path/to/LongMemEval-V2 \
  -- <upstream harness arguments>
```

If the evaluation wheel is installed in the harness environment, use its module entry point:

```bash
python -m powercontext_eval_longmemeval_v2.harness_bootstrap \
  --harness-root /path/to/LongMemEval-V2 \
  -- <upstream harness arguments>
```

The adapter memory configuration requires a dedicated evaluation Scope and
audit artifact path. Credentials are resolved only from `token_env` at runtime:

```json
{
  "memory_type": "powercontext",
  "memory_params": {
    "scope_id": "longmemeval-v2-smoke-run-id",
    "audit_path": "/path/to/run/context/powercontext-memory.jsonl",
    "base_url": "http://127.0.0.1:8765",
    "token_env": "POWERCONTEXT_TOKEN",
    "search_mode": "auto",
    "search_limit": 10
  }
}
```

Each trajectory chunk is captured through the public Content Source endpoint
and one deterministic bounded projection per trajectory is explicitly
remembered through the public Memory endpoint. The projection keeps goal,
outcome, URL, action, thought, and bounded observation evidence without calling
a model. The adapter audit correlates the returned Source references and Memory
citation; it does not claim that this correlation is native Memory lineage.
Queries use the public Memory search endpoint and return upstream-compatible
text context items. Query images are neither read nor sent because the current
Memory search contract is text only.

With a ready PowerContext Server, run the fixed ten-question retrieval-only
smoke workload without a Reader, Judge, model credential, or scoring step:

```bash
uv run --project evaluation longmemeval-v2 retrieval-smoke \
  --harness-root /path/to/LongMemEval-V2 \
  --data-root /path/to/longmemeval-v2-data \
  --dataset-lock evaluation/memory/longmemeval_v2/locks/longmemeval-v2-small-v1.dataset-lock.json \
  --smoke-manifest evaluation/memory/longmemeval_v2/locks/longmemeval-v2-small-v1.smoke.json \
  --base-url http://127.0.0.1:8000 \
  --run-id retrieval-smoke-001 \
  --powercontext-revision POWERCONTEXT_GIT_SHA \
  --integration-revision INTEGRATION_GIT_SHA \
  --output-dir /path/to/new-retrieval-smoke-artifacts
```

The runner verifies the locked input digests while streaming
`trajectories.jsonl`, retains only one trajectory object at a time, and creates
one isolated child Scope for each distinct ordered haystack. Identical
haystacks reuse their ingestion, while different haystacks cannot retrieve each
other's Memory. It writes `retrieval-manifest.json`,
`retrieval-results.jsonl`, `adapter-audit.jsonl`, `failures.jsonl`, and
`summary.json` beside the preflight `manifest.json` and `subset.json`. Accuracy,
Reader, and Judge fields remain null because retrieval-only output is not a
benchmark score.

### Experiment arms

Retrieval behaviour is selected through a registered experiment arm, not a free-form search mode:

```bash
uv run --project evaluation longmemeval-v2 retrieval-smoke \
  ... \
  --experiment-arm current-memory-hybrid-v1
```

An arm is a frozen configuration identity: same questions and the same downstream token
budget, with only the declared retrieval/projection knobs allowed to differ. Five arms are
currently registered:

| Arm ID | Strategy | Notes |
| --- | --- | --- |
| `current-memory-fts-v1` | Memory search (`fts`) | Default; identical to the previous `--search-mode fts` behaviour. |
| `current-memory-hybrid-v1` | Memory search (`hybrid`) | Requires a Server whose `/v1/capabilities` advertises `hybrid`. |
| `query-time-compact-v1` | PreparedContext (8,000 bytes) | Uses public `/v1/context/prepare`; does not alter ingestion or persist a new index schema. |
| `write-time-l0-l1-v1` | Memory search (`fts`) | Writes deterministic bounded L0 index and L1 summary entries through public `remember`; no schema fields are added. |
| `task-lensed-selection-v1` | Memory search (`fts`) | Projects the question into a deterministic keyword lens; never reads question type, gold answer, or Judge data. |

Unregistered arm IDs (currently the unsupported temporal-filter arm) are rejected
before any work runs instead of silently falling back to FTS. Before ingesting anything, the
runner checks `/v1/capabilities`: a Server without the arm's search mode or PreparedContext
schema fails as a capability error. Every explicitly requested search mode must match the
executed mode the Server reports (only `auto` accepts the Server's own choice) — a mismatch is
recorded as an integrity failure rather than a benchmark result. Every manifest, summary, and
unified report records the full arm block, `adapter-audit.jsonl` records the requested and
actual strategy/mode per query, and `ensure_comparable_experiment_runs` refuses to compare two
runs whose dataset lock, question manifest, harness commit, processor, context budget, search
limit, Reader/Judge configuration, or revisions differ beyond the arm.

Prepare bounded, replayable Reader inputs without calling a Reader. This command
uses the pinned upstream harness to count and truncate Memory context, and
requires an immutable Hugging Face processor revision rather than resolving the
processor from a moving branch:

```bash
uv run --project evaluation longmemeval-v2 prepare-smoke \
  --retrieval-dir /path/to/retrieval-smoke-artifacts \
  --harness-root /path/to/LongMemEval-V2 \
  --harness-python /path/to/longmemeval-harness-python \
  --processor-revision PROCESSOR_GIT_SHA \
  --memory-context-max-tokens 200000 \
  --output-dir /path/to/new-prepared-prompt-artifacts
```

It writes `prepare-manifest.json`, `prepared-prompts.jsonl`,
`prepare-failures.jsonl`, and `prepare-summary.json`. Each prepared prompt
records the original and bounded Context token counts, the bounded Context
bytes, final system/user messages, a prompt SHA-256, and prepare latency. This
is still a no-model stage: Reader, Judge, and accuracy remain absent.

Run an Anthropic-compatible Reader or DeepSeek's direct OpenAI-compatible API
over prepared prompts. Credentials are read only from the named process
environment variable and are never written to the run artifacts:

```bash
export DEEPSEEK_API_KEY=YOUR_KEY
uv run --project evaluation longmemeval-v2 reader-smoke \
  --prepared-dir /path/to/prepared-prompt-artifacts \
  --provider deepseek-openai \
  --model deepseek-flash \
  --token-env DEEPSEEK_API_KEY \
  --max-tokens 512 \
  --temperature 0 \
  --output-dir /path/to/new-reader-artifacts
```

Reader and Judge endpoints require HTTPS by default. For a trusted HTTP model
gateway, explicitly pass `--allow-insecure-http` to `reader-smoke`,
`--judge-allow-insecure-http` to `score-smoke`, or the separate
`--reader-allow-insecure-http` and `--judge-allow-insecure-http` flags to
`run-smoke`. HTTP sends prompts and the bearer token without transport encryption.
The opt-in is recorded separately for each model role in the run manifests.
URLs containing credentials, query strings, or fragments are rejected in both
modes, and model requests never follow redirects.

The OpenAI-compatible `--base-url` includes any gateway prefix such as `/v1`;
the Reader appends `/chat/completions`. For `anthropic-compatible`, supply the
gateway root because the Reader appends `/v1/messages`. Credentials still come
from the environment variable selected by `--token-env` or `--judge-token-env`.

The Reader writes `reader-manifest.json`, `reader-outputs.jsonl`,
`reader-failures.jsonl`, and `reader-summary.json`. It records provider usage
returned by the API but does not score answers. Keep OpenTelemetry variables
out of this command when prompt telemetry is not approved.

Score Reader outputs with the pinned deterministic metric functions. The
abstention and gotchas metric types require an LLM Judge and send the question,
reference answer, full Reader response, and parsed answer to the configured
Judge provider:

```bash
export DEEPSEEK_API_KEY=YOUR_KEY
uv run --project evaluation longmemeval-v2 score-smoke \
  --reader-dir /path/to/reader-artifacts \
  --data-root /path/to/longmemeval-v2-data \
  --dataset-lock evaluation/memory/longmemeval_v2/locks/longmemeval-v2-small-v1.dataset-lock.json \
  --smoke-manifest evaluation/memory/longmemeval_v2/locks/longmemeval-v2-small-v1.smoke.json \
  --harness-root /path/to/LongMemEval-V2 \
  --judge-model deepseek-flash \
  --judge-token-env DEEPSEEK_API_KEY \
  --judge-max-tokens 256 \
  --judge-temperature 0 \
  --output-dir /path/to/new-score-artifacts
```

The score run writes `per-question.jsonl`, `judge-outputs.jsonl`,
`score-failures.jsonl`, and `score-summary.json`. It also writes
`scoring-inputs.local.jsonl`, which contains reference answers for local replay
and must remain outside Git, shared reports, and telemetry.

Replay the saved deterministic and Judge decisions without a Reader, Judge
provider, token, or network request:

```bash
uv run --project evaluation longmemeval-v2 replay-score \
  --score-dir /path/to/score-artifacts \
  --harness-root /path/to/LongMemEval-V2 \
  --output-dir /path/to/new-score-replay-artifacts
```

The replay records source digests and writes `replay-manifest.json`,
`replay-per-question.jsonl`, `replay-failures.jsonl`, and
`replay-summary.json`. It reuses only saved Judge labels for LLM-scored cases.

### One-command smoke run

`run-smoke` chains every stage above into one fail-closed run directory. With a
ready PowerContext Server and no model credentials, run the model-free mode
first. The retrieval arm defaults to `current-memory-fts-v1` and can be selected
explicitly with `--experiment-arm`:

```bash
uv run --project evaluation longmemeval-v2 run-smoke \
  --data-root /path/to/longmemeval-v2-data \
  --dataset-lock evaluation/memory/longmemeval_v2/locks/longmemeval-v2-small-v1.dataset-lock.json \
  --smoke-manifest evaluation/memory/longmemeval_v2/locks/longmemeval-v2-small-v1.smoke.json \
  --harness-root /path/to/LongMemEval-V2 \
  --harness-python /path/to/longmemeval-harness-python \
  --processor-revision PROCESSOR_GIT_SHA \
  --powercontext-revision POWERCONTEXT_GIT_SHA \
  --integration-revision INTEGRATION_GIT_SHA \
  --powercontext-base-url http://127.0.0.1:18765 \
  --experiment-arm current-memory-fts-v1 \
  --skip-reader \
  --output-dir /path/to/new-run-artifacts
```

The full mode drops `--skip-reader`, requires `DEEPSEEK_API_KEY` in the environment, and also
runs the Reader, Judge scoring, and score replay. `--skip-score` keeps the Reader but skips
Judge scoring and replay. Every mode writes the same layout:

```text
<output-dir>/
  run-manifest.json    # exclusive-create; inputs, revisions, providers (no secret values)
  run-summary.json     # completed/skipped/failed phases; accuracy only when scored
  failures.jsonl       # one row per failed phase, with an error class
  report.json          # unified machine-readable summary
  report.md            # human summary with the fixed boundary banner
  01-inputs/  02-retrieval/  03-prepare/  04-reader/  05-score/  06-replay/
```

The command exits non-zero unless the run completed: `--skip-reader` and `--skip-score` runs end
as `partial`, and a failed stage ends as `failed` while keeping the earlier stage artifacts.
Recorded failure summaries are redacted against the configured token values, including
`POWERCONTEXT_TOKEN` in model-free mode. Reference answers are read only by the score stage,
which writes the local replay artifact, and by the replay stage reading that artifact; the
adapter, retrieval, prepare, and reader stages never read them.

### Cost reporting with an explicit price policy

Token usage is always recorded from the provider's own response. Model cost is reported only when
an explicit price policy is passed; prices are never hardcoded, and an unconfigured cost stays
`null` with a reason instead of `0`:

```bash
uv run --project evaluation longmemeval-v2 run-smoke \
  ... \
  --price-policy '{"provider":"deepseek-openai","model":"deepseek-flash","currency":"USD","input_cache_hit_price_per_million":0.006,"input_cache_miss_price_per_million":0.3,"output_price_per_million":1.2,"price_policy_revision":"deepseek-public-list-2026-09"}' \
  --judge-price-policy '{"provider":"deepseek-openai","model":"deepseek-flash","currency":"USD","input_cache_hit_price_per_million":0.006,"input_cache_miss_price_per_million":0.3,"output_price_per_million":1.2,"price_policy_revision":"deepseek-public-list-2026-09"}' \
  --output-dir /path/to/new-run-artifacts
```

`run-smoke` takes a separate policy per model role because the Reader and the Judge may run
different models; `reader-smoke` takes `--price-policy` and `score-smoke` takes
`--judge-price-policy`. A policy must contain exactly `provider`, `model`, `currency`,
`input_cache_hit_price_per_million`, `input_cache_miss_price_per_million`,
`output_price_per_million`, and `price_policy_revision`. Every field is required, prices must be
finite and non-negative, and `currency` must be `USD` because the recorded amount fields are named
`*_usd`. A policy is applied only when both its `provider` and its `model` match the configured
stage; otherwise the stage records `null` plus the mismatch reason instead of borrowing an
unrelated price.

Cached and uncached input are priced separately. DeepSeek reports
`prompt_cache_hit_tokens` and `prompt_cache_miss_tokens` alongside `prompt_tokens`, and the
cache-hit rate is roughly fifty times cheaper than the cache-miss rate, so the reader and judge
records keep that split instead of collapsing it into one blended input total. A response that
reports no split cannot be priced at either rate, so its stage records `null` with that reason
rather than an estimate.

Reader cost comes from the Reader's reported usage, Judge cost from the Judge's reported usage,
and each stage summary and manifest records the policy identity it was priced under. The unified
report adds `usage.reader_cost`, `usage.judge_cost`, `usage.estimated_cost_usd` (the sum, only
when every model stage that actually ran was priced in USD under one policy revision) and
`usage.estimated_cost_note` explaining how the total was computed or why it stays `null`. The
report decides which stages ran from the run manifest `modes` plus the stage summaries, so a
missing, unpriced, mismatched, or differently-revisioned stage cost keeps the total `null`
instead of silently summing the stages that happen to be present. Ingestion is model-free, so the
report records `usage.ingestion_cost` as zero tokens, `cost_usd: 0.0`, and that reason. Without a
policy the report keeps the `null` behavior and explains it.

Regenerate or inspect a report for any saved run without a model, provider, or server:

```bash
uv run --project evaluation longmemeval-v2 report \
  --run-dir /path/to/saved-run \
  --output-dir /path/to/new-report-artifacts
```

### LongMemEval-V2 full run (not executed)

A full-tier run has not been executed and is not approved, and its results must never be
confused with the smoke subset. The recorded prerequisites, configuration template,
cost-estimation and approval gates, output isolation, result labels, and failure-recovery
procedure live in [docs/longmemeval-v2-full-run.md](docs/longmemeval-v2-full-run.md); the
secret-free environment template lives in
[config/longmemeval-v2-run-config.example.env](config/longmemeval-v2-run-config.example.env). A
full-tier dataset lock and question manifest do not exist yet and must be created and reviewed
before any full run.

### What the LongMemEval-V2 workload evaluates

The smoke workload and any future full run measure one pipeline over fixed LongMemEval-V2
trajectories: whether the PowerContext Memory adapter retrieves citable evidence through public
interfaces under fixed upstream data, fixed questions, and a fixed context budget; the answer
accuracy, latency, context size, failures, and abstention of the configured Reader and Judge; and
whether saved outputs replay the same deterministic scoring inputs without a model.

They do not evaluate Handoff, cross-host recovery, normal Runtime persistence, or Work
Continuity. A ten-question smoke subset cannot be extrapolated to full benchmark performance,
general model capability, or product leadership, and it does not replace LoCoMo, SWE-bench Pro,
or real user-task acceptance. Scores must never be improved by rewriting gold prompts, reference
answers, or the Memory schema. Every smoke artifact is labelled `smoke-subset`; none of them is
a complete benchmark result.

The implemented scope and local validation evidence are summarized in
[docs/longmemeval-v2-smoke-delivery.md](docs/longmemeval-v2-smoke-delivery.md).
