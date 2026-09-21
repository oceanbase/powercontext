# LoCoMo-Plus benchmark

The default `memory` arm captures timestamped dialogue Sources, extracts conversational Memory, retrieves Memory
with hybrid search, generates an answer, and grades it with an explicitly selected judge model. Each run uses its own
SQLite database inside its results directory; the database configuration in the supplied environment file is not used.

## Dataset and evaluation contract

The `dataset/` directory contains a pinned ten-case smoke snapshot with complete conversation histories.
It can be loaded directly with `--dataset-file dataset/locomo_plus_smoke10.json` from this benchmark directory.
See [the dataset description](dataset/README.md) for sample IDs, provenance, and history coverage.
The complete upstream data is downloaded on demand into an ignored local cache for full or larger smoke runs.
The source is [xjtuleeyf/Locomo-Plus](https://github.com/xjtuleeyf/Locomo-Plus/tree/059f4e3d38f7f1f96765e8e2cb7de3097551bffb),
pinned to commit `059f4e3d38f7f1f96765e8e2cb7de3097551bffb`. The loader validates data structure without hash checks.

| Input | Published cases |
| --- | ---: |
| `locomo_plus.json` | 401 cognitive |
| `locomo10.json` | 1,986 factual |

The factual file is the copy in the pinned LoCoMo-Plus release. Its content differs from the existing LoCoMo
benchmark's file, so the two files and their results must not be treated as interchangeable.

The cognitive release contains causal (101), state (100), goal (100), and value (100) cases. The loader excludes
44 malformed cue dialogues instead of constructing empty prompts or repairing annotations silently. Every exclusion
retains its original case identity and reason in the audit. The full profile selects the 1,986 factual and
357 valid cognitive cases: **2,343 evaluated cases from 2,387 published cases**. It is labelled a subset of the
published release even when no user limit is applied.

| Cognitive relation | Published | Eligible |
| --- | ---: | ---: |
| causal | 101 | 85 |
| state | 100 | 95 |
| goal | 100 | 91 |
| value | 100 | 86 |

Cognitive histories restore dates from the pinned factual conversation data. History assignment uses a deterministic
seed (default `42`), and the resulting session and source mapping is recorded. Fifteen eligible cases have time-gap
text the adapter cannot parse; they retain the upstream zero-day fallback and are listed in the audit rather than
being described as known temporal gaps. Gold answers, relation labels, and
judge rubrics are excluded from normal captured Sources and generator prompts. The generator receives a common
answer policy without a cognitive-task hint. The diagnostic `oracle-cue` arm explicitly supplies the gold-selected cue.

This is a **PowerContext evaluation protocol**, not a paper-comparable reproduction. Cue exclusions, restored dates,
prompt policy, model choice, and history limits all affect the result. Upstream anomalies are documented in
[cue-format issue #2](https://github.com/xjtuleeyf/Locomo-Plus/issues/2) and
[timestamp issue #3](https://github.com/xjtuleeyf/Locomo-Plus/issues/3).

## Inspect and plan without model calls

Use the project environment installed with `uv sync`:

```bash
uv run python -m benchmark.locomo_plus inspect
uv run python -m benchmark.locomo_plus run --dry-run
uv run python -m benchmark.locomo_plus run --profile full --limit 2 --dry-run
```

These commands may download the pinned dataset but require no model credentials and make no inference requests.
`--data-directory PATH` changes the cache location. `inspect` reports provenance, raw counts, exclusions, and construction
policy. `--dry-run` reports the selected cases and ingestion plan before incurring inference cost.

Inspect and plan the bundled ten cases without downloading any data:

```bash
uv run python -m benchmark.locomo_plus inspect \
  --dataset-file benchmark/locomo_plus/dataset/locomo_plus_smoke10.json
uv run python -m benchmark.locomo_plus run \
  --dataset-file benchmark/locomo_plus/dataset/locomo_plus_smoke10.json \
  --limit 10 --dry-run
```

`--dataset-file` and `--data-directory` are mutually exclusive. The snapshot fixes history assignment at seed `42`
and supports smoke selections of up to ten cases. Use the upstream cache path for a different seed, larger smoke
selection, or full profile. Omitting `--dataset-file` preserves the upstream-loading workflow.

## Smoke and full runs

The runner reads generation and embedding settings from the same `.env` contract as the PowerContext Server.
Copy [.env.example](.env.example) to a local file and fill in the model names, endpoints, API key, and embedding
dimension. From the repository root:

```bash
cp benchmark/locomo_plus/.env.example benchmark/locomo_plus/.env
```

The commands below use `--env-file benchmark/locomo_plus/.env`; an existing compatible environment file can also be
passed directly. No external database configuration is needed because each run uses its own SQLite database.
Keep credentials outside tracked files. The judge model must be passed explicitly on every paid run. To use an
independent judge, choose a different model from the configured generator; explicit selection alone does not establish
judge independence or human agreement. Using the same model is supported and flagged in the manifest.

A single command runs the fixed four-case smoke profile, covering causal, state, goal, and value:

```bash
uv run python -m benchmark.locomo_plus run \
  --env-file benchmark/locomo_plus/.env \
  --judge-model "openai:YOUR_JUDGE_MODEL" \
  --run-id locomo-plus-smoke
```

Smoke uses stable IDs `cognitive:0000`, `cognitive:0101`, `cognitive:0188`, and `cognitive:0301` by default.
**Smoke and full both retain every historical session and every turn, with identical dates, content, and order.**
The only workload reduction is the selected case count: extraction, retrieval, answering, scoring, and their settings
use the same implementation and defaults. Each manifest records the selected history's session count, UTF-8 byte
count, SHA-256, and a comparison with the corresponding full-dataset history.

For a ten-case smoke using the bundled complete histories:

```bash
uv run python -m benchmark.locomo_plus run \
  --dataset-file benchmark/locomo_plus/dataset/locomo_plus_smoke10.json \
  --env-file benchmark/locomo_plus/.env \
  --judge-model "openai:YOUR_JUDGE_MODEL" \
  --run-id locomo-plus-smoke-10 \
  --limit 10
```

Limits above four extend the fixed anchors by alternating causal, state, goal, and value in published order.
Every selected ID is saved in the manifest. `--limit 50` selects 13 causal, 13 state, 12 goal, and 12 value cases.
Asking for more smoke cases than are available fails before model calls. This deterministic selection is not a
random population sample. Reducing the limit never shortens any selected case's history.

The full profile is explicit and retains complete histories by default:

```bash
uv run python -m benchmark.locomo_plus run \
  --profile full \
  --env-file benchmark/locomo_plus/.env \
  --judge-model "openai:YOUR_JUDGE_MODEL" \
  --run-id locomo-plus-full
```

A full run can incur substantial extraction, embedding, generation, and judge cost. Use `--profile full --limit 2`
for a small run through the full-profile configuration; the manifest records the subset. `--max-history-sessions N`
is an explicit shortened-history diagnostic, recorded as a subset that is not equivalent to a full-history run. `--top-k` defaults to `5`; `--max-tokens` defaults to `512` for each
answer and judge response. The response cap does not bound ingestion requests or input tokens. Cases run sequentially.
Memory extraction honors the configured `generation_max_requests` (PowerContext default: `2`) for both smoke and full.
This allows the normal structured-output correction attempt when a response fails schema validation; it does not
silently add retries beyond the configured limit. Semantic evidence validation errors still fail the case.

## Diagnostic arms

Choose one arm per run with `--arm`, and use a separate run directory for each configuration.

| Arm | Generator context |
| --- | --- |
| `memory` (default) | PowerContext hybrid Memory search results |
| `memory-source` | Retrieved Memory plus its cited original Source sessions |
| `query-only` | Question alone, without ingestion or retrieval |
| `oracle-cue` | Gold-selected cognitive cue, without Memory retrieval; requires a cognitive-only selection |
| `full-context` | All selected history sessions, without Memory retrieval |

The oracle and full-context arms diagnose generation behavior when retrieval is removed. Their scores are not Memory
retrieval scores. Use the smoke profile for `oracle-cue`; a full profile containing factual cases is rejected.
`--max-history-sessions` also changes the selected history for applicable diagnostic arms.

## Scoring and artifacts

The judge returns `0`, `0.5`, or `1` for factual single-hop, multi-hop, and commonsense cases. Factual temporal,
adversarial, and all cognitive cases use **binary `0` / `1` scoring**. Invalid judge responses are recorded as failures.
The judge prompts are independently authored adaptations of the released scoring semantics and have not received
human agreement validation for this harness. Saved judge inputs include their rubric version and content hash.
Factual and cognitive scores are reported separately, with cognitive results split by causal, state, goal, and value.
The report distinguishes quality among completed evaluations from end-to-end success over all planned valid cases.
Dataset exclusions are audited separately from inference and infrastructure failures.

Outputs default to `benchmark/locomo_plus/results/<run-id>/`:

- `run.json`: immutable run identity, selection, model configuration, harness fingerprint, and execution settings.
- `dataset-audit.json`: pinned provenance, source counts, construction policy, and excluded rows.
- `inputs.jsonl`: selected session inputs and source identities; Memory arms capture these sessions.
- `ingestion.json`: resumable ingestion state, failed session positions/source IDs, and sanitized exception chains
  with validation error codes. Failure history is retained after a successful resume.
- `diagnostics/`: captured extraction requests and model responses for failed sessions, when available. These
  contain conversation data and model output; keep the results directory local. Provider headers and arbitrary
  provider exception messages are not included in the diagnostics produced by the runner.
- `observations.jsonl`: frozen retrieval/context, rendered generator and judge inputs, answers, judgments, usage,
  latency, and classified errors.
- `summary.json` and `summary.md`: grouped quality, completion, retrieval, usage, and cost reports.
- `state.sqlite3`: the run's isolated local PowerContext database for Memory arms.

The benchmark-local `.gitignore` ignores generated results while retaining `results/.gitkeep`.

Artifacts contain dataset text and model output, so keep them local unless deliberately sharing a reviewed result.
Credentials and the environment database URL are not included in run manifests. Reuse the same run ID and output
location with the same settings to resume. Successful cases are retained; failed cases are retried. A judge-only
failure reuses the frozen generated answer. Configuration or dataset changes require a separate run.
The run command exits with status `1` if cases fail to execute or remain unobserved. A completed evaluation with an
incorrect model answer still exits with status `0`; answer quality is recorded in the report.

Rebuild summary artifacts from saved observations without downloading data, opening the database, or calling a model:

```bash
uv run python -m benchmark.locomo_plus replay \
  --run-directory benchmark/locomo_plus/results/locomo-plus-smoke
```

Replay uses saved answers and judgments; it does not ask a new judge to grade them.

Cost is **unknown**, rather than zero, when no explicit pricing table is supplied. Pass `--prices prices.json` to
record versioned per-model USD prices for available provider-reported usage:

```json
{
  "version": "provider-prices-YYYY-MM-DD",
  "models": {
    "openai:YOUR_GENERATOR_MODEL": {
      "input_per_million_usd": 1.0,
      "output_per_million_usd": 2.0
    },
    "openai:YOUR_JUDGE_MODEL": {
      "input_per_million_usd": 1.0,
      "output_per_million_usd": 2.0
    }
  }
}
```

The numbers above illustrate the schema and are not provider prices. Missing model prices or missing usage cannot
be interpreted as free inference. Provider token counts and context byte counts have different meanings; neither a
partial usage report nor an output-token cap establishes a total run budget.

## Local validation

Deterministic loader, prompt, scoring, and CLI tests use synthetic fixtures and do not require paid services:

```bash
uv run pytest tests/test_locomo_plus*.py
```

Use the bounded smoke command above for a real-service acceptance check. Full-run support is separate from executing
all published cases; a short smoke result cannot establish full-benchmark accuracy.
