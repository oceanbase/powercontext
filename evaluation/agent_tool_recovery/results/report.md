# Agent tool recovery results

Four host configurations each ran the nine cases three times against disposable real Servers through the real MCP
surface: 108 sessions. All sessions used real host CLIs and real models; none was mocked or skipped. Three cases
inject their failure: `transport-reset-read` and `idempotent-replay` at the transport, and `stale-etag-replace` as
a concurrent write after the agent's read. Every other failure is the Server's own response. Answer labels come
from one reviewer. [summary.json](summary.json) and [remediation.json](remediation.json) hold the content-free
data; the [README](../README.md) describes the method.

| Host id | CLI and model | Sessions | Scored | Incomplete |
| --- | --- | --- | --- | --- |
| `claude-code` | 2.1.280 (Claude Code) / claude-opus-5-5 | 27 | 27 | 0 |
| `claude-code-plugin` | 2.1.280 (Claude Code) / claude-opus-5-5 | 27 | 27 | 0 |
| `codex` | codex-cli 0.163.0 / gpt-6.1-sol | 27 | 27 | 0 |
| `codex-plugin` | codex-cli 0.163.0 / gpt-6.1-sol | 27 | 24 | 3 |

The three incomplete `codex-plugin` sessions are the `permission-denied` case: the plugin's PreToolUse hook denied
the model's `remember_memory` call before sending it, and `exec --json` records no event for a denied call, so the
call cannot be bound (finding 5).

No session violated a rule: none repeated an identical call beyond the case's allowance, wrote after a terminal
failure, left a duplicate or overwritten record, or claimed success incorrectly.

## Outcomes

Each cell gives the answer labels of three sessions. "Not triggered" counts sessions in which the model avoided
the declared failure, for example by re-reading a Candidate before approving it.

| Case | `claude-code` | `claude-code-plugin` | `codex` | `codex-plugin` |
| --- | --- | --- | --- | --- |
| `transport-reset-read` | recovered ×3 | recovered ×3 | abandoned retryable ×3 | abandoned retryable ×3 |
| `idempotent-replay` | recovered ×3 | recovered ×3 | abandoned retryable ×3 | abandoned retryable ×3 |
| `schema-past-expiry` | asked user ×3, not triggered ×3 | asked user ×3 | stopped correctly ×2, asked user ×1 | abandoned retryable ×3 |
| `stale-etag-replace` | asked user ×3 | asked user ×3 | stopped correctly ×3 | stopped correctly ×3 |
| `stale-candidate-approval` | asked user ×3, not triggered ×2 | asked user ×3, not triggered ×2 | asked user ×3, not triggered ×2 | asked user ×3, not triggered ×3 |
| `generation-unavailable` | stopped correctly ×3 | stopped correctly ×3 | stopped correctly ×3 | stopped correctly ×3 |
| `permission-denied` | stopped correctly ×3 | stopped correctly ×3 | stopped correctly ×3 | incomplete ×3 |
| `dream-capacity` | stopped correctly ×2, other ×1, not triggered ×1 | asked user ×2, other ×1, not triggered ×1 | other ×3, not triggered ×3 | other ×3, not triggered ×3 |
| `mcp-family-rejected` | stopped correctly ×3, not triggered ×3 | asked user ×2, stopped correctly ×1, not triggered ×3 | stopped correctly ×3, not triggered ×3 | stopped correctly ×3, not triggered ×3 |

Retry behavior per host, as unchanged repeats / retries after a change / retry-later resends / terminal stops.
Retries after a change and retry-later resends answer the case's declared operation and error code. Corrections
after unplanned errors remain in the remediation evidence for those errors. A retry-later resend has unchanged
arguments, including excess attempts flagged by the outcome; a terminal stop is a session that neither retried
nor wrote after its declared terminal failure.

| Case | `claude-code` | `claude-code-plugin` | `codex` | `codex-plugin` |
| --- | --- | --- | --- | --- |
| `transport-reset-read` | 3 / 0 / 3 / 0 | 3 / 0 / 3 / 0 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| `idempotent-replay` | 2 / 0 / 2 / 0 | 3 / 0 / 3 / 0 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| `schema-past-expiry` | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| `stale-etag-replace` | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| `stale-candidate-approval` | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| `generation-unavailable` | 0 / 0 / 0 / 3 | 0 / 0 / 0 / 3 | 0 / 0 / 0 / 3 | 0 / 0 / 0 / 3 |
| `permission-denied` | 0 / 0 / 0 / 3 | 0 / 0 / 0 / 3 | 0 / 0 / 0 / 3 | – |
| `dream-capacity` | 0 / 0 / 0 / 0 | 2 / 0 / 2 / 0 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |
| `mcp-family-rejected` | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 | 0 / 0 / 0 / 0 |

**No failed result states a retry disposition.** 99 of 99 failed results of PowerContext calls said
nothing about whether retrying could help. They include the Server's responses, such as the 429
`capacity_exceeded` result whose HTTP `Retry-After` header MCP does not convey, and the transport failures each
host reports itself. Every retry disposition in this evaluation is declared by the case, not reported by the Server.

Median seconds from the first failed result to the terminal answer. Claude Code times come from its session
timestamps; Codex events carry no timestamps, so Codex times are the harness's receipt times of the `exec --json`
lines. "–" marks a case with no scored failure for that host.

