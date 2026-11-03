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

Recommendation: **revise**. The measured service path has explicit failures and
higher successful-observation latency; complete account billing remains unavailable.

The [hosted execution records on PR #2005](https://github.com/oceanbase/powercontext/pull/2005)
retain the reviewed, non-secret endpoint, pinned version, input and adapter hashes,
per-case selections, latency, token receipts, and diagnostic failures. They include
successful serial and concurrent adapter requests and earlier HTTP 402 responses
whose cause remains undetermined. A provider rejection alone does not establish
that an account has no balance.

These are adapter connectivity and protocol checks over public examples. They do
not exercise ingestion, Runtime search, answer generation, or judging. Failed or
cancelled requests have unknown billing. Keep one-off raw records with the PR or CI
artifacts; the reusable bilingual and adversarial inputs remain in
`dataset/rerank_smoke_cases.json`.

On 2026-10-17 (Asia/Shanghai), an additional 12 predeclared bilingual/adversarial
questions ran through native Source capture, extraction/reconciliation, SQLite
hybrid search, reranking, answering, and judging. They used one frozen ingestion
of 12 sessions and the existing local DashScope Qwen/embedding configuration;
the Jev arm used the user-provided `jevmodel.org` endpoint with `jev-1.13.0`.
These measurements describe that service path, without independently verifying
upstream execution or establishing direct TypeSafe API performance.

| Arm | Completed | Judge correct / attempted | Search + rerank + answer p50 | p95 |
| --- | ---: | ---: | ---: | ---: |
| none | 12/12 | 12/12 | 1.20 s | 1.50 s |
| llm | 12/12 | 12/12 | 1.98 s | 2.38 s |
| jev | 7/12 | 7/12 | 8.57 s | 13.24 s |

Five Jev questions failed with actual HTTP 402 `insufficient_credits` responses.
The same arm recorded 16 successful HTTP requests. This does not establish a zero
account balance. Failed questions remain zero in the quality denominator; their
answers were not replaced with diagnostic retries. Latency quantiles cover
successful observations and exclude the evaluation judge. Seven jointly successful
questions have identical ordered candidate digests. Candidate equality is not
claimed for failed questions without a successful trace.

The run fixed candidate limit 10, answer limit 3, complete serialized answer budget
4096 UTF-8 bytes, temperature 0, concurrency 1, one operation attempt, and quality
regression tolerance 0. The separately versioned `strict-abstention` judge accepts
an explicit abstention for gold `Unknown`; all arms correctly abstained on both
unanswerable questions. The same configured Qwen model answers and judges, so these
are model grades, not independent human judgments.

On the same seven successful questions, rerank + answer token receipts total
7579 for LLM and 10463 for Jev, approximately 38% more for Jev. The Qwen answer
phase alone used 4194 versus 4676 tokens. Cross-provider token sums are not monetary
costs. Failed/cancelled requests and actual account prices leave USD costs unknown.
Two LLM observations used validated-empty-selection coarse fallback; Jev used
no fallback. Provider failures still propagate under RFC 0080.

These are small, agent-authored fixtures frozen before answering, not an external
independent benchmark. Candidate counts range from 1 to 9, with both baselines at
the score ceiling. Session-level provenance recall/MRR does not establish
sentence-level entailment. There is no basis for changing the default provider
or claiming a quality, latency, or cost improvement.

The separate preselected 12-question public LoCoMo pilot has no answer evaluation.
Native ingestion reached 7/19 sessions. Two attempts timed out under the existing
30-second model deadline; a further attempt was deliberately stopped after the
completed hosted comparison exposed repeated provider rejections. Partial ingestion
is retained as a checkpoint, not acceptance evidence. Input-dependent identity
validation now gives the model bounded correction feedback before publication;
the published-identity invariant and request/time limits are preserved.

The runnable comparison in [README.md](README.md#reranker-comparison) fixes the
inputs and delivery budget and rejects candidate/model/budget drift. The quality
regression tolerance is `0.0`, recorded before evaluation. A report with missing
cost receipts cannot recommend adoption. Comparison manifests pin both resolved
generation and independent reranker endpoints and record a no-SDK-retries,
required-receipts billing policy. Older runs without that policy cannot establish
complete cost. The bilingual/adversarial smoke cases
are separate from any future calibration and held-out evaluation.

Retain reviewed non-secret manifests and comparison results, complete the public
LoCoMo pilot once the service path is usable, and evaluate a dedicated reranker
baseline if available before closing the issue's empirical acceptance criteria.
The new input hash is
`be736cdb4c9691dfd7e482144d216ecc448fa3fa433990254c57eb4b0586b762`.
One-off local records remain outside tracked source pending user review. Include the shared
ingestion cost when reporting total pipeline cost; the comparator reports the
search-to-answer evaluation portion. Comparison schema v3 separates product
cost/latency from judging so an inexpensive or fast judge cannot justify adoption.
