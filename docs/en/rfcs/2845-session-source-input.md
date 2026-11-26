---
title: "RFC 2845: Session Source Input"
description: "Define role-aware Source input for live conversation turns and historical conversation imports."
---

- Proposal Name: `session_source_input`
- Start Date: 2026-11-26
- RFC PR: [oceanbase/powercontext#2845](https://github.com/oceanbase/powercontext/pull/2845)

# Summary

Extend the existing Content Source capture APIs to accept ordered User and Assistant text messages as an alternative to plain text. Live capture submits a user request and its Assistant response after the final response completes; historical import can submit multiple turns at once. Each submission is stored as one immutable Source, and subsequent processing preserves message roles and order. Plain-text behavior remains unchanged.

# Motivation

User input to a coding agent often depends on context, such as “You got it wrong.” Saving that sentence alone does not identify which Assistant response it refers to. A complete turn gives memory extraction context, while historical import can supply a longer conversation at once. An Assistant response is useful observational material, but it can contain plans, inferences, or mistakes and must not be treated as a user-confirmed fact.

The baseline `POST /v1/sources/content` accepts a string `content` and optional `metadata`; its text evidence projection supplies content and metadata to processors but has no structured message roles. A Source is deduplicated by identity, and an existing identity conflicts if its content or metadata differs. Successful capture does not mean memory was extracted. See [Discussion #2756](https://github.com/oceanbase/powercontext/discussions/2756).

# Guide-level explanation

## Live turns

The caller submits after receiving the final Assistant text response:

```json
{
  "scope_id": "scope-1",
  "source_id": "session-123-turn-1",
  "session": {
    "session_id": "session-123",
    "messages": [
      {"role": "user", "content": "Update the login module's timeout handling"},
      {"role": "assistant", "content": "I updated the handling and added regression tests."}
    ]
  },
  "metadata": {}
}
```

Use a different stable `source_id` for each turn. Retry a turn with the same ID and exactly the same request. `session_id` correlates Sources belonging to a conversation; it is not an authorization boundary.

## Historical import

The `messages` in one submission can contain multiple User and Assistant turns. The submission is stored as one Source rather than one Source per message. A historical import may overlap a live Source; the server does not deduplicate across Sources. Each import batch uses its own stable `source_id`.

# Reference-level explanation

## API and validation

Extend `POST /v1/sources/content` and each Source item in the batch API with a `session` object. A request must supply exactly one of `content` and `session`; existing requests without `session` remain unchanged. A `session` contains a nonblank `session_id` and a nonempty, time-ordered `messages` list; each message contains only `role` (`user` or `assistant`) and nonblank text `content`. Roles need not alternate, and no starting or ending role is required, so a historical segment may begin at any point. Unknown fields, blank text, unknown roles, and providing both forms return 422. The batch API retains its all-or-nothing transaction semantics.

Each message is limited to 200,000 characters, a Session to 512 messages, and the sum of message content to 200,000 characters. Exceeding a limit returns 422 without truncation or silent splitting; callers split at message boundaries and assign each segment a new `source_id`. These are proposed initial limits to validate against storage and model budgets before implementation. The response remains 202 with the Source reference and journal position; replay and conflict behavior remain unchanged.

## Identity and persistence

Keep the existing Content Source identity namespace; do not reuse `source_type` as a conversation-type parameter. Store a structured payload with an explicit version and type, preserving `session_id`, roles, order, and text. Read and write ordinary Content Sources in the existing format; a Session must round-trip losslessly through persistence, reads, restart, and replay. Different `source_id` values may overlap in content; different content or metadata under the same identity returns 409. The lossless structure is authoritative; model-facing text is a derived projection and must not become the authoritative original.

This RFC does not require a new database table or column. Before implementation, verify that existing Source payload encoding, schema registration, recovery, and historical reads support the extension. If a table change is necessary, separately describe the tables, fields, effects on existing data, and rollback, and obtain approval; do not hide compatibility issues by changing published migrations.

## Processing and security boundary

Plain text follows the existing processing path. A role-aware projection supplies Sessions to existing generation pipelines: messages appear in order, and structure distinguishes roles from message bodies rather than relying only on `User:` text that a body can forge. Processing prompts must distinguish user instructions, Assistant inferences, and verified outcomes; an Assistant claim that work is complete must not by itself establish that fact. Source content is always untrusted data, not an instruction to the processor. Retain the Source reference as evidence for generated results; extraction is not guaranteed to produce a memory.

Capture success means the Source was durably stored and entered the existing processing mechanism, not that model processing, indexing, or recall succeeded. Failures follow existing retry and state mechanisms; implementation must verify that Sessions are not silently skipped and plain Sources do not regress. The first version adds neither Agent Memory nor Agent Experience and does not change the recall API.

Callers apply applicable privacy filtering and secret redaction to both roles before capture; the server cannot assume every client has redacted content. Logs, metrics, and error responses must not contain message bodies or credentials. Session retention, deletion, and access control follow existing Source policies; `session_id` grants no additional authorization.

## Integration and delivery

Live integration submits one User/Assistant pair when the host can identify the final response; intermediate tool calls, tool results, and streaming fragments are not captured. Interrupted or failed turns without final text are not submitted as complete turns. A Source submission failure must not alter the model response, and reliable delivery must not be claimed without specifying retry and loss semantics. Hosts differ in their final-response events and must be verified individually; server API availability does not mean a plugin or gateway is integrated. Historical import is explicitly supplied and segmented by the caller.

## Acceptance

- Existing single and batch `content` requests, responses, retries, and conflicts remain unchanged.
- Single-turn and multi-turn Sessions retain original roles, order, and text through API, storage, restart, and supported reads.
- Retrying the same ID with the same content does not add a record; different content or metadata conflicts; partially overlapping content under different IDs is accepted.
- Invalid roles, blank text, both inputs, size limits, and batch conflicts have explicit outcomes; batch failure leaves no partial writes.
- The actual processing entry distinguishes User from Assistant, and forged role markers in message bodies do not change actual roles; verify failure and retry behavior.
- Exercise a real host that supports final responses for success, tool continuation, interruption, and capture failure; do not mark hosts as verified when they are not integrated or only simulated inputs were used.
- Compare User-only and User/Assistant extraction on labeled conversations, checking attribution, false facts, duplicates, and cost; successful acceptance is not evidence of extraction quality.

# Drawbacks

Saving Assistant responses increases privacy exposure, storage, and model processing costs. Long histories can exceed budgets, and splitting can lose context. Duplicate content in different Sources may produce duplicate candidates. Role labels can improve attribution but cannot guarantee correct model extraction.

# Rationale and alternatives

Compared with concatenating a conversation into `content`, structured input makes roles, order, and format explicit and prevents labels in user text from becoming message boundaries. Compared with one Source per message, this design retains a turn or historical segment as an evidence unit and more readily preserves local context, at the cost of independently addressing individual messages. Compared with mutable Sessions, appending immutable Sources reuses existing retry and conflict semantics but does not automatically reconstruct the full session or deduplicate across segments. A separate endpoint is also possible, but this input remains a Content Source, so extending the existing endpoint avoids splitting the entry point.

# Prior art

mem0 supports extracting memories from conversation messages and also offers a write path without inference; OpenViking separates original sessions, archives, and asynchronously derived memories; Memobase buffers chat before generating profiles and events; mem9 supports session storage and fact extraction. Their retention, failure, and retrieval semantics differ, so receipt cannot be equated with reliable raw storage or searchability. This proposal borrows the separation of originals and derived memories without copying their storage or recall APIs.

# Unresolved questions

- Before implementation, verify that existing Source extension, projection, and persistence registration can read the new payload losslessly, and determine the role-aware processor representation and test baseline.
- Validate initial size limits and host final-response event availability through measurement.
- Host-persistent retries and cross-restart delivery guarantees require separate design for each integration; this RFC does not assume all hosts support them.

# Future possibilities

Evaluate conversation history in query construction, reading or searching original content by session, tool messages, and new Agent Memory or Experience types separately. These capabilities are outside this RFC's acceptance scope.
