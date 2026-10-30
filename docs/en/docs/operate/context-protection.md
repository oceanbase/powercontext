---
title: Context reuse and final admission
description: Reuse exact prepared Atomic Memory and check final requests and responses with current authorization.
---

# Context reuse and final admission

Prepare context once, retain its short-lived reuse proof, and validate the exact bytes before each model request and complete response delivery. Configure an enforcing target using [Inference privacy protection](inference-protection.md) before using the public admission API. The caller owns model execution and delivery; a check does not execute a model or authorize spending.

## Prepare once, validate each use

The default `POST /v1/context/prepare` response remains `schema`, `status`, `content`, and `content_bytes`.
The existing `explain` option still controls optional diagnostics. Add `include_reuse: true` for:

```json
{
  "schema": "powercontext.prepared-context-with-reuse.v1",
  "context": {
    "schema": "powercontext.prepared-context.v1",
    "status": "ready",
    "content": "<complete prepared historical context>",
    "content_bytes": 38
  },
  "reuse": {
    "schema": "powercontext.context-reuse.v1",
    "constraints": {
      "preserve": ["content", "citations", "trust", "order"],
      "refresh_on": ["permission_change", "source_ineligible", "context_boundary"]
    },
    "expires_at": "2026-10-26T12:05:00Z",
    "binding": "<opaque encrypted binding>"
  }
}
```

The angle-bracket values, byte count, and time above are documentation examples, not a runtime masking format.
Use the exact returned content and binding. The Python
client returns either `PreparedContext` or `PreparedContextWithReuse`; narrow that union before reading its fields.
In process, use `runtime.context.for_scope(scope_id).prepare(request)`. Its `validate_reuse` operation checks the
retained proof without retrieval or embedding; embedded applications supply their own current authorization context.

The first reuse profile supports a single Scope and selected Atomic Memory only, without context references or code.
It records selected current revisions, lifecycle versions, and complete bounded historical dependencies. Exact
historical ancestors of a merge remain valid evidence when their owning domain permits them. Revised or forgotten
roots, revoked access, changed reference policy, expired bindings, and ineligible Sources stop reuse.
Other selected families are explicitly rejected for this option; ordinary preparation remains supported.

Bindings are authenticated and encrypted, expire after five minutes, and are limited to 64 dependencies and eight
ancestry levels. They do not grant access. Server bindings use the existing signing-secret material with a separate
cryptographic purpose and persistent Server identity; processes sharing that deployment must share the secret.
An embedded Runtime's default key is ephemeral. There is no stored prompt or response cache.

## Check the actual final payload

Call `POST /v1/context/check`, operation `check_context`, at two distinct points:

1. `before_send`: after native provider rendering and cache settings, before writing any model request.
2. `before_release`: after collecting the complete raw response, before exposing content, tool arguments, or errors.

Both use root `scope_id`, current authentication/delegation, and current Scope/resource authorization.
`scope.read` does not grant model spending or tool execution. Request fields cannot turn off deployment policy.

The versioned JSON carrier contains the original JSON text, not a reconstructed object:

```json
{
  "scope_id": "<existing Scope>",
  "phase": "before_send",
  "target_profile": "coding",
  "payload": {
    "schema": "powercontext.model-json-payload.v1",
    "headers": {"content-type": "application/json"},
    "body": "{\"model\":\"approved-model\",\"max_tokens\":1024,\"messages\":[{\"role\":\"user\",\"content\":\"Hello\"}]}"
  },
  "prepared_contexts": []
}
```

For each retained prepared block, include its binding and a `powercontext.model-json-text-location.v1` location:
JSON pointer, `start_byte`, and `end_byte`. Offsets address decoded UTF-8 text with half-open bounds. The whole exact
block must remain in a supported user-message text position, preserving its historical-data trust wrapper and order.
System/developer promotion, changed text, overlapping/reordered locations, and unbound prepared markers are rejected.
A target requiring prepared context cannot silently downgrade to a pure check.

Admission does not redact text or restore placeholders. Replacing a name, path, address, or other value inside
a prepared block changes its content and invalidates its exact reuse proof; even a reversible replacement is not
the prepared value. A gateway must not mask an approved request or restore values into an approved response and
reuse that approval for the changed bytes. A changed payload needs its own admission; this API supplies no
placeholder mapping or authorization to reverse a privacy transformation.

Only `allow` with `coverage.complete=true` permits the corresponding action. `block` and `withhold` return HTTP
200 because a decision was made, but neither permits execution. Invalid carriers/locators return 422; authorization
failure returns 401/403; an oversized payload returns 413. If the required check is unavailable, do not send or release.
The public operation withholds when enforcing is not configured.

An allowed send returns a payload-, target-, policy-, identity-, and Scope-bound permit. Its 60-second send deadline
is separate from its five-minute response-correlation deadline. Pass it as `request_binding` to
`before_release`, with the complete response carrier and `completion: "complete"`.
Current policy, identity, and retained context dependencies are checked again. A release permit cannot authorize a
new request, and correlation never extends the send deadline. These permits do not provide exactly-once execution.

The public carrier currently supports complete JSON. The local protected transport also supports OpenAI Chat SSE
with a single choice and explicit `allow_streaming=true`. Other streaming profiles are withheld. It buffers the
complete stream before release with byte, timeout, and concurrency limits. Do not wrap an SSE
summary in the JSON carrier and call it a complete original response. Strict buffering delays first content.

HTTP over a Unix domain socket and remote HTTP(S) use the same DTOs. UDS does not replace bearer authentication.
The reference `examples/context_protection/external_executor.py` shows how an external executor uses these decisions
to control one exact JSON request and response, including byte limits, cancellation, deadlines, and local binding correlation.
The execution adapter must preserve the approved bytes, honor deadlines, stop on refusal, propagate cancellation,
and account for uncertain/failed sends. No concrete third-party gateway plugin or Rust gateway is installed by this
feature. The executor must retain each call's corresponding request binding locally; stateless Core cannot prove
which approved request an arbitrary external executor actually sent. An opaque upstream gateway limits protection to the observed endpoint; it cannot prove hidden routing,
retention, account/region isolation, or internal retries.

## Boundaries and cost

The caller retains and refreshes reuse metadata. Reusing a prepared value can avoid repeated retrieval and assembly within its validity period; it does not create a server-side context cache or a provider KV cache. Compare repeated preparation and exact reuse against an existing once-prepared baseline before claiming task-level savings. The API verifies current authorization and exact payload identity, but cannot prove that an external executor actually sent or released the approved bytes.

An allowed response is not a guarantee that the model understood its evidence or chose the correct action.
Literal template markers remain ordinary content; the API neither invents missing values nor certifies that they
are executable arguments. The application still validates the proposed action, target, and authorization before
running a tool or persisting a generated fact. Restoring a real path after a model chose the wrong operation
would not make that operation correct.
