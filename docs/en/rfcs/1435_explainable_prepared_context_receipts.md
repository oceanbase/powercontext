---
title: "RFC 1435: Explainable PreparedContext Receipts"
---

- Proposal Name: `explainable_prepared_context_receipts`
- Start Date: 2026-09-02
- RFC PR: [oceanbase/powercontext#1435](https://github.com/oceanbase/powercontext/pull/1435)
- Tracking Issue: [oceanbase/powercontext#1356](https://github.com/oceanbase/powercontext/issues/1356)
- Related RFCs: [RFC 0014](0014_memory_layer_design.md), [RFC 0028](0028_context_pack.md),
  [RFC 0046](0046_observability_foundations.md), and [RFC 0080](0080_memory_search_reranking.md)

# Summary

This RFC adds an optional, bounded PreparedContext Receipt to request-time recall. The existing
`powercontext.prepared-context.v1` injection value stays `status`, `content`, and `content_bytes`. Callers that opt in
receive a companion Receipt that identifies what the Runtime selected, what it omitted, which retrieval path it used,
and which byte budget it consumed, without retaining query text or selected bodies.

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
2. Exact Memory entry, Topic Memory, Experience, or Profile read by the Scope-qualified identity; code read by its
   fingerprint, repository-relative path, file hash, and line range.
3. Exact Source evidence already attached to that Artifact, when the caller is authorized to read it.

The Receipt does not cache item bodies for later expansion. If the caller needs the text, it loads the current exact
identity through the ordinary Artifact and code APIs. If that identity has since been retired, the exact-read
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
  query_digest: sha256 hex of normalized original query
  content_digest: sha256 hex of injected UTF-8 content, or null when status=empty
  requested_max_bytes: integer
  used_bytes: integer, equal to content_bytes
  truncated: true when any selected item was size-truncated
  non_deterministic: true when model-backed rerank or query expansion was invoked
  retrieval:
    rounds: [RecallRound]          # max 3: round 0 and at most 2 expansions
    searches: [SearchGroup]        # max 32 groups across all rounds
    rerank_configs: [RerankConfig] # max 8 distinct configurations
    reranks: [RerankGroup]         # max 32 groups across all rounds
  selected: [SelectedItem]         # max 32, including code evidence
  omitted: [OmittedGroup]           # max 16 groups
  stages: [StageTiming]             # max 8 aggregate stage timings
```

`receipt_id` correlates this Receipt with the HTTP `X-PowerContext-Request-ID` in logs. It is not an Artifact ID and
must not be used as a durable fetch key in v1. `non_deterministic` describes execution, not merely configuration:
model-backed rerank remains non-deterministic at temperature zero; a configured reranker that never ran does not set it.

### Exact selected identities

| `SelectedItem.kind` | Required identity |
| --- | --- |
| `memory` | `memory_entry_address`: Scope-qualified Memory Artifact address, entry ID, entry version ID |
| `topic-memory` | `artifact_address`: Scope ID and exact Topic Memory Artifact ref |
| `experience` | `artifact_address`: Scope ID and exact Experience Artifact ref |
| `profile` | `artifact_address`: Scope ID and exact committed Profile Artifact ref |
| `code` | `code_evidence`: Scope ID, workspace fingerprint, repository-relative path, file SHA-256, inclusive start/end lines, snippet SHA-256 |

An Artifact address contains `scope_id` and `artifact` (`family`, `artifact_id`, `revision`). Every selected item also
has `rendered_bytes` (UTF-8 size of its rendered fragment, excluding separators between items) and `truncated`.
The identity fields are mutually exclusive. Even a current-Scope Memory citation or Artifact ref is expanded to its
Scope-qualified address; the same Artifact or entry ID in two Scopes must remain two different identities.
`memory_entry_address` uses the existing `MemoryEntryAddress` shape (`memory`, `entry_id`, `entry_version_id`), with
`memory` an Artifact address. `code_evidence` uses the existing `CodeEvidenceRef` fields (`scope_id`, `fingerprint`,
`path`, `file_sha256`, `start_line`, `end_line`, `snippet_sha256`); it is not an Artifact ref.

Selected items follow injection order. After normalizing short refs to addresses, their ordered identities must equal
`PreparedContextBuild.origins` followed by `PreparedContextBuild.code_origins`, including the final line range and
snippet hash of any truncated code item. A code-only ready result has empty `origins` and non-empty `code_origins`.
Neither a set comparison nor an `origins`-only comparison satisfies this invariant. Missing, extra, reordered, or
ambiguous identities invalidate the entire Receipt and follow the Receipt-assembly failure path.

The 32-item cap covers the current explicit assembly maximum of 26 historical items (eight Memory, eight Topic Memory,
eight Profile, two Experience), plus up to four code items. The default builder's combined historical entry limit is
eight and it also supports Topic Memory; neither default is a universal selected-item bound. Runtime entry limits and
the byte budget still control actual selection. The Receipt never changes those limits to make its own schema fit.

### Retrieval evidence across Scopes and rounds

`RecallRound` contains `round` (0, 1, or 2), `outcome` (`completed` or `expansion_failed`), and `retained` (whether that
round's candidate pool contributes to the final build). It reuses the execution outcomes already tracked for recall
expansion. If expansion fails and the Runtime returns round-zero candidates, round zero is retained and every
abandoned expansion is marked not retained. An attempted but failed expansion is not reported as a successful search.
Expanded query text and its model response are never included.

`SearchGroup` aggregates calls with the same `round`, `family`, actual `mode`, `outcome`, and `fallback_reason`, and
contains a positive `count`. Families are `memory`, `topic-memory`, `experience`, `profile`, and `code`; modes are
`fts`, `vector`, `hybrid`, `snapshot`, `code`, or `none`. `snapshot` describes a Profile read and `code` the existing code
query operation. `outcome` is `completed`, `not_run`, or `failed`; `none` means no retrieval mode was executed. An
Experience adapter must provide its actual retrieval mode; the Runtime must not infer it from the adapter's presence.
A disabled family has no group; a requested family with no configured reader or no searchable head has a `not_run`
group. A completed zero-hit search is still `completed` with its actual mode. `count` counts actual calls for completed or
failed groups, and skipped retrieval opportunities for not-run groups. Profile and code operations outside the
expansion loop belong to round zero and are counted once per actual invocation.

`fallback_reason` is `none`, `inference_unavailable`, `inference_timeout`, or `reused_fts_fallback`. Choosing FTS normally
under `auto` uses `none`; dropping the vector channel after an inference error uses the observed error class. If a later
round reuses the FTS-only outcome of that failure, it uses `reused_fts_fallback`. Expansion failure belongs in
`RecallRound`, not in this search fallback enum. `auto` is a request policy, never an actual mode in a Receipt.

For example, Memory searches in two authorized Scopes can produce these groups in the same round:

```json
[
  {"round": 0, "family": "memory", "mode": "hybrid", "outcome": "completed", "fallback_reason": "none", "count": 1},
  {"round": 0, "family": "memory", "mode": "fts", "outcome": "completed", "fallback_reason": "inference_timeout", "count": 1}
]
```

Grouping intentionally omits per-search Scope IDs: ContextReferences have no fixed count cap. Selected identities
always retain Scope, while aggregate counts describe all executed calls, including calls in rounds later abandoned.
Do not multiply Topic Memory, Profile, or code calls by the number of referenced Scopes: record the calls that actually
ran. Grouping is deterministic by the tuple of grouping fields, and cannot collapse different modes or fallback causes.

### Rerank evidence

`RerankConfig` has `config_id`, `policy_id`, `model`, `effective_settings`, `timeout_seconds`, `max_requests`,
`config_digest`, `prompt`, and `non_deterministic`. `policy_id` identifies the rerank instruction policy;
it is insufficient to identify the model or configuration. `model` is the credential-free provider/model identity (null for a declared deterministic, non-model reranker).
`effective_settings` contains the non-content model settings actually passed after inheritance, overrides, and
normalization, including explicit defaults such as temperature zero. It is not a dump of deployment configuration.
The implementation must define a versioned allowlist of safe setting names and types, and validate their values;
headers, credentials, URLs, arbitrary provider payloads, and content-bearing settings are excluded.

`config_digest` is SHA-256 over RFC 8785 canonical JSON of a versioned record containing the policy, model, effective
non-content settings, timeout, request limit, and Prompt identity below. Safe execution-affecting settings cannot be
silently omitted from this record. An adapter whose effective configuration cannot be represented completely and safely
must omit the Receipt, with a content-free diagnostic, rather than claim exact evidence using a partial config digest.
This digest is configuration evidence, not a promise that the provider will reproduce identical results.

`prompt` comes from the `ResolvedPrompt` bound to the actual invocation, not the latest Prompt head read afterward.
It contains `scope_id`, `key`, `definition_version`, `builtin_version`, `selection` (`built_in` or `artifact`),
`selected_version`, `compiled_digest`, and `artifact_address` (null for a built-in Prompt; Scope-qualified exact Prompt
Artifact address otherwise). Compiled instructions and demonstrations are excluded. Two Scopes with different Prompt
selections cannot share a config entry merely because the rerank instruction policy ID matches.

`RerankGroup` contains `round`, `config_id`, `outcome` (`selected`, `fallback`, or `failed`), `fallback_reason`
(`none`, `empty_selection`, `inference_unavailable`, `inference_timeout`, or `invalid_output`), and positive `count`.
These fields summarize actual rerank invocations; identical configurations and outcomes are grouped and share one
config entry. A fallback still records the configuration that was invoked. No invocation means no group or config.
A model-backed config has `non_deterministic=true`, even when its invocation fails or falls back. An injected reranker
must supply equivalent evidence and declare whether it is model-backed; unavailable evidence is a Receipt failure,
not permission to invent a built-in Prompt or mark the call deterministic. A declared deterministic non-model
reranker uses `prompt=null` and `non_deterministic=false`, and identifies its actual algorithm/version and effective
safe settings in the configuration record. Merely lacking model metadata does not establish determinism.

`config_id` is the `config_digest` itself; config entries are sorted by this digest and every rerank group must resolve
to exactly one entry. Group reranks by all four grouping fields and sort by that tuple.

### Omission and timing summaries

`OmittedGroup` contains `family`, `reason`, and positive `count`; equal family/reason pairs are grouped. Families use
the same five-value enum as selected items. Reasons are closed:

| Reason | Meaning |
| --- | --- |
| `duplicate` | Repeated Scope-qualified exact candidate identity excluded by deduplication |
| `blank` | Empty identity or empty renderable text |
| `family_limit` | Candidate excluded by that family's or assembly section's admission cap |
| `entry_limit` | Candidate excluded by the combined injection item cap |
| `below_min_bytes` | Candidate too short to truncate into the remaining budget |
| `no_fitting_truncation` | No permitted truncation fits the remaining byte budget |
| `rerank_not_selected` | Candidate in the coarse Memory pool excluded by listwise rerank before the Builder |
| `not_retrieved` | Requested family produced no candidates across the retained rounds |

`below_min_bytes` and `no_fitting_truncation` preserve the Builder's existing `dropped_below_min_bytes` and
`dropped_no_fitting_truncation` distinction. Their sum is `dropped_items`; do not add that total again as a separate
omission. Successful truncation is recorded on the selected item and Receipt, not counted as an omitted candidate.

`not_retrieved` is an empty-set flag per requested family with `count=1`, including a missing reader/head. It is absent
when that family produced any candidates for the final build, even if every candidate was later dropped. Other reasons
count exclusion events in the bounded candidate lists processed for the retained rounds and final build, not distinct
Artifacts in storage. A repeated identity increments `duplicate` when it is excluded. Abandoned expansion pools do not
inflate these counts. Reuse existing omission and recall-effort counters where they represent the same event; count
other exclusions while traversing the same bounded pools, without extra database searches or whole-Artifact scans.

`StageTiming` contains `stage`, `duration_ms`, and `count`. Aggregate repeated instances of the same existing Runtime
stage by summing durations and recording the instance count; this sum need not equal wall-clock prepare latency.
Include only stages that ran, including abandoned rounds. Do not copy span payloads or add per-Scope timing lists.

## Bounds

| Limit | Value |
| ---: | ---: |
| `selected` | 32 items |
| `retrieval.rounds` | 3 |
| `retrieval.searches` | 32 groups total |
| `retrieval.rerank_configs` | 8 distinct configurations |
| `retrieval.reranks` | 32 groups total |
| `omitted` | 16 groups |
| `stages` | 8 aggregate timings |
| `receipt` JSON UTF-8 size | 8192 bytes |
| item bodies, original or expanded query text, prompt bodies, model responses, vectors, secrets, tokens, absolute paths | forbidden |

Check the byte budget on the exact serialized Receipt returned to the caller. The count caps do not guarantee that
32 complete identities plus configuration evidence fit into 8192 bytes. Any count overflow, byte overflow, incomplete
identity, or missing execution evidence drops the entire Receipt with a content-free diagnostic and leaves injection
unchanged. Never truncate identities, replace configuration evidence with a generic policy ID, or ignore extra calls.
Implementation acceptance must measure representative mixed-family, cross-Scope, and rerank-config payloads using
real identity lengths. If ordinary workloads frequently exceed the byte budget, revise that budget using the measured
results before shipping; do not silently ship consistently absent diagnostics.

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

Implementation begins only after this design is accepted, as required by the tracking issue.

1. Extend `PrepareContextRequest` and `PreparedContext` in OpenAPI, then regenerate bindings.
2. Preserve ordered selected identities and per-item rendering metadata from `build_scopes_result()` and code assembly.
   Count exclusions in the same bounded pools and reuse existing budget-omission and recall-effort counters.
3. Carry request-local execution evidence out of each search and inference invocation before `_recall_scope` and
   `_recall_round` discard it. Aggregate actual modes and fallback causes by round; retain failed expansion outcomes.
   Bind rerank configuration evidence to the effective model settings and actual resolved Prompt at invocation time.
4. Hash the normalized original query and final injected `content` after the Builder returns. Validate ordered complete
   origins, referential integrity of rerank groups/configs, all count caps, and exact serialized byte size.
5. On any Receipt failure, log a content-free diagnostic and return the unchanged PreparedContext without `receipt`.
6. Leave official host recall requests unchanged. Update a host validator only in a change that opts that host into
   Receipts. Do not add a Receipt table or progressive-content cache.

Implementation acceptance covers:

- Empty, ready, truncated, deduplicated, reranked, fallback, and receipt-construction failure results; default, false,
  and null flags continue to return exactly four response keys.
- Topic Memory in default recall; mixed Memory/Topic Memory/Experience/Profile assembly with 18 and 26 selected
  historical items; code-only and mixed code results, including final truncated code ranges and hashes.
- Ordered complete selected identities across both origins collections, identical short IDs in different Scopes,
  exact-read reuse, and `content_digest` equal to the hash of returned UTF-8 `content`.
- Hybrid and FTS in different Scopes in the same round, completed zero-hit versus not-run searches, normal FTS versus
  inference fallback, reused FTS fallback, and failed expansion returning only the round-zero candidate pool.
- Effective inherited and separate rerank models/settings, distinct Scope Prompts and revisions, changed settings or
  compiled Prompt changing the config digest, repeated config deduplication, temperature-zero non-determinism, and
  injected rerankers with missing evidence causing fail-open Receipt omission.
- Both budget-drop sub-counts, all five families' empty-set flags, no double counting of abandoned rounds, each count
  cap, representative serialized payload sizes, and byte overflow dropping the whole Receipt without identity loss.
- Equivalent Receipt semantics on SQLite and OceanBase, no schema migration, no additional recall/model calls caused
  by `include_receipt`, and no forbidden content in successful Receipts or failure diagnostics.

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

- `PreparedContextBuild.origins` and `code_origins` retain the exact selected identities, discarded at the HTTP boundary.
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
