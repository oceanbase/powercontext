# LoCoMo end-to-end benchmark

This benchmark runs the public LoCoMo conversation-memory dataset through the real PowerContext chain:

```text
timestamped dialogue Source
  -> model-backed Memory extraction
  -> OceanBase FTS + vector Hybrid coarse search
  -> optional PowerContext Memory listwise rerank
  -> model-generated answer
  -> deterministic metrics + LLM correctness judge
```

## Evaluation contract

- Dataset: 10 conversations, 272 timestamped sessions, 5,882 dialogue turns, and 1,986 questions.
- Scored set: categories 1-4, 1,540 questions. Category 5 is excluded by this benchmark's scored-set contract.
- Scope: one registered PowerContext scope per two-speaker conversation. The run ID creates a separate namespace;
  a stable idempotency key reuses the same scope on resume. `ingestion.json` records its namespace and scope ID.
- Ingestion: each session is captured as one `ContentSource`, then flushed through the configured Memory extraction
  model. Gold answers and QA evidence annotations are never included in captured Source content.
- Extraction profile: `coding` keeps the default work-context policy; `conversation` preserves independently
  answerable conversational facts, exact time, lists, events, and history. The selected profile and instruction
  version are recorded in `run.json`.
- Retrieval: `hybrid`, Top 30 candidates by default. With `--rerank-mode llm`, PowerContext Runtime applies its
  versioned listwise Memory policy and returns no more than `--answer-k` hits. The benchmark does not construct a
  reranker or reorder hits. Reranking remains opt-in, so existing runs retain the original retrieval order.
- Answer: the configured generation model sees only the question, speaker names, retrieved Memory text, and cited
  Source dates. It does not see the gold answer.
- Inference-aware answer A/B: `--answer-inference-aware` applies the inference policy to every question.
  `--answer-unknown-fallback-inference` first uses the original Source Answer policy and retries only when its
  normalized answer is exactly `Unknown`. Both modes require `--answer-source-content`, are mutually exclusive and
  disabled by default, and record versioned contracts in `run.json`.
- Accuracy: normalized exact match, normalized token F1, reference-set F1, BLEU-1, and a binary LLM
  judge. Errors remain in the denominator and score zero.
- Retrieval: Hit, Recall, and MRR over cited evidence sessions. PowerContext Memory cites a Source session (`D1`),
  whereas LoCoMo gold evidence identifies a dialogue turn (`D1:3`), so this evidence score is intentionally
  session-level and looser than turn-level recall.
- Evidence hygiene: the original annotation is retained as `evidence_raw`. Compound strings are split into turn IDs,
  the upstream `D:11:26` typo is normalized to `D11:26`, numeric IDs are canonicalized, and one unresolvable
  standalone `D` is ignored for retrieval scoring. A few references point beyond the recorded turn count but still
  identify a valid session; they remain usable for the intentionally session-level score. Gold questions and answers
  are unchanged.
- Judge boundary: the configured generation model both answers and judges. The LLM-judge score is reproducible model
  evaluation, not an independent human label. Rerank, answer, and judge requests explicitly use temperature `0`;
  provider implementations may still retain some nondeterminism.
- Judge policy: `strict` rejects unsupported additions that change an answer and remains the default. The opt-in
  `--judge-profile topical` uses a more generous topical and time-equivalence policy;
  scores from the two profiles must not be compared as though they used the same label definition.

## Configuration

The runner reads the same `.env` contract as the PowerContext Server. At minimum, configure a database, a generation
model, and a complete embedding profile. Secret values and the database URL are never written to result artifacts.

```bash
uv run python -m evaluation.memory.locomo inspect --env-file .env
```

The checked `.env.example` lists variable names only. Keep real credentials in the repository-root `.env`, which is
already ignored.

## Run

A small real-service smoke run uses the first conversation and five scored questions:

```bash
uv run python -m evaluation.memory.locomo run \
  --env-file .env \
  --run-id locomo-smoke \
  --conversation-limit 1 \
  --question-limit 5
```

The complete reference-shaped run is:

```bash
uv run python -m evaluation.memory.locomo run \
  --env-file .env \
  --run-id locomo-full \
  --top-k 30
```

Retrieve a broad candidate pool, then send a more precise context to the answer model:

```bash
uv run python -m evaluation.memory.locomo run \
  --env-file .env \
  --run-id locomo-rerank \
  --top-k 30 \
  --answer-k 10 \
  --rerank-mode llm
```

The `llm` policy makes one structured listwise selection request inside PowerContext Memory search using the configured
generation model. Each observation reads the PowerContext rerank trace and records the coarse candidate pool, selected
original ranks, rerank latency, model usage, and evidence metrics before and after selection.

