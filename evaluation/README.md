# PowerContext evaluation console

This directory contains a self-progressing SWE-bench Pro evaluation service. A batch can run the paired OFF/ON
experiment or only one Arm to reduce cost and latency. The web process owns the HTTP API and report UI; the worker
owns task execution, retries, resource cleanup, and durable recovery.

The service is intentionally deployment-neutral. Host names, operators, filesystem roots, optional proxy endpoints, Docker
network ranges, credentials, and service locations are supplied by the operator. The repository does not contain a
production environment file or a ready-to-install host-specific systemd unit.

## Runtime requirements

- Linux, Git, Python 3.11 or newer, `uv`, and Node.js/npm
- Docker with permission to pull images, create isolated bridge networks, and run evaluation containers
- Codex CLI and a valid Codex `auth.json`
- [`regctl`](https://github.com/regclient/regclient) for importing task images that are not already present
- enough disk space and inodes for the selected parallelism

TokensFlow telemetry and the credential-free loopback proxy are optional integrations. Both are disabled by default;
the normal open-source path uses native container egress and starts neither a proxy relay nor a TokensFlow
daemon/finalizer.

Install and validate from the repository root:

```bash
uv sync --project evaluation --frozen
uv run --project evaluation pytest -c evaluation/pyproject.toml evaluation/tests -m "not live" -q
uv run --project evaluation ruff check evaluation
uv run --project evaluation ruff format --check evaluation
uv run --directory evaluation ty check src
```

Build and test the web UI:

```bash
cd evaluation/web
npm ci
npm test -- --run
npm run build
```

## Quick start

The commands below prepare a single-host development deployment. They intentionally bind the unauthenticated console
to `127.0.0.1`. Put an authenticating reverse proxy in front of the console before allowing access from another
machine; do not expose the HTTP service directly to an untrusted network.

### 1. Prepare the evaluation root

Choose an absolute writable directory and create the layout expected by the default configuration. The Web and
Worker processes must be able to read this repository and the protected configuration; the Worker also needs Docker
access and write access to the evaluation root.

```bash
export REPOSITORY_ROOT="$(pwd -P)"
export EVALUATION_ROOT=/srv/powercontext-eval

install -d -m 0700 \
  "$EVALUATION_ROOT/bin" \
  "$EVALUATION_ROOT/cache" \
  "$EVALUATION_ROOT/codex-home" \
  "$EVALUATION_ROOT/config" \
  "$EVALUATION_ROOT/source" \
  "$EVALUATION_ROOT/venvs"
```

### 2. Install the pinned SWE-bench Pro harness

The runner accepts exactly harness commit `ca10a60a5fcae51e6948ffe1485d4153d421e6c5`. That commit contains the pinned
731-row dataset; the console verifies its schema, order, row count, and SHA-256 before admitting a batch.

```bash
git clone https://github.com/scaleapi/SWE-bench_Pro-os.git \
  "$EVALUATION_ROOT/cache/swebench-pro.git"
git -C "$EVALUATION_ROOT/cache/swebench-pro.git" checkout --detach \
  ca10a60a5fcae51e6948ffe1485d4153d421e6c5
test "$(git -C "$EVALUATION_ROOT/cache/swebench-pro.git" rev-parse HEAD)" = \
  ca10a60a5fcae51e6948ffe1485d4153d421e6c5
test "$(sha256sum "$EVALUATION_ROOT/cache/swebench-pro.git/helper_code/sweap_eval_full_v2.jsonl" | cut -d' ' -f1)" = \
  b5b2462bfbf5aeb2cb7ba7d215778a1768b85f9d7ad7f748546c7f80a0ad1510

uv venv --python 3.11 "$EVALUATION_ROOT/venvs/swebench-pro-ca10a60"
uv pip install \
  --python "$EVALUATION_ROOT/venvs/swebench-pro-ca10a60/bin/python" \
  -r "$EVALUATION_ROOT/cache/swebench-pro.git/requirements.txt"
```

### 3. Prepare source, tools, credentials, and frontend

The source is a bare mirror so every submitted branch, tag, or commit resolves to immutable Git data rather than a
mutable developer working tree. Refresh or replace this mirror deliberately when evaluating newer PowerContext
commits.

```bash
git clone --mirror "$REPOSITORY_ROOT" "$EVALUATION_ROOT/source/powercontext.git"

install -m 0755 "$(command -v codex)" "$EVALUATION_ROOT/bin/codex"
install -m 0755 "$(command -v uv)" "$EVALUATION_ROOT/bin/uv"
install -m 0755 "$(command -v regctl)" "$EVALUATION_ROOT/bin/regctl"

install -m 0600 "${CODEX_HOME:-$HOME/.codex}/auth.json" \
  "$EVALUATION_ROOT/codex-home/auth.json"
```

If the Codex account uses a custom provider, also copy its configuration without printing it:

```bash
install -m 0600 "${CODEX_HOME:-$HOME/.codex}/config.toml" \
  "$EVALUATION_ROOT/codex-home/config.toml"
```

Build the frontend from the repository checkout that will run the services:

```bash
cd "$REPOSITORY_ROOT/evaluation/web"
npm ci
npm test -- --run
npm run build
cd "$REPOSITORY_ROOT"
```

### 4. Create and validate the runtime environment

There is one platform runtime configuration file. Copy the example, edit every path that differs from the layout
above, and keep it private. In particular, set `POWERCONTEXT_EVAL_FRONTEND_DIST` to the absolute
`$REPOSITORY_ROOT/evaluation/web/dist` path. Uncomment `POWERCONTEXT_EVAL_CODEX_CONFIG` only if the file was installed
in the previous step.

```bash
install -m 0600 evaluation/deploy/powercontext-eval.env.example \
  "$EVALUATION_ROOT/config/evaluation-console.env"
${EDITOR:-vi} "$EVALUATION_ROOT/config/evaluation-console.env"

set -a
. "$EVALUATION_ROOT/config/evaluation-console.env"
set +a

uv run --project evaluation python -c \
  'import os; from powercontext_eval.web.config import WebConfig; WebConfig.from_environment(os.environ); print("configuration valid")'
docker info >/dev/null
```

Use `POWERCONTEXT_EVAL_USAGE_MODE=api_key` for API-key credentials. That mode skips subscription-usage probing and
treats quota admission as sufficient; filesystem and Docker admission remain active. Keep `subscription` for a
Codex subscription whose CLI exposes the supported usage probe.

### 5. Start Web and Worker

From the repository root, load the same protected environment in two terminals:

```bash
# Terminal 1
set -a; . "$EVALUATION_ROOT/config/evaluation-console.env"; set +a
uv run --project evaluation powercontext-eval web
```

```bash
# Terminal 2
set -a; . "$EVALUATION_ROOT/config/evaluation-console.env"; set +a
uv run --project evaluation powercontext-eval worker
```

Verify the control plane before submitting work:

```bash
curl --fail --silent http://127.0.0.1:8787/api/health
```

Open `http://127.0.0.1:8787/`, choose `swebench-pro-stability-v1` and an `OFF + ON`, `ON only`, or `OFF only` run.
This 24-task set is the deployment regression suite; use it before the 731-task `swebench-pro-public-v2` set. A
completed aggregate report can freeze any executed Arm as an immutable baseline. Reports may select multiple
compatible baselines for historical comparison without rerunning evaluation, while the baseline library lists the
newest saved baselines first. The same bounded batch can be created from the CLI after Web and Worker are healthy:

```bash
uv run --project evaluation powercontext-eval swebench-pro create-batch \
  --console-url http://127.0.0.1:8787 \
  --task-set swebench-pro-stability-v1 \
  --treatment-mode on_only \
  --powercontext-ref latest \
  --idempotency-key "stability-$(date -u +%Y%m%dT%H%M%SZ)"
```

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
uv run --project evaluation powercontext-eval longmemeval-v2 smoke \
  --harness-root /path/to/LongMemEval-V2 \
  --data-root /path/to/longmemeval-v2-data \
  --dataset-lock evaluation/locks/longmemeval-v2-small-v1.dataset-lock.json \
  --smoke-manifest evaluation/locks/longmemeval-v2-small-v1.smoke.json \
  --output-dir /path/to/new-smoke-artifacts
```

The command refuses a harness checkout at a different commit, mismatched input
hashes, invalid or incomplete smoke coverage, and an existing output directory.
It writes `manifest.json` and `subset.json`, both labelled as a smoke subset.

### PowerContext Memory adapter bootstrap

Run the bootstrap with a Python environment containing the pinned upstream
harness dependencies. It validates the clean harness commit, registers
`memory_type: powercontext` in memory, and then forwards all remaining arguments
to the unchanged upstream harness:

```bash
python evaluation/scripts/run_longmemeval_v2_harness.py \
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
uv run --project evaluation powercontext-eval longmemeval-v2 retrieval-smoke \
  --harness-root /path/to/LongMemEval-V2 \
  --data-root /path/to/longmemeval-v2-data \
  --dataset-lock evaluation/locks/longmemeval-v2-small-v1.dataset-lock.json \
  --smoke-manifest evaluation/locks/longmemeval-v2-small-v1.smoke.json \
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
uv run --project evaluation powercontext-eval longmemeval-v2 retrieval-smoke \
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
uv run --project evaluation powercontext-eval longmemeval-v2 prepare-smoke \
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
uv run --project evaluation powercontext-eval longmemeval-v2 reader-smoke \
  --prepared-dir /path/to/prepared-prompt-artifacts \
  --provider deepseek-openai \
  --model deepseek-flash \
  --token-env DEEPSEEK_API_KEY \
  --max-tokens 512 \
  --temperature 0 \
  --output-dir /path/to/new-reader-artifacts
```

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
uv run --project evaluation powercontext-eval longmemeval-v2 score-smoke \
  --reader-dir /path/to/reader-artifacts \
  --data-root /path/to/longmemeval-v2-data \
  --dataset-lock evaluation/locks/longmemeval-v2-small-v1.dataset-lock.json \
  --smoke-manifest evaluation/locks/longmemeval-v2-small-v1.smoke.json \
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
uv run --project evaluation powercontext-eval longmemeval-v2 replay-score \
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
uv run --project evaluation powercontext-eval longmemeval-v2 run-smoke \
  --data-root /path/to/longmemeval-v2-data \
  --dataset-lock evaluation/locks/longmemeval-v2-small-v1.dataset-lock.json \
  --smoke-manifest evaluation/locks/longmemeval-v2-small-v1.smoke.json \
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
uv run --project evaluation powercontext-eval longmemeval-v2 run-smoke \
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
uv run --project evaluation powercontext-eval longmemeval-v2 report \
  --run-dir /path/to/saved-run \
  --output-dir /path/to/new-report-artifacts
```

### LongMemEval-V2 full run (not executed)

A full-tier run has not been executed and is not approved, and its results must never be
confused with the smoke subset. The recorded prerequisites, configuration template,
cost-estimation and approval gates, output isolation, result labels, and failure-recovery
procedure live in [docs/longmemeval-v2-full-run.md](docs/longmemeval-v2-full-run.md); the
secret-free environment template lives in
[deploy/longmemeval-v2-run-config.example.env](deploy/longmemeval-v2-run-config.example.env). A
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

## Configuration files

Only the environment file configures the evaluation platform. The other files either belong to Codex or are
deployment templates:

| File | Required | Purpose |
| --- | --- | --- |
| `evaluation-console.env` | yes | Web, Worker, storage, benchmark, retries, parallelism, and optional integrations |
| `auth.json` | yes | Codex credentials; referenced by path and copied into isolated task homes |
| `config.toml` | no | Custom Codex provider/model configuration |
| `powercontext-eval.env.example` | no | Safe template; never loaded directly in production |
| `*.service.in` | no | Unrendered systemd templates; they are not additional application configuration |

## Configuration reference

Copy `evaluation/deploy/powercontext-eval.env.example` to a protected location, replace every applicable example
value, and set mode `0600`. Only `POWERCONTEXT_EVAL_ROOT` is required by the configuration loader; the runtime tools,
dataset, credentials, and source checkout must exist at their derived or explicitly configured paths before work is
claimed.

Derived paths are rooted below `POWERCONTEXT_EVAL_ROOT` unless explicitly overridden. The settings fall into these
groups:

| Group | Variables |
| --- | --- |
| Service and storage | `ROOT`, `HOST`, `PORT`, `DATABASE_PATH`, `RUN_ROOT`, `FRONTEND_DIST` |
| Benchmark inputs | `POWERCONTEXT_SOURCE`, `HARNESS_ROOT`, `HARNESS_PYTHON`, `DATASET_PATH` |
| Executables and credentials | `CODEX_BINARY`, `CODEX_MODELS`, `CODEX_CONFIG`, `UV_BINARY`, `REGISTRY_BINARY`, `AUTH_JSON` |
| Scheduling and recovery | `TASK_PARALLELISM`, `MAX_ATTEMPTS`, `LEASE_SECONDS`, `POLL_SECONDS`, `WORKSPACE_RECLAIM_INTERVAL_SECONDS` |
| Resource admission | `FILESYSTEM_MIN_FREE_BYTES`, `FILESYSTEM_MIN_FREE_INODES`, `DOCKER_NETWORK_POOL` |
| Usage admission | `USAGE_MODE`, `USAGE_PAUSE_PERCENT`, `USAGE_PROBE_SECONDS`, `USAGE_PROBE_TIMEOUT_SECONDS`, `USAGE_SNAPSHOT_MAX_AGE_SECONDS` |
| Optional integrations | `TOKENSFLOW_ENABLED`, TokenFlow settings, `PROXY_URL`, `EXTRA_NO_PROXY_HOSTS` |

Every name in the table has the `POWERCONTEXT_EVAL_` prefix. Important behavior-changing settings are:

- `POWERCONTEXT_EVAL_TASK_PARALLELISM` controls concurrent OFF/ON task pairs and defaults to `1`.
- `POWERCONTEXT_EVAL_MAX_ATTEMPTS` bounds durable per-task retries and defaults to `5`.
- `POWERCONTEXT_EVAL_USAGE_MODE=api_key` disables subscription quota probing; API-key mode is treated as having
  sufficient quota while still enforcing resource admission.
- `POWERCONTEXT_EVAL_TOKENSFLOW_ENABLED=true` explicitly enables internal TokensFlow telemetry and then requires its
  binary, user home, and egress network settings. When false or absent, no TokensFlow profile, daemon, finalizer,
  mount, or retained audit artifact is created.
- `POWERCONTEXT_EVAL_PROXY_URL` explicitly enables the loopback proxy relay. When absent, task networks use native
  egress and proxy variables are cleared from managed task processes.
- `POWERCONTEXT_EVAL_EXTRA_NO_PROXY_HOSTS` is a comma-separated, validated list appended to loopback-only
  `NO_PROXY`. It is valid only with the evaluation proxy and is recorded in each retained report.
- `POWERCONTEXT_EVAL_DOCKER_NETWORK_POOL` selects the private IPv4 pool used for isolated /28 task networks. It must
  provide at least 32 subnets and is recorded in each retained report.

Credential files are referenced by path and copied into isolated task homes. Never place credential values in the
environment example, command line, logs, reports, or repository.

## Standalone runner

The systemd templates and local commands must use the repository root as their working directory. A packaging
workflow that starts the service outside a Git checkout must provide the exact 40-character source revision in
`POWERCONTEXT_EVAL_BUILD_REVISION`.

The standalone runner defaults to native networking with TokensFlow disabled. Other paths derive from the supplied
root and remain individually overridable:

```bash
uv run --project evaluation powercontext-eval swebench-pro run \
  --root /srv/powercontext-eval \
  --instance-id instance_owner__repository-revision
```

An internal deployment can explicitly add `--proxy-url http://127.0.0.1:8081 --tokensflow
--tokensflow-egress-network bridge`; TokensFlow binary and profile paths derive from the root unless overridden.

## systemd templates

`evaluation/deploy/*.service.in` are templates, not installable units. Render all placeholders into a staging
directory, inspect the result, and run `systemd-analyze verify` before installation. Required placeholders are:

- `@EVALUATION_ROOT@`: absolute writable evaluation root
- `@REPOSITORY_ROOT@`: absolute deployment checkout
- `@UV_BINARY@`: absolute `uv` executable
- `@EVALUATION_USER@` and `@EVALUATION_GROUP@`: unprivileged web identity
- `@EVALUATION_WORKER_USER@` and `@EVALUATION_WORKER_GROUP@`: worker identity with the required Docker access

The web template is sandboxed to the evaluation root. The worker template intentionally does not claim a generic
sandbox because Docker access and host cleanup policy vary by deployment. Do not grant the web role Docker access.

## Control and recovery model

Only operator pause or cancel changes durable control intent. Task failures do not pause healthy peers. Retriable
task failures enter durable backoff and another free worker slot may claim the next eligible task. The default five
attempt budget uses 30, 120, 300, and 600 second delays. An exhausted or non-retriable task becomes a retained
failure while the rest of the batch continues.

The worker performs startup-only orphan recovery under a process lock. A normal claim never steals another slot's
lease. Loss of attempt ownership cancels child processes, after which the lifecycle cleaner removes exact
attempt-owned containers, networks, and scratch workspaces. Retained `/runs` reports and private incident evidence
remain available for audit. When TokensFlow is explicitly enabled, finalizer-owned containers are excluded until
their durable finalization job is terminal.

Admission pressure is transient: disk, inode, Docker, or usage probe pressure prevents new claims without changing
batch intent. Workspace reclamation runs continuously and removes only terminal attempts after report publication;
it never deletes running, queued, retryable, or finalizer-owned state.

## Operational acceptance

Before resuming a real batch:

1. verify the exact source revision and a clean checkout;
2. validate the protected environment file without displaying it;
3. run backend, lint, format, type, frontend test, and frontend build gates;
4. verify rendered systemd units and confirm web/worker health;
5. run a small OFF/ON batch and inspect Gold, OFF, ON, official evaluator, retry, cleanup, and report evidence;
6. confirm `active_task_pairs` never exceeds configured parallelism;
7. confirm unrelated host services are neither dependencies nor restart targets;
8. perform a secret scan over retained artifacts and logs;
9. document the exact rollback revision before increasing parallelism.

Rollback is a source checkout and service restart operation. Do not delete the database, `/runs`, incident evidence,
or task workspaces needed by queued and retryable attempts. Do not use global Docker prune as an evaluation cleanup
mechanism.
