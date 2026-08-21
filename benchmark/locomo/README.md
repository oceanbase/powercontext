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
- Scope: one isolated PowerContext scope per two-speaker conversation. The run ID creates a separate namespace.
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
uv run python -m benchmark.locomo inspect --env-file .env
```

The checked `.env.example` lists variable names only. Keep real credentials in the repository-root `.env`, which is
already ignored.

## Run

A small real-service smoke run uses the first conversation and five scored questions:

```bash
uv run python -m benchmark.locomo run \
  --env-file .env \
  --run-id locomo-smoke \
  --conversation-limit 1 \
  --question-limit 5
```

The complete reference-shaped run is:

```bash
uv run python -m benchmark.locomo run \
  --env-file .env \
  --run-id locomo-full \
  --top-k 30
```

Retrieve a broad candidate pool, then send a more precise context to the answer model:

```bash
uv run python -m benchmark.locomo run \
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
uv run python -m benchmark.locomo run \
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
uv run python -m benchmark.locomo run \
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
uv run python -m benchmark.locomo run \
  --env-file .env \
  --run-id locomo-ab-coding \
  --memory-extraction-profile coding \
  --top-k 30

uv run python -m benchmark.locomo run \
  --env-file .env \
  --run-id locomo-ab-conversation \
  --memory-extraction-profile conversation \
  --top-k 30
```

The write path is resumable. Sources use stable IDs and PowerContext capture is idempotent; extraction resumes from
the persisted Source cursor. Per-question results are appended to `observations.jsonl`; a repeated command skips
successful questions and retries errors. Pass `--keep-errors` only when you deliberately want existing errors to
remain zero-scored without retrying.

Each inference operation retries timeout and temporary-provider failures up to three attempts by default. The runner
does not retry database, schema, invalid-output, or consistency failures. Change the bound with
`--operation-retries`, using a new run identity because it is part of `run.json`.

Useful stage controls:

```bash
# Reuse an already-ingested database namespace.
uv run python -m benchmark.locomo run --env-file .env --run-id locomo-full --skip-ingestion

# Ingest without spending answer/judge requests yet.
uv run python -m benchmark.locomo run --env-file .env --run-id locomo-full --skip-evaluation
```

Do not change dataset selection, candidate K, answer K, rerank policy, model profile, or limits while reusing an
output directory. `run.json` rejects such drift before stateful work begins.

## Outputs

Each run writes under `benchmark/locomo/results/<run-id>/`:

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
