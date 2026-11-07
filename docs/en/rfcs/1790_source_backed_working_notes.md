---
title: "RFC 1790: Source-backed Working Notes"
description: A design decision to capture short-lived Working Notes as Sources, with exact identity, expiry, and exclusion from automatic Memory generation.
---

# Source-backed Working Notes

- Proposal Name: `source_backed_working_notes`
- Tracking Issue: [oceanbase/powercontext#1790](https://github.com/oceanbase/powercontext/issues/1790)
- Status: implementation decision

## Decision

PowerContext uses a Source-kind-only design for short-lived working notes. It does not add a durable `notes` Artifact
family. A working note is captured as a `ContentSource` marked by a server-owned field, with descriptive kind and
session metadata. The Source remains addressable by its exact `(scope_id, source_type, source_id)` identity.

This is distinct from a Memory entry whose `kind` is also `working_note`: the latter is an explicit Memory write and is
eligible for Memory lifecycle and retrieval. A captured Working Note is evidence only until a caller explicitly uses it
in a domain operation.

Hosts capture one through `POST /v1/sources/working-notes`, the generated Python Client's `capture_working_note`, or the
same MCP tool when exposed. The request requires a caller-stable `source_id`, host `session_id`, content, and one of
`inspection`, `abandoned_approach`, `validation`, `unresolved`, or `host_context`. Replaying an identity with different
content remains a Source conflict. A caller may also set a timezone-aware `expires_at` in the future. An exact replay
of an accepted capture returns its original receipt even after expiry; expiry does not weaken capture idempotency.

Working Note classification is a server-owned `ContentSource` field. The `metadata.kind=working_note` value is retained
as descriptive metadata but is not authoritative by itself. Generic content capture rejects that reserved kind and
cannot set the server-owned field. Existing Sources that only happen to carry this metadata value remain ordinary
Sources after upgrade and retain their previous generation behavior.

## Processing boundary

Working Notes are excluded from automatic Source-window generation. They must not be promoted into Memory, Topic Memory,
Experience, or another generated artifact merely because they were captured. Explicit consumers may opt in to the exact
Source reference. Handoff evidence resolution opts in because a cited Working Note is still inspectable evidence.

The content is untrusted historical material. It is not an instruction, Skill, prompt policy, or executable tool input.

## Retention and access

Working Notes inherit Scope Source authorization. `session_id` identifies the originating host/session for grouping and
future cleanup; it is not an additional authorization boundary. When `expires_at` is omitted, the note remains active
until a future explicit lifecycle action. After the timestamp, new consumers, including Handoff evidence resolution,
must treat the note as unavailable.

Expiry is logical: it does not delete or rewrite the immutable Source, which remains readable through the supported
Source audit path. Physical purge, session-end cleanup, legal hold, and cross-scope export are separate lifecycle work
and are not implemented by this decision.

An exact Source reference remains the evidence identity when a Handoff cites a note. A later cleanup operation must not
silently resolve that citation to different content.

## Promotion

Promotion is disabled in the initial implementation. A future explicit promotion operation must submit the exact
Source reference and pass the owning domain's normal validation. No extractor may infer a durable `fact`, `decision`,
or `constraint` solely from the note's presence.

## Alternatives rejected

- A new Notes Artifact family duplicates Handoff and Source revision/lineage machinery before the use case is proven.
- Storing notes directly in Memory pollutes durable retrieval with session progress.
- Keeping notes only in host transcripts prevents cross-host evidence resolution.