When extracted Memory has identified the right session but compressed away an exact name, place, number, or list
item, `--answer-source-content` expands only the Source sessions cited by the selected Memory entries. This remains
gold-free and does not search the full conversation; it is opt-in and records the exact Source IDs supplied to the
answer model.

For an isolated inference-answering treatment, keep retrieval fixed and enable the versioned prompt explicitly:

```bash
uv run python -m evaluation.memory.locomo run \
  --env-file .env \
  --run-id locomo-inference-aware \
  --top-k 30 \
  --answer-k 10 \
  --answer-source-content \
  --answer-inference-aware \
  --categories 3
```

The inference-aware option does not change extraction, retrieval, or the Judge policy. Use a separate output directory
and an independent `rejudge` run when comparing it with a baseline.

To preserve direct-answer behavior while giving abstentions one inference attempt, enable the exact-Unknown fallback:

```bash
uv run python -m evaluation.memory.locomo run \
  --env-file .env \
  --run-id locomo-unknown-fallback-inference \
  --top-k 30 \
  --answer-k 10 \
  --answer-source-content \
  --answer-unknown-fallback-inference \
  --categories 1,2,3,4
```

The fallback reuses the same retrieved Memory and Source context; it does not search again and never sees the gold
answer or dataset category. Each observation records whether the direct answer triggered the fallback, the initial
answer, the fallback instruction identity, and any extra latency, usage, and retries. Trigger rate, resolved-Unknown
rate, fallback-answer accuracy, and model usage are aggregated in `summary.json`.

Run an isolated extraction-profile A/B with distinct database scopes and output directories while keeping every
other setting fixed:

```bash
uv run python -m evaluation.memory.locomo run \
  --env-file .env \
  --run-id locomo-ab-coding \
  --memory-extraction-profile coding \
  --top-k 30

uv run python -m evaluation.memory.locomo run \
  --env-file .env \
  --run-id locomo-ab-conversation \
  --memory-extraction-profile conversation \
  --top-k 30
```

The write path is resumable. Sources use stable IDs and PowerContext capture is idempotent; extraction resumes from
the persisted Source cursor. Per-question results are appended to `observations.jsonl`; a repeated command skips
successful questions and retries errors. Pass `--keep-errors` only when you deliberately want existing errors to
remain zero-scored without retrying.

Evaluation requires the registered Scope mapping in `ingestion.json` and the original database state. Missing
ingestion or deleted Scopes fail explicitly. Historical runs that used logical names directly as Scope IDs require
a new run ID and output directory; they are not reassigned to new Scopes during resume.

Each inference operation retries timeout and temporary-provider failures up to three attempts by default. The runner
does not retry database, schema, invalid-output, or consistency failures. Change the bound with
`--operation-retries`, using a new run identity because it is part of `run.json`.

Useful stage controls:

```bash
# Reuse an already-ingested database namespace.
uv run python -m evaluation.memory.locomo run --env-file .env --run-id locomo-full --skip-ingestion

# Ingest without spending answer/judge requests yet.
uv run python -m evaluation.memory.locomo run --env-file .env --run-id locomo-full --skip-evaluation
```

Do not change dataset selection, candidate K, answer K, rerank policy, model profile, or limits while reusing an
output directory. `run.json` rejects such drift before stateful work begins.

## Outputs

Each run writes under `evaluation/memory/locomo/results/<run-id>/`:

- `run.json`: immutable dataset, selection, metric, and non-secret deployment identity.
- `ingestion.json`: session progress, extracted Memory counts, no-change flushes, and extraction latency.
- `observations.jsonl`: exact per-question retrieval, answer, judge, latency, and usage observations.
- `summary.json`: overall and per-category aggregates.
- `summary.md`: compact human-readable result and interpretation boundaries.

The `results/` directory is ignored because raw outputs can be large and deployment-specific. Publish a reviewed,
non-secret result separately when it should become repository evidence.

Reviewed non-secret comparison reports should be published as separate artifacts with the
`run.json` identity (dataset hash, extraction profile, top-k, judge profile, and model names).
This repository does not currently include a reviewed coding-versus-conversation A/B report.

## Jev Memory comparison

`--rerank-mode jev` injects `JevMemoryReranker` into the Atomic Memory Runtime.
It scores each authorized candidate independently with a native System One Noul
question, then selects original ranks by decreasing probability, preserving coarse
order for ties. It does not use the LLM listwise generation protocol. Configure
dedicated credentials and an explicit model version in the run's local env file:

```dotenv
JEV_API_KEY=your-dedicated-key
JEV_MODEL=jev-1.13.0
JEV_ENDPOINT=https://api.typesafe.ai/v1/systemone
```

