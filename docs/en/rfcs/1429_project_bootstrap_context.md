# RFC 1429: Bounded Scope Bootstrap Context

- Status: Implemented
- Issue: [#1429](https://github.com/oceanbase/powercontext/issues/1429)
- Related RFCs: [Scope organization and Agent integration](1345_scope_organization_and_agent_integration.md), [Memory layer](0014_memory_layer_design.md), [Handoff Artifact](0048_handoff_artifact.md), and [Artifact tags](1467_artifact_tags.md)

## Summary

PowerContext may provide a small, explicitly curated context package before the first real query of a supported Agent lifecycle. The package is opt-in, deterministic, byte-bounded, cited to immutable content, treated as untrusted history, and delivered at most once for a host-provided stable lifecycle identity. Delivery failure never prevents the Agent from starting.

Issue #1429 used the product terms Project and Workstream. RFC 1345 subsequently made Scope the only ownership and routing boundary and direct Context References the authorized read set. This RFC therefore implements the requested behavior for one resolved current Scope plus its direct Context References; it does not reintroduce Project or Workstream catalogs.

## Goals

- Prepare useful context at `startup`, `resume`, `clear`, `compact`, `restore`, or `fork` without manufacturing a search query.
- Include only reviewed, explicitly selected material and preserve exact authoritative references and content digests.
- Bound work, item count, per-item text, and final UTF-8 bytes without model inference.
- Persist content-free delivery receipts with `pending`, `injected`, `skipped`, or `failed` state.
- Let the first ordinary query identify the last injected receipt so identical Memory entry versions are not returned twice.
- Support both Codex and Claude Code through their maintained `SessionStart` hooks.

## Non-goals

Bootstrap is not full Memory recall, a transcript summary, a new instruction tier, or a replacement for `prepare_context`. It does not create or infer a Scope binding. It does not read raw Sources, pending candidates, generated Profile drafts, arbitrary Artifact families, environment variables, repository files, or conversation transcripts.

## Eligibility profile

The initial profile is `powercontext.scope-bootstrap.v1`.

Eligible content is deliberately narrow:

1. Up to six active current Memory entries explicitly tagged `bootstrap-context`. The current Scope is considered first, followed by at most eight direct Context References in canonical Scope order. At most 32 tagged entries are inspected per participating Scope.
2. At most one committed Handoff Revision supplied by the caller as an exact `{family, artifact_id, revision}` reference in the current Scope. “Latest Handoff” is never selected implicitly.

The `bootstrap-context` tag is an operator review decision, not an authorization mechanism. Operators must not tag credentials, secrets, personal data, generated instructions that have not been reviewed, or material that should not be injected automatically. The Runtime additionally rejects entries whose kind identifies secret, credential, password, token, or private-key material. Current authorization is still checked for every participating Scope.

The package renderer includes a prominent untrusted-history policy. Current system and developer instructions, the current user request, repository rules, and live validation always take precedence. Historical Markdown is rendered as quoted data rather than executable instructions.

## HTTP contract

`POST /v1/context/bootstrap` accepts the resolved `scope_id`, opt-in `enabled`, profile, lifecycle, integration name, optional stable `event_id`, byte limit, and optional exact Handoff reference. The default is disabled. It returns `powercontext.bootstrap-context.v1` with `ready`, `empty`, or `skipped` status, the bounded content when ready, its digest, exact item references and digests, truncation information, and a delivery receipt.

A ready package has a `pending` receipt. Before emitting hook output, the host calls `POST /v1/context/bootstrap/receipts` with `injected` or `failed`. A host must claim the `pending`→`injected` transition successfully before writing `additionalContext`; a second `injected` claim is rejected even if the stored state is already `injected`. If the claim fails, the host emits no bootstrap content. Disabled and empty requests produce `skipped` receipts; a terminal event retry also returns `skipped` without a body.

Receipts store no query, content body, transcript path, raw host event identifier, or host error text. They store only bounded identity metadata, exact content references and digests, byte counts, and state. A supplied event identifier is hashed with the Scope, integration, and lifecycle before persistence. Repeating the same stable event returns the existing result while pending and returns `skipped` after a terminal outcome. If opt-in is revoked while an event is pending, that receipt is terminally skipped without reading Context References. Hosts that expose no stable event identity still get per-invocation receipts but cannot claim restart idempotency.

`PrepareContextRequest` optionally accepts `bootstrap_receipt_id`. When that receipt belongs to the same Scope and is `injected`, ordinary recall removes only the exact Memory entry versions listed by the receipt. Revised versions remain eligible. An absent, stale, foreign, pending, skipped, or failed receipt is ignored so recall remains fail-open.

## Rendering and bounds

The default final limit is 4,096 bytes and the hard maximum is 8,192 bytes. There are at most seven items: one exact Handoff followed by six curated Memory entries. Each item body is capped at 2,000 bytes and truncated only at Unicode character boundaries. Complete Scope, Artifact Revision, Memory entry/version, digest, and truncation metadata remain visible. Items that cannot retain a meaningful body and their citation within the remaining budget are omitted.

Selection uses exact tag lookup and immutable reads only. It does not embed, rerank, generate, or expand. The participating Scope count, tagged-entry scan, selected item count, per-item text, and final output are all bounded. Ordering and rendering are deterministic for the same exact inputs.

## Host behavior

Codex and Claude Code resolve Scope through their existing binding service, then request bootstrap context from `SessionStart`. Their supported lifecycle sources stay distinct in the request and receipt. Both integrations are disabled by default, enforce one wall-clock HTTP budget, validate the complete response schema, persist only the latest receipt ID in the host's plugin data directory, and fail open on timeout, authentication failure, unavailable or older servers, and invalid responses.

The subsequent `UserPromptSubmit` uses normal query-based recall and passes the saved receipt ID solely as an exact-version deduplication hint. Prompt capture behavior is unchanged.

## Security and observability

- Bootstrap never expands Scope authorization. The current Scope and every direct Context Reference considered by the bounded profile require `scope.read`.
- Package text is historical data, not authority. Exact citations make every included claim inspectable.
- Logs and diagnostics use outcome codes and counts only. They do not include package bodies, queries, identifiers supplied by the host, or errors returned by external systems.
- Database unavailability, malformed historical content, hook timeouts, and unsupported server versions all degrade to no injected context.

## Acceptance

Implementation is complete when contract and runtime tests cover opt-in defaults, all lifecycle values, exact Handoff selection, curated Memory ordering, Unicode byte bounds, secret-kind exclusion, authorization across Context References, receipt retry and transition semantics, first-query exact-version deduplication, and content-free storage; and both Codex and Claude Code tests cover successful injection, disabled/empty paths, state handoff to the first query, invalid responses, timeouts, and HTTP failures without blocking the host.
