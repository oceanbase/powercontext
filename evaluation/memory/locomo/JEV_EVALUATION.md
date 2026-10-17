# Jev Memory reranking evidence

Task: #1937, restored from #1643.

The implementation uses the existing `MemoryReranker` injection boundary over
independent Atomic Memory Artifacts. Policy:
`powercontext.memory.rerank.jev.noul.v1`; protocol fixtures use `jev-1.13.0`.
Jev is opt-in and requires a dedicated key and pinned model. The standard
Runtime and LLM reranker retain their defaults.

## Execution coverage

SQLite acceptance uses a real local database and the actual Runtime assembly,
HTTP authorization, exact-revision read path, and PreparedContext builder.
It covers successful scoring, explicit rate-limit and total-deadline failures,
cross-Scope rejection before provider requests, identity preservation, read-only
Memory state, UTF-8 delivery limits, and suppression of automatic recall expansion.

Provider responses in these tests use HTTPX MockTransport. They establish local
protocol handling and RFC 0080's failure behavior, not Jev's semantic accuracy,
availability, network latency, or billed cost. Persisted-candidate evaluation
tests exercise search, answer generation, and judging through the locked native
OpenAI and Anthropic SDKs with simulated HTTP responses. They reject missing or
partial usage receipts and prevent SDK retries from hiding billable attempts in
both the answer/judge path and the independent LLM reranker.

The same acceptance scenarios support an isolated real OceanBase database through
`POWERCONTEXT_TEST_OCEANBASE_URL`. The fixture creates and removes its own database;
the connection must permit those operations. OceanBase scenarios are skipped when
the setting is absent.
The E2E harness's `Recall effort (OceanBase)` job includes all six Jev SQLite and
OceanBase scenarios in its database contract step, rejects skipped cases, and
uploads the JUnit report. Its provider responses still use MockTransport; the
database integration and hosted Jev quality are separate evidence.

On 2026-10-17 (Asia/Shanghai), the database contract step of
[E2E harness run 38928735027](https://github.com/oceanbase/powercontext/actions/runs/38928735027/job/117079194023)
passed on commit `60b6ffcbaa1598586c7baa992c05f8a6817e82c7`: nine cases executed
without skips, comprising three existing OceanBase contracts and all six Jev
integration scenarios across SQLite and real OceanBase. This execution establishes
database integration, including explicit provider-failure propagation. The same
job's broader recall suite passed 307 tests.

```bash
uv run python -m pytest \
  tests/builtin/artifacts/memory/test_jev_reranking.py \
  tests/e2e/test_jev_memory_reranking.py \
  tests/evaluation/test_jev_rerank_comparison.py
```

## Quality and cost decision

Recommendation: **revise pending live comparative evidence**.

On 2026-10-16, the user-configured hosted endpoint
`https://jevmodel.org/v1/systemone` accepted the System One request and resolved
`jev-latest` to `jev-1.13.0`. Subsequent adapter probes pinned `jev-1.13.0`,
used the normal 30-second deadline and four-request concurrency bound, and sent
public fixture session text. The initial eight-case probe completed four cases
and exposed four provider failures. Diagnostic calls returned HTTP 402
`insufficient_credits` for the Chinese complementary-evidence and version-condition
cases. The service message stated that input-token credits were insufficient.
Successful calls supplied token receipts; failed or cancelled requests have
unknown billing. These errors establish that those requests were rejected, not
that the account had no remaining balance.

On 2026-10-17 (Asia/Shanghai), nine requests through the same configured key and
pinned adapter all returned HTTP 200 with valid token receipts. Three
single-candidate requests covered both previously failing Chinese cases and an
English control. The complementary-evidence case then ran with the same three
candidates first serially and then concurrently; both selected original ranks
`[2, 1]`. The final response's `x-tokens-remaining` header reported `84421`.
The nine requests consumed 3702 input tokens and 189 output tokens. This verifies
usable quota at that time. The earlier 402 responses did not recur, and their
cause remains undetermined; neither account exhaustion nor a concurrency defect
has been established.

The non-secret [hosted adapter evidence](evidence/jev_live_smoke_2026-10-16.json)
records the endpoint, pinned version, dataset and executed-adapter hashes,
per-case selections, latency, token receipts, and diagnostic failures. This is an
adapter connectivity and protocol check over public examples. It does not
exercise ingestion, Runtime search, answer generation, or judging.

No live three-arm run of this Noul adapter is recorded here.
Relevant-evidence recall, ranking quality, downstream task success, real p50/p95
pipeline latency, billed pipeline input, retries, and total pipeline cost are
therefore unmeasured.
There is no basis for changing the default provider or claiming a quality, latency,
or cost improvement.

The runnable comparison in [README.md](README.md#jev-memory-comparison) fixes the
inputs and delivery budget and rejects candidate/model/budget drift. The quality
regression tolerance is `0.0`, recorded before evaluation. A report with missing
cost receipts cannot recommend adoption. Comparison manifests pin both resolved
generation and independent reranker endpoints and record a no-SDK-retries,
required-receipts billing policy. Older runs without that policy cannot establish
complete cost. The bilingual/adversarial smoke cases
are separate from any future calibration and held-out evaluation.

Complete a live three-arm Jev comparison with authorized downstream services,
retain reviewed non-secret manifests
and comparison results, and evaluate a dedicated reranker baseline if available
before closing the issue's empirical acceptance criteria. Include the shared
ingestion cost when reporting total pipeline cost; the comparator reports the
search-to-answer evaluation portion.
