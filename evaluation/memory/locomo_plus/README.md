# LoCoMo-Plus benchmark

The default `memory` arm captures timestamped dialogue Sources, extracts conversational Memory, retrieves Memory
with hybrid search, generates an answer, and grades it with an explicitly selected judge model. The runner uses the
Server database configuration from the supplied environment file. When no database settings are supplied, each run
uses its own SQLite database inside its results directory.

The six model-comparison scores displayed in the website and root READMEs were supplied by the project owner.
Their run manifests, summaries, protocol identities, model/Judge configurations, run hashes and raw outputs are
not publicly available for verification. This guide documents a reproducible harness workflow; it does not
establish that those specific scores have been reproduced or independently verified.

## Dataset and evaluation contract

The `dataset/` directory contains a pinned ten-case smoke snapshot with complete conversation histories.
Its provenance is `powercontext-locomo-plus-v1`, including the 44 exclusions recorded when it was constructed.
Loading the snapshot preserves that provenance; it does not apply the full-data v2 adapter or reclassify the snapshot
as a no-exclusion dataset.
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

The cognitive release contains causal (101), state (100), goal (100), and value (100) cases. Matching the upstream
implementation, the loader scans each physical cue line, retains lines beginning with `A:` or `B:`, and ignores other
lines. It does not require exactly one `A:`/`B:` pair and does not exclude records based on cue turn count. The full
profile therefore selects all 1,986 factual and 401 cognitive cases: **2,387 evaluated cases**.

| Cognitive relation | Published | Eligible |
| --- | ---: | ---: |
| causal | 101 | 101 |
| state | 100 | 100 |
| goal | 100 | 100 |
| value | 100 | 100 |

Cognitive histories restore dates from the pinned factual conversation data. History assignment uses a deterministic
seed (default `42`), and the resulting session and source mapping is recorded. Fifteen eligible cases have time-gap
text the adapter cannot parse; they retain the upstream zero-day fallback and are listed in the audit rather than
being described as known temporal gaps. Gold answers, relation labels, and
judge rubrics are excluded from normal captured Sources and generator prompts. The generator receives a common
answer policy without a cognitive-task hint. The diagnostic `oracle-cue` arm explicitly supplies the gold-selected cue.