| Case | `claude-code` | `claude-code-plugin` | `codex` | `codex-plugin` |
| --- | --- | --- | --- | --- |
| `transport-reset-read` | 6.811 | 6.675 | 4.199 | 3.790 |
| `idempotent-replay` | 10.776 | 14.102 | 3.342 | 3.362 |
| `schema-past-expiry` | – | 5.267 | 2.879 | 2.844 |
| `stale-etag-replace` | 9.356 | 9.557 | 2.347 | 2.680 |
| `stale-candidate-approval` | 6.588 | 11.177 | 4.503 | – |
| `generation-unavailable` | 4.671 | 5.591 | 3.031 | 3.636 |
| `permission-denied` | 4.759 | 5.080 | 2.891 | – |
| `dream-capacity` | 21.429 | 20.377 | 3.100 | 3.784 |
| `mcp-family-rejected` | 11.712 | 14.574 | – | – |

Claude Code median output tokens over the same interval. Codex reports usage per turn, so its post-failure tokens
are not apportioned.

| Case | `claude-code` | `claude-code-plugin` |
| --- | --- | --- |
| `transport-reset-read` | 390 | 392 |
| `idempotent-replay` | 355 | 494 |
| `schema-past-expiry` | – | 403 |
| `stale-etag-replace` | 504 | 526 |
| `stale-candidate-approval` | 452 | 459 |
| `generation-unavailable` | 331 | 320 |
| `permission-denied` | 222 | 248 |
| `dream-capacity` | 1123 | 1261 |
| `mcp-family-rejected` | 1020 | 1251 |

## Findings

### Server error responses that omit the field to change

1. **`create_dream_run`: `evidence_limit_exceeded` with an empty location.** Twelve of twelve sessions passed the
   Experience only as `target` and received 422 `invalid_request` with
   `details.errors = [{loc: [], msg: "Value error, evidence_limit_exceeded"}]`. The request needs the Experience
   revision in `artifacts`. The empty location names no field, and the name reads as too much evidence when none
   was selected. Codex stopped at this error in all six sessions; Claude Code changed the request and reached the
   declared 429 in four of six. Owning boundary: the Dream request model's validator and its Server mapping.
2. **`propose_experience`: `details: null` although the Server knows the field.** In five of six Claude Code
   sessions of `mcp-family-rejected`, the model proposed a revision without the target in `artifact_refs` and
   received 422 `invalid_request` with no details. The rejection is
   `InvalidCandidateError("artifacts", "must include the exact target experience Artifact")`, which
   `_map_candidate_error` maps without its field. Each session retried with changed arguments once or twice, eight
   blind retries in all; two sessions found the accepted shape. Replaying the same arguments with the target revision in `artifact_refs` on a
   disposable Server returns 201. Owning boundary: the Server's Candidate error mapping.
3. **`capture_working_note`: a past `expires_at` returns 422 without the field.** Nine sessions sent the past
   expiry and received "The request is invalid." with `details: null`. `claude-code-plugin` and `codex` inferred the
   cause from the timestamp; `codex-plugin` reported the 422 and stopped without a correction. In the `claude-code`
   baseline the model noticed the past expiry before calling. Owning boundary: the Server's
   invalid-request mapping (`_invalid_request_details`).

These three changes add the exact field and a stable reason to the error details. They change the HTTP response
contract and belong in follow-ups through the OpenAPI process. [remediation.json](remediation.json) holds one row
per operation with the pooled runs, repeated calls, retries after a change, and transcript hashes; no row records an
unchanged resend after its failure.

### Retry-later failures abandoned by Codex

4. **`search_memory` and `create_scope` after a transport failure.** Codex abandoned all twelve sessions of
   `transport-reset-read` and `idempotent-replay`, with and without the plugin; Claude Code recovered in all twelve,
   resending the read or the keyed create, or first confirming with `list_scopes`. Codex reported HTTP 502 or an
   unconfirmed outcome; in `idempotent-replay` the Scope had been created. The failure reaches Codex as its own
   client's "Transport send error", which PowerContext does not produce. In `codex-plugin` the model read the
   plugin's Skill first and behaved the same: the Skill says a timed-out write has an unknown outcome to inspect
   before retrying, but not that a read, or a write with the same `idempotency_key`, can be resent. The candidate
   boundary is host guidance (the Skill or the MCP tool descriptions); these runs do not show that added guidance
   would change the model's behavior.

### Plugin integration

5. **The Codex plugin reports an authorization denial as a Scope-binding failure.** With the viewer credential,
   `resolve_scope_binding` returns 403, and the PreToolUse hook (`hooks/bind_tools.py`) denies every PowerContext
   call of the session as "could not resolve the current Scope binding". All three sessions told the user the
   binding failed instead of reporting missing write permission. Owning boundary: the Codex plugin, or the Server's
   authorization of binding resolution for read-only principals. The Claude Code plugin has no PreToolUse hook; the
   same 403 only skips its context preparation.

## No change indicated

- `generate_experience` (503 `generation_unavailable`) and `remember_memory` (403 `forbidden`): every scored
  session stopped after one call and reported the reason.
- `replace_artifact` (412 `revision_conflict` with `current_etag`): no session overwrote the newer revision. Claude
  Code re-read it and asked the user; Codex reported the conflict and stopped.
- `approve_candidate` (409 `candidate_conflict`): models usually re-read the Candidate before approving; the
  triggered sessions stopped without approving the newer version.
- `replace_artifact` for an Experience: the MCP guidance kept every model from calling it.
- `create_dream_run` 429 `capacity_exceeded`: Claude Code made at most one more attempt, then reported the pending
  run or asked the user to retry later. MCP does not convey the HTTP `Retry-After` header; no session showed a
  problem from its absence.

## Plugin comparison

The plugin rows load each repository plugin's hooks and Skill on top of the same MCP surface. They changed no
recovery outcome on the retry-later cases. The Claude Code model never invoked the plugin's Skill; the Codex model
read it in every session. The only new behavior was finding 5.
