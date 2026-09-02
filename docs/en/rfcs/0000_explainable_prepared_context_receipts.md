- Proposal Name: `explainable_prepared_context_receipts`
- Start Date: 2026-09-02
- RFC PR: [oceanbase/powercontext#0000](https://github.com/oceanbase/powercontext/pull/0000)
- Tracking Issue: [oceanbase/powercontext#1356](https://github.com/oceanbase/powercontext/issues/1356)
- Related RFCs: [RFC 0014](0014_memory_layer_design.md), [RFC 0028](0028_context_pack.md),
  [RFC 0046](0046_observability_foundations.md), and [RFC 0080](0080_memory_search_reranking.md)

# Summary

This RFC adds an optional, bounded PreparedContext Receipt to request-time recall. The existing
`powercontext.prepared-context.v1` injection value stays `status`, `content`, and `content_bytes`. Callers that opt in
receive a companion Receipt that identifies what the Runtime selected, what it omitted, which retrieval path it used,
and which byte budget it consumed, without retaining query text or Memory/Experience bodies.

The first policy is `powercontext.prepared-context-receipt.v1`. Receipts are ephemeral diagnostics attached to one
`prepare` response. They are not Artifacts, have no Revision, are not persisted by default, and are not a second
authority for facts. OpenTelemetry spans remain the timing and outcome signal. A Receipt is the exact-selection
contract for one injected byte string.

# Motivation

`POST /v1/context/prepare` already owns selection, citation, rendering, and the UTF-8 budget. The Runtime keeps exact
origins on `PreparedContextBuild` and records search/build stage attributes on traces, then discards both at the public
boundary. Integrations and operators therefore see only an opaque injection string.

That gap blocks three product uses:

- An operator cannot tell whether an Agent received a decision, hit the budget, or received an empty result for a
  different reason.
- Evaluation cannot score selected exact references, omission classes, or retrieval fallback independently from the
  injected text.
- Adjacent work such as multi-resolution packing can compare policies only after a stable, content-free selection
  record exists.

Spans answer "which stage ran and how long it took". They do not identify the exact Memory entry versions or Experience
revisions that were rendered, and they must not grow into a public selection schema. Memory search's in-process rerank
trace is also the wrong surface: it describes one search, not the interleaved, budgeted PreparedContext that the host
injects.

Without a Receipt, each host or benchmark will invent its own explanation of recall. That explanation will either leak
content or disagree with what was actually injected.

# Guide-level explanation

## Ask for a Receipt

The default `prepare` request is unchanged:

```python
prepared = await client.prepare_context(
    PrepareContextRequest(scope_id="project:payments", query="Why did we choose SQLite?", max_bytes=8000)
)
```

The response remains `powercontext.prepared-context.v1`. Hosts that inject `content` keep their current parsing.

A caller that needs to explain the same result sets `include_receipt` to true:

```python
prepared = await client.prepare_context(
    PrepareContextRequest(
        scope_id="project:payments",
        query="Why did we choose SQLite?",
        max_bytes=8000,
        include_receipt=True,
    )
)
```

When the Runtime produces a ready context, the response still contains the injection string and adds a Receipt. The
Receipt names the exact selected citations, groups omitted candidates by a closed reason enum, records the retrieval
mode actually used, and hashes the injected bytes. It never repeats the query or the selected bodies.

Empty results also receive a Receipt when requested. An empty Receipt still reports the query digest, budget, retrieval
path, and omission counts so "no Memory" is distinguishable from "everything was over budget".

Automatic host recall must not set `include_receipt`. Official Pi, DSH, OpenCode, Codex, Claude Code, and WorkBuddy
validators currently require the injection object to contain exactly `schema`, `status`, `content`, and `content_bytes`.
A default or host-recall Receipt would be rejected as an invalid response and would fail open without injecting
context. Operators, evaluation harnesses, and a later CLI request Receipts on a separate prepare call, or through an
updated validator that opts into the extra field.

## Treat a Receipt as untrusted metadata

A Receipt proves that PowerContext rendered those exact references under that policy and budget. It does not prove that
the historical content is currently true, and it does not outrank system, developer, repository, or current-user
instructions. Hosts must not inject Receipt JSON into the model prompt. The injection value remains `content`.

PreparedContext Receipts are not Handoff Receipts. A Handoff Receipt is a Work Continuity acknowledgement of an exact
Handoff Revision. A PreparedContext Receipt explains one ephemeral recall.

## Inspect without a new content API

The compact Receipt is the first disclosure level. Progressive inspection reuses existing exact-read operations:

1. Receipt: selected refs, omission counts, retrieval path, digest, budget.
2. Exact Memory or Experience read by the cited identity.
3. Exact Source evidence already attached to that Artifact, when the caller is authorized to read it.

The Receipt does not cache item bodies for later expansion. If the caller needs the text, it loads the current exact
identity through the ordinary Memory and Experience APIs. If that identity has since been retired, the exact-read
failure is the explanation; the Receipt is not a time-travel store.

## Failure stays fail-open

Receipt assembly must not change or block injection. If `include_receipt` is true and Receipt construction fails, the
Server still returns the PreparedContext that would have been returned without the flag, omits `receipt`, and records a
content-free diagnostic. Integrations that ignore the new field keep working.

# Reference-level explanation

## Public request

`PrepareContextRequest` gains one optional field:

| Field | Default | Contract |
| --- | ---: | --- |
| `include_receipt` | `false` | When true, the Runtime attempts to attach `powercontext.prepared-context-receipt.v1` to this response. |

Omitted, null, and false are equivalent. Existing clients send neither the field nor a Receipt parser. v1 does not
offer a Server deployment default that turns Receipts on for every prepare.

`query`, `scope_id`, and `max_bytes` keep their current bounds. `query_digest` hashes the same normalized query Memory
search already uses (`normalize_text`: NFC Unicode of the trimmed query, UTF-8). The Receipt stores only `sha256:<hex>`.

## Public response

`PreparedContext` keeps `schema`, `status`, `content`, and `content_bytes`. It gains:

| Field | Presence | Contract |
| --- | --- | --- |
| `receipt` | omitted unless requested and successfully built | `PreparedContextReceipt` |

When `include_receipt` is false, null, or omitted, the JSON object must not contain a `receipt` key. A `null` value is
not equivalent to omission. Official host validators and OpenAPI `additionalProperties: false` reject unknown keys,
including `"receipt": null`.

The injection schema name remains `powercontext.prepared-context.v1`. Default prepare responses therefore stay a
four-field object. Callers that set `include_receipt` true must parse the optional `receipt` field; official generated
clients are regenerated in the implementation PR. Host recall plugins keep their exact four-field validators until they
explicitly opt in.

## Receipt schema

Policy ID: `powercontext.prepared-context-receipt.v1`.

```text
PreparedContextReceipt
  schema: powercontext.prepared-context-receipt.v1
  receipt_id: opaque UUID
  policy_id: powercontext.prepared-context-receipt.v1
  query_digest: sha256 hex of normalized query
  content_digest: sha256 hex of injected UTF-8 content, or null when status=empty
  requested_max_bytes: integer
  used_bytes: integer, equal to content_bytes
  truncated: true when any selected item was size-truncated
  retrieval:
    memory_mode: auto | fts | vector | hybrid | none
    rerank_policy_id: string | null
    rerank_fallback: boolean
    experience_configured: boolean
  selected: [SelectedItem]  # schema max 16; current Builder emits at most 8
  omitted: [OmittedGroup]   # max 16 groups
  stages: [StageTiming]     # memory.search, experience.search, context.build
```

`receipt_id` correlates this Receipt with the HTTP `X-PowerContext-Request-ID` in logs. It is not an Artifact ID and
must not be used as a durable fetch key in v1.

`SelectedItem`:

| Field | Contract |
| --- | --- |
| `kind` | `memory` or `experience` |
| `memory_citation` or `artifact_ref` | Exact identity already admitted by the Builder |
| `rendered_bytes` | UTF-8 size of that item's rendered fragment, not the source body |
| `truncated` | Whether the Builder truncated that item to fit |

Selected items are listed in injection order. The set of selected identities must equal `PreparedContextBuild.origins`.
A Receipt whose selected refs do not match the injected origins is invalid and must not be returned; the Server then
follows the Receipt-assembly failure path.

The OpenAPI array cap is 16 so a later Builder change does not require a schema bump. The current Coding Agent Builder
admits at most eight Memory items and two Experience items, interleaves Memory first, and caps the injected list at
eight. A v1 Receipt lists exactly that injected list, never a superset.

`OmittedGroup`:

| Field | Contract |
| --- | --- |
| `reason` | Closed enum below |
| `count` | Distinct dropped identities, except empty-set flags which always use `1` |

Closed `reason` values:

| Reason | Meaning |
| --- | --- |
| `duplicate` | Same Memory entry version or Experience revision already admitted |
| `blank` | Empty identity or empty renderable text |
| `family_limit` | Exceeded the Builder's Memory or Experience admission cap |
| `entry_limit` | Exceeded the combined injection item cap |
| `over_budget` | Could not fit even at the minimum truncated size |
| `rerank_not_selected` | Present in the coarse Memory pool and dropped by listwise rerank before the Builder |
| `memory_not_retrieved` | No Memory head, or Memory search returned zero hits |
| `experience_not_retrieved` | Experience recall is not configured, or it returned zero hits |

`memory_not_retrieved` and `experience_not_retrieved` are empty-set flags, not scans of the Artifact. Each appears at
most once, with `count` equal to `1`. They are omitted when that source produced a non-empty candidate list, even if
every candidate is later dropped for another reason. Other reasons count distinct candidate identities in the pool that
reached the Builder or were excluded immediately before admission. Omission counts never try to count the entire Memory
Artifact.

`StageTiming` records milliseconds for `memory.search`, `experience.search`, and `context.build`. Missing stages are
omitted. Stage names match the existing Runtime span names so a Receipt can be correlated with a trace without copying
span payloads.

Rerank: when Memory search produced a `MemoryRerankTrace`, the Receipt copies `policy_id` and `used_fallback` only. It
does not copy candidate hits, selected ranks, usage, or any Memory text from that trace.

## Bounds

| Limit | Value |
| ---: | ---: |
| `selected` | 16 items in the schema; current Builder output is at most 8 |
| `omitted` groups | 16 |
| `stages` | 8 |
| `receipt` JSON UTF-8 size | 8192 bytes |
| item bodies, query text, prompts, vectors, secrets, tokens, absolute paths | forbidden |

If a valid Receipt would exceed 8192 bytes, the Server drops the Receipt rather than truncating selected identities.
A truncated identity list would disagree with the injected bytes.

## Persistence and MCP

v1 does not persist Receipts and does not add a fetch-by-`receipt_id` operation. Evaluation that needs a durable record
stores the response itself in the evaluation harness. A later opt-in store with TTL requires its own RFC.

`prepare_context` remains absent from the default MCP tool surface. Receipts are not a reason to project prepare as an
Agent-facing tool.

## CLI

The Client SDK exposes `include_receipt` on the existing prepare operation. A later CLI command such as
`powercontext context prepare --include-receipt` may print the Receipt as JSON. That command is implementation work
after this RFC; it must not inject Receipts into Agent prompts.

## Compatibility

| Surface | Change |
| --- | --- |
| Default `prepare` | Same four-field JSON object; no `receipt` key |
| OpenAPI `PrepareContextRequest` | Optional `include_receipt` |
| OpenAPI `PreparedContext` | Optional `receipt`, present only when requested and successfully built |
| SQLite / OceanBase | No schema change in v1 |
| Host recall plugins | No required change while they omit `include_receipt` |
| Host validators | Exact four-field checks remain valid on the default path |
| Tracing | No new required span; existing stage names are reused in `stages` |

Generated Python, DSH, Pi, and OpenCode operation tables are regenerated in the implementation PR. Automatic recall
must keep sending the current request shape. A host that wants to log Receipts updates its validator in the same
change that sets `include_receipt`.

## Implementation sketch

1. Extend `PrepareContextRequest` and `PreparedContext` in OpenAPI, then regenerate bindings.
2. Keep `PreparedContextBuilder.build_result()` as the origin of selected items. Count omitted identities while
   walking the same candidate lists the Builder already walks.
3. Copy retrieval mode and rerank summary from the Memory search result already produced in
   `ScopedContextApplication._prepare`.
4. Hash the Memory-search-normalized query and the injected `content` after the Builder returns.
5. If Receipt validation fails, log a content-free error and return the four-field PreparedContext with no `receipt`
   key.
6. Leave official host recall requests unchanged. Update a host validator only in a change that opts that host into
   Receipts.

Focused tests cover empty, ready, truncated, deduplicated, reranked, fallback, `include_receipt=false` (no `receipt`
key), `memory_not_retrieved` / `experience_not_retrieved` flags, and Receipt-assembly failure. Each ready case asserts
`content_digest` matches the returned `content` and selected refs match `origins`. Host recall fixtures keep asserting
exactly four response keys.

# Drawbacks

- Optional fields expand the OpenAPI models and every generated client even when most hosts never request a Receipt.
- Omission counts are summaries of the admitted candidate pool, not of the entire Memory Artifact, so they can be
  misread as "how much of the project was considered".
- Callers might inject Receipt JSON into prompts despite the trust rule.
- `receipt_id` looks like a durable identifier even though v1 cannot fetch it later.

# Rationale and alternatives

**Opt-in field on `prepare`, not a second operation.** A separate `POST /v1/context/explain` would either re-run
selection and disagree with the injected bytes, or require the Server to remember the last prepare. The first is
incorrect; the second is persistence. Attaching the Receipt to the same response keeps one selection, one digest, and
no store.

**Do not put diagnostics on PreparedContext v1 by default.** Every host would download selection metadata on the hot
path. Fail-open injectors would start depending on a larger schema.

**Do not use OTel spans as the public contract.** Spans are sampled, exporter-specific, and must stay content-free.
They cannot carry exact Memory citations as a supported API.

**Do not reuse Memory search's rerank trace.** That trace includes candidate hits and is in-process only. HTTP search
already withholds it. PreparedContext selection happens after Memory search and Experience search and applies a
different budget.

**Do not persist every prepare.** Request-time recall would become an unbounded history of user queries. Evaluation can
keep the HTTP response in the harness.

**Do not invent a progressive-content cache.** Expanding a Receipt item through existing exact-read APIs preserves
Artifact authority. A prepare-side body cache would be a second store of Memory text keyed by an ephemeral id.

Impact of not doing this: hosts and benchmarks will keep reverse-engineering recall from injected text or private
logs, and later packing experiments will have no shared omission vocabulary.

# Prior art

PowerContext already has four related but distinct records:

- `PreparedContextBuild.origins` is the exact selected set, discarded at the HTTP boundary.
- Runtime `memory.search`, `experience.search`, and `context.build` spans record counts and modes, not citations.
- `MemoryRerankTrace` explains listwise Memory search inside the process.
- Handoff Receipts acknowledge an exact Handoff Revision in Work Continuity.

RFC 0028 already requires citations and budgets on the injection path; it does not expose the selection record.
RFC 0046 forbids putting Memory bodies and query text into traces. RFC 0080 keeps rerank diagnostics off the HTTP
search contract.

Outside PowerContext, retrieval systems often return hit IDs and scores with the answer. This RFC returns exact
PowerContext identities and closed omission reasons, and it refuses scores and bodies so the diagnostic cannot become
another prompt.

# Unresolved questions

- Should a later evaluation profile persist Receipts under an explicit TTL, or is harness-side storage enough?
- Does Dashboard inspection belong in the first implementation issue, or only CLI/SDK?

v1 keeps `include_receipt` request-only. A Server default that attaches Receipts to every prepare would break current
host validators and is out of scope.

These remaining questions do not block accepting the v1 contract above. They belong in the implementation issue or a
follow-up RFC.

# Future possibilities

- A Context Inspector UI that renders selected refs and omission counts from a prepare-with-receipt response.
- Evaluation reports that join Receipt omission reasons with task scores.
- Multi-resolution packing ([#1426](https://github.com/oceanbase/powercontext/issues/1426)) reporting selected level
  through the same Receipt `omitted` vocabulary.
- An opt-in durable Receipt store for audits, with query digests only and a short TTL.
- Host-visible, content-free diagnostics that log `receipt_id` and `content_digest` when recall is empty or truncated.