This is a **PowerContext evaluation protocol**, not a paper-comparable reproduction. Cue line parsing, restored dates,
prompt policy, model choice, and history limits all affect the result. Upstream anomalies are documented in
[cue-format issue #2](https://github.com/xjtuleeyf/Locomo-Plus/issues/2) and
[timestamp issue #3](https://github.com/xjtuleeyf/Locomo-Plus/issues/3).

## Benchmark-only Jev reranking

Jev integration lives entirely in this benchmark; it does not modify `src/powercontext` or enable a global Runtime
default. `memory_reranking.py` implements `DecisionMemoryReranker` against the existing `MemoryReranker` port.
`jev.py` maps native SystemOne Choice responses to the existing `DecisionRequest`/`DecisionResult` types;
`jev_transport.py` owns the bounded connection pool, `jev_diagnostics.py` captures sanitized transport events, and
`decision.py` assembles the provider and isolates per-case audit state.

The consumer sends the current query as `subject` and one Memory as `evidence`. It retains `yes` and healthy `abstain`
results in coarse retrieval order, skips `no`, and stops after a completed batch provides enough results. An all-`no`
scan uses a marked coarse fallback. Sparse results are not padded unless `fill_to_limit=True` is explicitly selected.
Provider errors, degraded decisions, and timeouts fail the search; cancellation propagates. Connection retries are
disabled by default. An explicit `connect_attempts=2` or `3` permits retries only when transport evidence proves that
the POST has not started; it never retries an already-sent request or disables TLS verification.

Use the Python assembly API; passing `decision_model=` alone does not enable reranking, and the generic CLI's
`--rerank-model` selects a listwise LLM, not Jev. The following example runs the bundled smoke selection with up to
8 concurrent cases and 8 in-flight Jev decisions, with at most 4 decisions per case at a time:

```python
import asyncio
import hashlib
from contextlib import AsyncExitStack
from pathlib import Path

from evaluation.memory.locomo_plus.dataset import load_smoke_dataset
from evaluation.memory.locomo_plus.decision import ConcurrentDecisionReranker, open_decision_reranker
from evaluation.memory.locomo_plus.jev import JevConfig
from evaluation.memory.locomo_plus.runner import load_settings, run_benchmark


async def main():
    settings = load_settings(Path(".env"))
    # A local dotenv file containing JEV_API_KEY, JEV_BASE_URL, and JEV_MODEL.
    # Do not execute a curl example as a configuration file.
    jev = JevConfig.from_env_file("evaluation/memory/locomo_plus/.env_jev")
    output = Path("evaluation/memory/locomo_plus/results/jev-smoke")
    output.mkdir(parents=True, exist_ok=True)
    limits = {
        "concurrency": 4,
        "max_inflight": 8,
        "timeout_seconds": 240,
        "request_timeout_seconds": 60,
        "connect_attempts": 1,
        "fill_to_limit": False,
    }
    async with AsyncExitStack() as resources:
        template = await open_decision_reranker(
            "jev", inference=settings.inference, jev=jev,
            output_directory=output, resources=resources, **limits,
        )
        await run_benchmark(
            load_smoke_dataset(), settings=settings, output_directory=output,
            run_id="jev-smoke", judge_model="openai:YOUR_JUDGE_MODEL",
            arm="memory-source", concurrency=8, top_k=8,
            memory_rerank=True, rerank_candidate_limit=30,
            rerank_model=jev.model,
            memory_reranker=ConcurrentDecisionReranker(template, output),
            rerank_identity={
                "backend": "jev", "model": jev.model,
                "endpoint_sha256": hashlib.sha256(jev.base_url.encode()).hexdigest(),
                **limits,
            },
        )


asyncio.run(main())
```

This example defaults to isolated SQLite. Pass `database="oceanbase"` to use the environment's OceanBase settings.
For a full run, load the complete upstream dataset and set `profile="full"`; the smoke snapshot is not the full dataset.
An injected reranker requires `memory_rerank=True` and a nonempty, credential-free `rerank_identity`. Set `rerank_model`
to the actual decision model so usage is not attributed to the answer model. Record all provider, budget, timeout,
fallback, and retry settings in the identity; changed identities cannot resume an existing run.

`decisions.jsonl` records each logical decision attempt, including failures. `decision-searches.jsonl` records each
case's candidate pool, selection, `decision_count`, and request usage. These contain query/Memory text and stay in the
ignored results directory. Runtime `generation_calls` still counts one rerank operation, **not** the number of
per-candidate decisions. Read `decision_count` for logical decisions and `usage.requests`/transport traces for HTTP
attempts; a request count is not proof of provider receipt or billing. Unknown token counts remain unknown.

Keep `.env_jev` local and untracked; `.env.example` contains only optional placeholder settings. No historical logs,
database snapshots, or credentials are required to import these modules. Tests under `tests/test_locomo_plus_*` cover
the benchmark-only consumer, provider mapping, transport failures, cancellation, and Runtime injection with SQLite;
offline tests do not establish current availability or scores of a real Jev service.

## Inspect and plan without model calls

Use the project environment installed with `uv sync`:

```bash
uv run python -m evaluation.memory.locomo_plus inspect
uv run python -m evaluation.memory.locomo_plus run --dry-run
uv run python -m evaluation.memory.locomo_plus run --profile full --limit 2 --dry-run
```

These commands may download the pinned dataset but require no model credentials and make no inference requests.
`--data-directory PATH` changes the cache location. `inspect` reports provenance, raw counts, exclusions, and construction
policy. `--dry-run` reports the selected cases and ingestion plan before incurring inference cost. Its Judge budget
includes one verdict per case and an additional claim-projection request per Cognitive case, with separate counts for
both stages. These counts assume a fresh run without retries; extraction, embedding, and reranking add model calls.

Inspect and plan the bundled ten cases without downloading any data:

```bash
uv run python -m evaluation.memory.locomo_plus inspect \
  --dataset-file evaluation/memory/locomo_plus/dataset/locomo_plus_smoke10.json
uv run python -m evaluation.memory.locomo_plus run \
  --dataset-file evaluation/memory/locomo_plus/dataset/locomo_plus_smoke10.json \
  --limit 10 --dry-run
```

`--dataset-file` and `--data-directory` are mutually exclusive. The snapshot fixes history assignment at seed `42`
and supports smoke selections of up to ten cases. Use the upstream cache path for a different seed, larger smoke
selection, or full profile. Omitting `--dataset-file` preserves the upstream-loading workflow.

## Smoke and full runs

The runner reads database, generation and embedding settings from the same `.env` contract as the PowerContext Server.
Copy [.env.example](.env.example) to a local file and fill in the model names, endpoints, API key, and embedding
dimension. From the repository root:

```bash
cp evaluation/memory/locomo_plus/.env.example evaluation/memory/locomo_plus/.env
```

The commands below use `--env-file evaluation/memory/locomo_plus/.env`; an existing compatible environment file can also be
passed directly. Without database settings, the runner creates `state.sqlite3` in the results directory. Explicit
database settings use `settings.database`, including a configured SQLite URL, OceanBase URL or seekdb path.
Memory arms require persistent storage; in-memory SQLite is rejected because it cannot retain state for resume.
The optional `--database sqlite` explicitly selects an isolated results-directory database; `--database oceanbase`
requires a matching OceanBase configuration. Omitting this option preserves the configured database selection.
Keep credentials outside tracked files. The judge model must be passed explicitly on every paid run. To use an
independent judge, choose a different model from the configured generator; explicit selection alone does not establish
judge independence or human agreement. Using the same model is supported and flagged in the manifest.

For OceanBase, set `POWERCONTEXT_SERVER_DATABASE_KIND=oceanbase` and
`POWERCONTEXT_SERVER_DATABASE_URL=mysql+aoceanbase://USER:PASSWORD@HOST:2881/EVALUATION_DB?charset=utf8mb4` in the selected environment
file. Create a dedicated evaluation database first: the runner initializes PowerContext tables in the configured
database and does not create or drop databases. Each results directory receives a persistent random Scope namespace,
so separate runs remain isolated even when they share a database and the same `--run-id`. Benchmark state remains in
that database for resume; dispose of a dedicated evaluation database only after its runs are no longer needed.

`run.json` and the summaries record the actual backend and a database target fingerprint. Connection credentials and
the raw database URL are excluded. OceanBase fingerprints use the installed dialect's effective host, port,
tenant/user and database, including query-string overrides such as `db`, `user`, `host` and `port`. When a Unix socket
is configured, its resolved path replaces the TCP host and port. Passwords in either the URL authority or query string,
TLS/authentication material and connection tuning options do not affect the fingerprint. The runtime overrides
`init_command` with its fixed transaction initialization command. Duplicate query parameters, `read_default_file`,
`read_default_group` and `sql_mode` are rejected before opening services because their routing is ambiguous or can
depend on external configuration or SQL. SQLite and seekdb fingerprints continue to identify the local database path.
Reusing a results directory
with a different database target is rejected before opening the database or calling models. Pending Memory runs
also verify that their saved Scopes still exist, including judge-only retries; missing Scopes are not recreated.
Completed runs can regenerate their summary without opening the database; use `replay` for explicitly offline checks.

A single command runs the fixed four-case smoke profile, covering causal, state, goal, and value:

```bash
uv run python -m evaluation.memory.locomo_plus run \
  --env-file evaluation/memory/locomo_plus/.env \
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
uv run python -m evaluation.memory.locomo_plus run \
  --dataset-file evaluation/memory/locomo_plus/dataset/locomo_plus_smoke10.json \
  --env-file evaluation/memory/locomo_plus/.env \
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
uv run python -m evaluation.memory.locomo_plus run \
  --profile full \
  --env-file evaluation/memory/locomo_plus/.env \
  --judge-model "openai:YOUR_JUDGE_MODEL" \
  --memory-extraction-model "openai:YOUR_EXTRACTION_MODEL" \
  --memory-extraction-timeout-seconds 120 \
  --run-id locomo-plus-full \
  --database oceanbase \
  --concurrency 4
```

A full run can incur substantial extraction, embedding, generation, and judge cost. Use `--profile full --limit 2`
for a small run through the full-profile configuration; the manifest records the subset. `--max-history-sessions N`
is an explicit shortened-history diagnostic, recorded as a subset that is not equivalent to a full-history run. `--top-k` defaults to `5`; `--max-tokens` defaults to `512` for each
answer and judge response. The response cap does not bound ingestion requests or input tokens. `--concurrency N`
controls the maximum number of in-flight cases and defaults to `1`. Memory preparation and retrieval for the same
conversation scope remain serialized to prevent duplicate extraction and usage-accounting races; different scopes
run concurrently and are scheduled round-robin.
`--memory-extraction-model` overrides the model used by the Memory extraction runtime without changing the configured
answer model or the explicit judge model. When omitted, extraction uses the configured generation model.
`--memory-extraction-timeout-seconds` similarly overrides only the extraction request timeout.
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

Outputs default to `evaluation/memory/locomo_plus/results/<run-id>/`:

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
- `state.sqlite3`: the run's isolated local PowerContext database when no database settings were supplied.

The benchmark-local `.gitignore` ignores generated results while retaining `results/.gitkeep`.

Artifacts contain dataset text and model output, so keep them local unless deliberately sharing a reviewed result.
Credentials and the environment database URL are not included in run manifests. OceanBase runs share the configured
database but use run-isolated benchmark scopes; changing the run ID creates a new logical namespace. Reuse the same run ID and output
location with the same settings to resume. Successful cases are retained; failed cases are retried. A judge-only
failure reuses the frozen generated answer. Configuration or dataset changes require a separate run.
Cancellation and result-collection failures cancel and await every case task before model and Runtime resources
are closed. Rerank usage is checkpointed per retrieval attempt and accumulated across retries, including failed
searches and searches followed by generation failures. Audited decision backends retain reported usage even when
a decision is degraded; unreported provider request or token counts remain unknown.
Runs without a saved database fingerprint and Scope namespace cannot be resumed; their saved results remain
available through `replay`. Preserve `ingestion.json` alongside `run.json` to retain the original Scope identities.
OceanBase manifests must also record `database_fingerprint_version: oceanbase-target-v2`. Unversioned OceanBase
fingerprints are not reinterpreted or migrated: their saved results remain replayable, but a new run needs a new
output directory. This version requirement does not apply to SQLite or seekdb manifests.
The run command exits with status `1` if cases fail to execute or remain unobserved. A completed evaluation with an
incorrect model answer still exits with status `0`; answer quality is recorded in the report.

Rebuild summary artifacts from saved observations without downloading data, opening the database, or calling a model:

```bash
uv run python -m evaluation.memory.locomo_plus replay \
  --run-directory evaluation/memory/locomo_plus/results/locomo-plus-smoke
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

## Reusing ingestion and retrieval comparisons

`--reuse-ingestion-directory PATH` reads complete Memory scopes from an existing run. The runner verifies
the database target fingerprint and its version, history hashes, extraction and embedding settings, the live cursor,
and the saved Memory snapshot before use. A changed database target is rejected before opening services; password
rotation alone does not change an OceanBase target. Donors without a database fingerprint cannot be reused.
It does not capture or extract Sources again. OceanBase reuse keeps the tenant's processing Family declarations
and uses the API-only role to prevent supervisor execution. SQLite and seekdb retain their processing Family
declarations and required `all` role; the reuse run closes the supervisor after opening Runtime and before retrieval.
Use a quiescent donor with no pending background work, since embedded Runtime startup precedes that shutdown.
Use a new output directory and run ID for each treatment. SQLite
reuse opens the donor database; OceanBase reuse uses the configured tenant and the donor's exact scope IDs.
For chained SQLite reuse (A to B to C), the runner verifies each saved donor link and opens A's backing database,
not a new database in B. Missing databases or changed donor identities are rejected before opening resources.
Keep the donor's Sources and Memory unchanged during a comparison. Recall statistics still accumulate in that
database; the new run records only its own retrieval usage and zero ingestion usage.

`--memory-rerank --rerank-model openai:gpt-4o-mini --rerank-candidate-limit 30` enables the built-in listwise
reranker. Its model uses the configured generation endpoint and headers. The hybrid backend candidate budget is
`max(4 * max(top_k, rerank_candidate_limit), 32)` with reranking, or `max(4 * top_k, 32)` without it. `--top-k`
controls final hits, while `--arm memory-source` expands their original sessions. Saved observations contain
the reranker candidate pool, selected ranks, fallback status, latency, and reported token usage.
The built-in reranker's effective timeout and request budget are part of the run identity; changing either requires
a new output directory. Custom decision-backend budgets belong in the explicit `rerank_identity` passed by the caller.

Answer protocol v2 resolves relative dates using Source dates and connects present requests to relevant earlier
experiences. Judge protocol v7 recognizes equivalent normalized dates. For Cognitive cases it first projects
specific claims added by the answer beyond the current request, without access to historical evidence. A second
call compares those claims to the historical evidence. This isolates present-situation paraphrases from Memory
use. Both stages are saved; copied claims and positive verdicts' supporting quotes are validated against their
original texts (case, whitespace and terminal punctuation are normalized). Each positive prediction-support span
must occur within one individual projected claim; joining separate claims cannot establish a supporting quote.
Negative verdict annotations are preserved verbatim and do not establish a positive claim.
A malformed projection or fabricated credited quote is a
judge failure, never a wrong-answer score. Judge retries reuse a valid frozen projection. A single true historical
connection can earn credit even if the answer also contains unrelated details. The projection adds one Judge
request per Cognitive case and is recorded separately in usage. Older frozen judgments remain replayable with
their original instructions and digest. Scores produced by different judge
protocols must be labelled separately; use the same protocol and judge model in controlled retrieval comparisons.

## Local validation

Deterministic loader, prompt, scoring, and CLI tests use synthetic fixtures and do not require paid services:

```bash
uv run pytest tests/test_locomo_plus*.py
```

Use the bounded smoke command above for a real-service acceptance check. Full-run support is separate from executing
all published cases; a short smoke result cannot establish full-benchmark accuracy.