The adapter has a 30-second total deadline, a shared concurrency bound of four,
and a 32 KiB UTF-8 limit per request. The deadline includes connection-pool waits
and any transport retries. The adapter adds no retries; the ordinary HTTPX transport
also defaults to zero retries. A timeout, provider rejection, invalid score, missing
usage receipt, oversized request, or returned model-version mismatch fails the
operation under RFC 0080's inference error contract. Caller cancellation propagates,
and outstanding requests are cancelled and awaited. A failed benchmark observation
keeps its total billed cost unknown; it cannot be treated as a successful coarse result.

Runtime authorization, exact Artifact revisions, lifecycle eligibility, and final
PreparedContext budgets still apply. Injected rerankers suppress recall expansion
unless `recall_gate_allow_with_rerank` is explicitly enabled. The adapter does not
log candidate text. Benchmark observations intentionally contain the inspected and
delivered evidence and belong in the ignored local results directory.

Compare three runs against **one ingestion** and the same registered Scope mappings.
Pin the candidate count, answer count, UTF-8 budget, downstream model, question set,
and question policies before evaluation. `--answer-max-bytes` fits the complete
serialized answer payload, including metadata and any expanded Source text, by
dropping the lowest-ranked memories. Observations record the exact delivered hits
and bytes. The fixed quality-regression tolerance is zero and is saved before inference.

First ingest once with `--skip-evaluation`; use the same arguments for each evaluation:

```bash
uv run python -m evaluation.memory.locomo run \
  --env-file .env --run-id jev-comparison \
  --output-directory evaluation/memory/locomo/results/jev-comparison/none \
  --top-k 30 --answer-k 5 --answer-max-bytes 4096 --skip-evaluation
```

Copy that run's `ingestion.json` to the `llm` and `jev` output directories.
Run each arm with `--skip-ingestion`, its own output directory, and respectively
`--rerank-mode none`, `--rerank-mode llm`, and `--rerank-mode jev`. Keep the original
run ID and all selection/model/budget arguments. Retain the three `run.json` files.
The comparator rejects changed query sets, exact ordered candidate pools, budgets,
deployment settings, or predeclared quality tolerance.
The downstream identity fingerprints the endpoint resolved by the model provider,
including environment variables such as `OPENAI_BASE_URL` and the provider's own
defaults. The independent LLM reranker resolves its own provider endpoint in the
same way, including `ANTHROPIC_BASE_URL`. A changed reranker endpoint invalidates
resuming its run. A changed downstream endpoint also invalidates comparing runs;
explicitly setting the same resolved default does not change the identity.

Comparison answer, judge, and LLM rerank models disable native SDK retries.
The runner owns transient retries and records them explicitly; token receipts
are required for every model response, including structured-output retries.
Missing receipts fail the observation and leave cost unknown. Manifests record
this billing policy so older runs without observable provider billing cannot
establish complete costs or recommend adoption.

```bash
uv run python -m evaluation.memory.locomo compare-rerank \
  --none evaluation/memory/locomo/results/jev-comparison/none \
  --llm evaluation/memory/locomo/results/jev-comparison/llm \
  --jev evaluation/memory/locomo/results/jev-comparison/jev \
  --llm-input-price "$LLM_INPUT_PRICE" --llm-output-price "$LLM_OUTPUT_PRICE" \
  --jev-input-price "$JEV_INPUT_PRICE" --jev-output-price "$JEV_OUTPUT_PRICE" \
  --generation-input-price "$GENERATION_INPUT_PRICE" \
  --generation-output-price "$GENERATION_OUTPUT_PRICE" \
  --output evaluation/memory/locomo/results/jev-comparison/comparison.json
```

Supply actual USD-per-million-token rates. If retrieval used embeddings, provide
the measured total retrieval costs for the three arms with
`--retrieval-cost-usd NONE LLM JEV`. Without those receipts, or when retries/errors
leave billing unknown, total evaluation cost stays `null`. Shared ingestion cost
is excluded and must be reported separately when reporting the complete ingest-to-answer
pipeline. The report includes evidence recall/MRR, downstream correctness, delivered
bytes, request/token counts, fallbacks/retries, and p50/p95 latency. Adoption requires
complete cost evidence, no Jev fallbacks, no accuracy regression against either
baseline, a lower evaluation cost than LLM reranking, and no p95 latency regression.
The comparator does not establish held-out independence or statistical significance.

`dataset/jev_rerank_cases.json` is a small runnable LoCoMo-format smoke dataset with
English/Chinese queries, lexical distractors, complementary evidence, negation,
date/version conditions, no-match questions, and an adversarial passage. Pass it
with `--dataset` to exercise the pipeline. It is public smoke input, not an independent
held-out quality evaluation. Calibrate the question policy on a separate dataset and
freeze it before running a held-out comparison. A dedicated reranker baseline is
not supplied by this experiment; report its absence or evaluate one separately using
the same candidate pools.

See [Jev evaluation evidence](JEV_EVALUATION.md) for execution coverage and the
adoption recommendation.
