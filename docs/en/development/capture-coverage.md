---
title: Capture coverage contract
description: Versioned coverage declarations for Hermes and Gateway Content Source projections.
---

# Capture coverage contract

Hermes and Gateway attach a bounded declaration to new Content Source metadata under
`powercontext_capture_coverage`. It describes capacity losses observed while producing
one supported projection. It does not describe the completeness of the original conversation.

This reference defines `powercontext.capture-coverage.v1`, implemented for
[issue #2437](https://github.com/oceanbase/powercontext/issues/2437).
The shared synthetic cases live in `tests/integration_conformance/capture_coverage_cases.json`;
Python and Rust consume that same file without a shared runtime or plugin framework.

## Declaration

```json
{
  "powercontext_capture_coverage": {
    "schema": "powercontext.capture-coverage.v1",
    "projection": "hermes-turn-text.v1",
    "unit": "user_assistant_text_pair",
    "status": "complete",
    "reasons": []
  }
}
```

The declaration has exactly these five fields. The object itself must fit within
512 UTF-8 bytes, measured as compact JSON without ASCII escaping, excluding the outer
metadata key. All valid v1 field values are fixed enums, so the bound follows from the
contract. Object key order is immaterial; reason array order is significant.
Other metadata, including opaque prefix-extraction extensions, must remain unchanged.
Do not add content, paths, message ID lists, prompts, model configuration or credentials.

| Field | Contract |
| --- | --- |
| `schema` | Exactly `powercontext.capture-coverage.v1`. |
| `projection` | One of the three profiles below. |
| `unit` | The exact unit paired with that profile. |
| `status` | `complete`, `partial` or `unknown`. |
| `reasons` | Unique supported strings in ascending lexicographic order. |

Supported reasons are `character_limit`, `field_byte_limit`, `transcript_byte_limit`
and `unmeasured_projection`.

| Observed facts | Status | Reasons |
| --- | --- | --- |
| The supported projection is measurable and has no additional capacity loss. | `complete` | Empty. |
| At least one capacity loss occurred. | `partial` | Every observed capacity-loss reason. |
| Coverage cannot be measured, with no known capacity loss. | `unknown` | `unmeasured_projection`. |
| Known loss and an unmeasured portion coexist. | `partial` | Known loss reasons plus `unmeasured_projection`. |

A partial declaration must contain a real capacity-loss reason; `unmeasured_projection`
alone is insufficient. An unknown declaration cannot hide a known loss. Missing metadata,
an absent key, unknown schema or profile, mismatched unit, unsupported or incorrectly typed
fields, extra fields, duplicate or unsorted reasons, and inconsistent status/reasons are
interpreted as unknown. Interpretation never rewrites the stored payload.

## Supported projections

| Profile | Unit | Projection boundary |
| --- | --- | --- |
| `hermes-turn-text.v1` | `user_assistant_text_pair` | The current normalized user/assistant text pair with the existing role labels and separators. |
| `hermes-precompress-text.v1` | `new_user_assistant_window` | The new user/assistant entries delivered by the host after existing filtering and overlap removal. |
| `gateway-turn-text.v1` | `gateway_turn_projection` | The existing supported Chat/Responses normalized turn from the last user message through the terminal assistant response. |

### Hermes completed turn

Coverage compares the complete concatenated string, including role labels and separators,
with the existing 50,000-character limit. A string exactly at the limit is complete;
actual clipping produces partial/character_limit. Python characters are counted, not UTF-8 bytes.
The captured text, original source ID calculation, role selection, secret handling and queue
behavior remain unchanged. The `messages` argument does not expand this profile to tool history.
Empty pairs do not create a Source. Text that resembles a truncation marker remains ordinary data.

### Hermes pre-compress

The host's `evidence_messages` takes precedence when supplied. The projection uses the existing
user/assistant filter, secret redaction, fingerprinting and overlap handling. Its unit is the
entire new window requested for this capture, not just the prefix that fits.

The existing 30,000-character limit admits complete entries. If every new entry fits, coverage
is complete. Optional capture of a nonempty prefix is partial/character_limit. If the first
entry cannot fit, no empty Source is created. Required checkpoints reject an incomplete
window before sending a partial Source or advancing the captured snapshot.

Scope binding, overlap identities and checkpoint transitions retain their existing semantics.
Once capture is durable, later extraction failure does not invalidate the checkpoint.

### Gateway normalized turn

The projection retains its existing visible text, tool calls and tool outputs. Injected prepare
content, opaque reasoning, transport IDs and unsupported media remain outside the profile.
Excluding them does not itself make this projection partial.

Actual clipping at the existing 8 KiB field boundary records `field_byte_limit`; actual step
removal under the existing 64 KiB transcript policy records `transcript_byte_limit`. Both are
retained if both happened, even when the clipped field belonged to a subsequently omitted step.
UTF-8 boundaries, body JSON, `omitted_steps`, truncation markers and terminal-response detection
remain unchanged. Coverage comes from the same projection computation as the body, without
re-parsing the serialized transcript or keeping global state.

An accepted Responses history item with an unknown type is unmeasured because the existing
projection skips that item. History messages without exactly one normalized item are also
unmeasured; the body retains its existing first-item projection. An unknown terminal response, a client tool call requiring another
turn, or an unparseable response still returns no transcript and creates no Source. Marker-like
text does not change coverage.

Responses call coverage also requires a known call type and a non-null payload in one of the
existing projected fields: `arguments`, `input`, `action`, `code`, `query` or `queries`.
A type accepted only by its `_call` suffix, such as `apply_patch_call` or a future call type,
is unmeasured even if one of those fields is present. A known call with no projected payload
is also unmeasured; an explicitly empty string is still measurable. This applies to history
calls and hosted calls in the terminal response. Body rendering and terminal detection remain
unchanged; unmeasured calls record `unmeasured_projection` alongside any known capacity losses.

Responses history outputs are measurable for the supported function, custom, computer and shell
output types when `output` is present and non-null; an empty string is measurable. MCP approval
responses and unknown `*_call_output` types record `unmeasured_projection`, even if they also
contain an `output` field. Their other payload fields are not projected. Missing or null output
on a supported type is also unmeasured. Body rendering and terminal detection remain unchanged.

Chat tool calls are measurable when their function name and arguments are strings and their
type is `function` or absent, preserving the existing legacy shape. Other call types or
unprojectable function fields record `unmeasured_projection` without changing body rendering.
Known capacity losses still produce partial with both the loss reason and the unmeasured reason.

## Identity, replay and upgrades

New captures submit body and declaration together. Identical retries preserve source ID, body,
declaration and unrelated metadata, and do not allocate another journal position. Coverage does
not enter the source ID algorithm. SourceRepository retains its complete-payload conflict rule.

Hermes pre-compress freezes the first attempted coverage reasons for each Source identity in
the active provider Scope/stream, before sending. If acknowledgement is lost and the host later
supplies a shorter or larger window with the same captured prefix, replay uses the original
declaration. The current window is still independently checked for required-checkpoint eligibility;
successful replay does not turn a partial historical declaration into complete.

This process-local map stores only a fixed reason tuple per attempted identity, not transcript
content. Its memory grows with distinct attempted identities in the active Scope/stream;
initialization, Scope changes and stream reset/rewind clear it. It is not a durable recovery journal:
after a provider restart, a differently sized host window is not automatically reconciled with
an earlier declaration. Body and unrelated metadata conflicts remain errors in every case.

For Hermes capture only, a 409 can trigger one legacy attempt with the same Scope, source ID,
body and other metadata, removing only this newly added coverage key. This attempt succeeds only
if the Server accepts that exact legacy payload. A second failure preserves the original conflict;
401, 403 and transport failures on the first attempt do not trigger this path. Ordinary turn
failure diagnostics and required-checkpoint failure behavior remain at their owning boundaries.

A successful legacy replay leaves the old Source without a declaration, interpreted as unknown.
There is no historical backfill, alternate ID, extra lookup or generic HTTP-client retry policy.
Gateway keeps its existing 409 first-write-wins handling; a duplicate does not establish that the
new declaration was persisted.

## Persistence, evidence and trust

The existing Content Source adapter and TextEvidence projection preserve metadata. A full
SourceRepository read after reopening SQLite returns the original declaration and other metadata.
The current HTTP `get_source` for a Content Source exposes its body and identity, **not metadata**;
this feature does not add an HTTP response field. The E2E test verifies HTTP body/identity across
a real Server process restart, and full metadata through the existing repository read afterward.

Capture coverage is separate from `EvidenceManifest.incomplete`, privacy admission's
`coverage.complete`, authority/verification and root independence. A caller-supplied declaration
does not attest origin or grant permissions. Core does not use this key to change generation
eligibility, authority, trust policy, Memory content or recall.

The declaration remains untrusted evidence data. It is not converted into system instructions.
Its survival in Source metadata and extraction input does not promise deterministic propagation
through model summaries into Memory or PreparedContext. No host lifecycle capability is upgraded.

## Input cost and validation

The declaration can occur in both the evidential content and its provenance. Measure complete
input instead of treating the declaration's byte limit as a model-input budget.

The fixture's `budget_sample` uses `hermes-turn-text.v1` with the built-in coding extraction
instructions, an injected recording extractor, no custom input renderer and estimator
`character:weighted`, version `1`:

| Measurement | Without declaration | With declaration | Increase |
| --- | ---: | ---: | ---: |
| Declaration object, compact UTF-8 bytes | 0 | 147 | 147 |
| Serialized `AtomicMemoryExtractionInput`, UTF-8 bytes | 608 | 968 | 360 |
| Estimated tokens for instructions + newline + serialized input | 1,238 | 1,328 | 90 |

These are synthetic measurements, not a real model's token usage. Instructions, rendering,
estimator and input contents can change the totals. The complete input budget remains enforced:
an oversized single Source is rejected without consuming its cursor, and an oversized multi-Source
window follows the existing persisted shrink/retry behavior. Neither path discards the declaration
or relaxes the budget.

Validation commands:

```bash
uv run pytest tests/integration_conformance tests/integrations/test_hermes_provider.py -q
uv run pytest tests/e2e/test_capture_coverage.py -q
cargo test --manifest-path gateway/Cargo.toml -p pc-gateway-core
cargo clippy --manifest-path gateway/Cargo.toml -p pc-gateway-core --all-targets -- -D warnings
```

The E2E covers real local HTTP, separate Server processes and SQLite, exact replay, conflicts,
Scope isolation, TextEvidence and AtomicMemoryEvidence. Model generation is replaced by a recording
extractor; directly invoking the Hermes provider verifies its adapter behavior, not native Hermes
host qualification. Gateway capture behavior is tested through its executor and controlled peers.
