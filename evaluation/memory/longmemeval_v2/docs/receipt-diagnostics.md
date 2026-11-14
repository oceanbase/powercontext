# PreparedContext Receipt diagnostics

Receipt diagnostics consume the Receipt from the same public `/v1/context/prepare`
response that supplies the Reader's context. They are an opt-in profile of the
LongMemEval-V2 evaluation package ([issue #2436](https://github.com/oceanbase/powercontext/issues/2436)).
They leave the Server, production retrieval policy, Reader and Judge prompts unchanged.

## Configuration and Server identity

The Python runners `run_retrieval_smoke` and `run_smoke` accept
`receipt_diagnostics: bool = False` and `receipt_server_revision: str | None = None`.
The harness adapter accepts the same fields in `memory_params`. Boolean values are
strict: JSON `false` is valid; the string `"false"`, integers and null are rejected.
When enabled, `arm.retrieval_strategy` must be `prepared-context` (adapter
`query_strategy: prepared-context`), and the revision must match `[0-9a-f]{40}` exactly.
The revision is not required or used while disabled.

The operator must actually deploy the named source revision and verify that build
supports `include_receipt: true`. Supplying a SHA attests to that prerequisite; it
does not verify deployment identity. Existing readiness and PreparedContext capability
checks remain in place, but `/v1/capabilities.context_versions` does not attest to
Receipt support. There is no new capability, version endpoint, compatibility probe,
or automatic negotiation.

Both CLIs accept the same options, appended to their usual required arguments:

```bash
uv run --project evaluation longmemeval-v2 retrieval-smoke \
  ... \
  --experiment-arm query-time-compact-v1 \
  --receipt-diagnostics \
  --receipt-server-revision "$RECEIPT_SERVER_REVISION" \
  --output-dir /path/to/new-receipt-retrieval

uv run --project evaluation longmemeval-v2 run-smoke \
  ... \
  --experiment-arm query-time-compact-v1 \
  --receipt-diagnostics \
  --receipt-server-revision "$RECEIPT_SERVER_REVISION" \
  --skip-reader \
  --output-dir /path/to/new-receipt-run
```

Set `RECEIPT_SERVER_REVISION` to the verified deployment's complete lowercase SHA;
it is distinct from the local harness's `--powercontext-revision` and
`--integration-revision`. Configuration validation runs before runtime HTTP calls,
stage execution or output creation. It also applies to `--skip-reader` runs.

The retrieval manifest and the enclosing `run-manifest.json` retain this local block
only when enabled:

```json
{
  "diagnostics": {
    "receipt_diagnostics": true,
    "receipt_server_revision": "0123456789abcdef0123456789abcdef01234567",
    "server_revision_basis": "operator_attested"
  }
}
```

That SHA is synthetic and is not evidence of an actual Server build. The revision
and attestation basis never enter the prepare HTTP payload. A harness configuration
for direct adapter use has these additional `memory_params` fields:

```json
{
  "query_strategy": "prepared-context",
  "receipt_diagnostics": true,
  "receipt_server_revision": "0123456789abcdef0123456789abcdef01234567"
}
```

Direct harness users must preserve the operator attestation in their own local run
manifest as well. No extra model credential is needed for retrieval-only diagnostics.

## Requests, versions and status boundaries

| Mode | Prepare request | Audit schema | Context items |
| --- | --- | --- | --- |
| Default / disabled | Original fields, no `include_receipt` | `powercontext.longmemeval-v2-memory-audit.v1` | Original text items |
| Enabled | Same call plus `include_receipt: true` | `powercontext.longmemeval-v2-memory-audit.v2` | Original text items |

Neither mode implicitly enables `include_reuse`. Each query still makes exactly one
prepare call. No search/get/second prepare reconstructs a Receipt. HTTP 422 from an
incompatible Server, network errors and prepare failures follow the original request
failure path. There is no automatic retry that removes `include_receipt`; such a
failure is never rewritten as empty context or a missing Receipt.

Audit v2 preserves existing ingest/query fields. Only query events add
`prepared_context_diagnostics`, linked to the result and timing by the existing
`query_invocation_id`. The query metadata hook keeps its baseline shape; diagnostics
do not enter retrieval results, Reader context items or prompts. Each adapter owns its
audit version. Opening a run rejects a different existing audit version before writing;
use a fresh run directory. Report readers support v1/v2 and reject unknown or mixed
schemas. A v1 report says `not_collected`, with null diagnostic totals rather than zero.

The existing PreparedContext schema, status, UTF-8 byte count and budget checks run
first. An otherwise valid prepare and its content remain usable when Receipt
diagnostics are incomplete:

| Receipt state | Diagnostic status | Completeness and attribution |
| --- | --- | --- |
| Valid supported Receipt | `available` | `complete`; public facts can be aggregated |
| Missing or null Receipt | `unavailable` | `incomplete`; cause unknown, including size suppression versus build failure |
| Unsupported/malformed Receipt, mismatched integrity or local size limit | `invalid` | `incomplete`; no trusted attribution |
| HTTP or original prepare validation failure | Failed query event | Request failure, never an unavailable/empty success |

Local `validation_failure` values include `invalid_receipt`, `unsupported_schema`,
`used_bytes_mismatch`, `budget_mismatch`, `content_digest_mismatch`,
`truncation_mismatch`, `identity_mismatch`, `evidence_mismatch` and `local_size_limit`.
An available Receipt must have valid bounded lists, strict types and nonnegative or
positive counts as appropriate. Its `used_bytes` equals same-response `content_bytes`,
its requested budget equals the request, and nonempty content has the exact UTF-8
SHA-256 with the `sha256:` prefix. Empty context uses the public null digest. Selected
identity shapes and top-level versus selected truncation must agree. Failed requests
remain separately counted by the report.

## Bounded projection and interpretation

The diagnostic contract is `powercontext.longmemeval-v2-prepared-context-diagnostics.v1`;
the public Receipt schema and policy are `powercontext.prepared-context-receipt.v1`.
The explicit allowlist retains schema/status/validation/completeness, Receipt/policy
identity, content digest and byte budgets, ordered selected identities with rendered
bytes and truncation, omitted groups, public rounds/searches/reranks, config/prompt
digests, nondeterminism and stage timings. It excludes response/content bodies, query
text, raw rerank `effective_settings`, prompt payloads, code paths and unknown fields.
Unknown public fields are ignored through Pydantic's default behavior and never copied
by the explicit projection.

The serialized projection is bounded to **8,192 UTF-8 bytes**, independent of the
PreparedContext content budget. Exceeding it produces `invalid` / `local_size_limit`;
the adapter does not truncate a partial object and label it available.

`candidate_count` always remains `null` with `candidate_count_basis: not_exposed`.
The public HTTP response does not expose `RecallEffort.candidates_by_round`. No sink,
daily table, private runtime object or added API is consulted. In particular,
`selected_count: 0` and `not_retrieved` do not establish zero original candidates.

| Dimension | Interpretation and count unit |
| --- | --- |
| `selected` / selected truncation | Selected items, with exact local identity and byte contribution |
| `omitted` | Items grouped independently by family/reason; `not_retrieved` alone does not establish a disabled family or zero candidates |
| `below_min_bytes`, `no_fitting_truncation`, `family_limit`, `entry_limit` | Budget/quota omission item counts |
| `rerank_not_selected` | Pre-selection omission items, not Reader/model failures |
| Search `fallback_reason` | Executions of the public search path; timeout/unavailable/reused fallback remain distinct |
| Rerank groups | Executions grouped by round/outcome/fallback |
| `expansion_failed` | Recall rounds; an expansion failure can coexist with other signals |
| Stage timing | Milliseconds plus execution count |

These dimensions overlap. Do not make one exclusive failure class or add execution
counts to omission item counts. Only available Receipts contribute trusted attribution;
unavailable/invalid counts and request failures remain visible independently.

Exact Scope/Artifact addresses and existing query hashes belong to controlled local
audit artifacts. Shareable diagnostic summaries add only grouped public dimensions
and totals; they omit exact identities, query hashes, query text and content. A hash
is not anonymization. Review other existing report/run identifiers under the usual
sharing rules. Prepared context, actual send, host injection, model citation, model
use, answer correctness and material usefulness are separate facts. A Receipt proves
none of the downstream facts.

## Synthetic examples and offline verification

The fixed examples contain synthetic identities and no real user content:

- [Local manifest and fixture provenance](examples/receipt-diagnostics-v2.manifest.json)
  labels the source/config/build identities synthetic. The setup's Source reference
  is provenance for this example, not a claim of native Memory lineage.
- [One v2 query event](examples/receipt-diagnostics-v2.query.jsonl) includes an ordered
  selected address, truncation, budget/rerank omissions, search fallback and an
  expansion failure. The digest represents 160 synthetic ASCII `x` bytes; the body
  is absent from the audit.
- [Shareable diagnostic aggregate](examples/receipt-diagnostics-v2.aggregate.json)
  is the `prepared_context_diagnostics` report block for that event. It preserves
  units and unknown candidate count and contains no exact source/Artifact identity.

Default tests use offline synthetic runtimes and stage/model doubles, with no remote
Server or model calls. Run the focused harness checks and lint:

```bash
uv run --project evaluation pytest \
  evaluation/memory/longmemeval_v2/tests/test_longmemeval_v2_retrieval_smoke.py \
  evaluation/memory/longmemeval_v2/tests/test_longmemeval_v2_run_smoke.py \
  evaluation/memory/longmemeval_v2/tests/test_longmemeval_v2_cli.py -q
uv run --project evaluation ruff check evaluation/memory/longmemeval_v2
uv run --project evaluation ruff format --check evaluation/memory/longmemeval_v2
```

The real TCP HTTP/SQLite acceptance uses the root Server environment, starts a
disposable Server and makes no model calls. It is skipped unless explicitly enabled.
From the repository root, run:

```bash
POWERCONTEXT_TEST_RECEIPT_HTTP=1 \
PYTHONPATH=evaluation/memory/longmemeval_v2/src \
uv run pytest evaluation/memory/longmemeval_v2/tests/test_longmemeval_v2_receipt_http.py -q
```

The acceptance's operator-attested source baseline is
`923e3d4cec8038d0799f6893b0ec8e746c870434`. That identifies the verified source
baseline, not Server self-attestation or a remote deployment check. This real
same-response check must be reported separately from offline synthetic tests.
Mock responses establish local handling only. A simulated `HTTPError(422)` fixture verifies
the old-Server failure path without claiming execution against a historical Server build.
