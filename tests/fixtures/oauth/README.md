# OAuth behavior fixtures

Python and Rust use these synthetic credentials and HTTP responses to test credential ownership, refresh and failure handling. `contract.json` contains 27 project-authored scenarios for read-only Codex CLI credentials and shared-file Grok renewal. [Upstream vectors](upstream/README.md) preserve separate source tests and their provenance.

## Run the tests

From the repository root:

```bash
uv run pytest tests/builtin/inference/test_oauth_contract.py -q
cargo test --manifest-path gateway/Cargo.toml --locked -p pc-gateway-auth
```

These tests use temporary files and controlled HTTP peers. Separate Grok tests spawn workers to verify cross-process locking and persisted rotation. They do not verify live account eligibility, quota or installed CLI compatibility.

## Fixture format

| Field | Meaning |
| --- | --- |
| `schema_version` | The supported fixture format. Runners reject unknown versions, profiles and actions. |
| `profiles` | Credential ownership, allowed HTTPS origin, resource endpoint and token endpoint. Times use Unix seconds. |
| `credentials`, `now` | Initial contents of a temporary credential file and a deterministic clock. A null credential value represents malformed contents. |
| `http` | Resource and token responses. `text` represents invalid JSON, `headers` can provide redirects, and `wait_for_cancel` blocks renewal until cancellation. Undeclared HTTP targets fail the test. |
| `actions` | Ordered `request`, `replace_credentials`, `concurrent_requests` or `cancel_during_refresh` operations. An omitted URL uses the profile's resource endpoint. |
| `outcome` | `success`, `http_error`, `auth_error` or `cancelled`. HTTP errors retain their status; cancellation propagates to the caller. |
| `expect.resource_tokens` | Tokens actually sent to the resource endpoint. Concurrent requests use multiset comparison; other requests retain action order. Codex also sends the current `chatgpt-account-id`. |
| `expect.refresh_forms` | Actual form-encoded token requests, sent without an Authorization header. |
| `expect.store` | `unchanged` preserves the latest file bytes. `updated` requires disk readback of the rotated credentials while preserving unrelated fields and provider entries. JSON layout is not significant. |

## Required behavior

Validate the destination before loading or refreshing credentials, and disable redirects. Grok refreshes within 60 seconds of expiry under the CLI's `auth.json.lock`; it retains the previous refresh token when the response omits a replacement and rejects an explicitly empty value. Codex never exchanges its CLI-owned refresh token; its profile has `refresh_url: null`.

A resource 401 does not trigger renewal or model replay. Failed, malformed or cancelled renewals leave the store unchanged and send no resource request. Errors must not expose credentials, file paths or the fixture's `synthetic-secret-error` value. Cancellation tests also verify that no background write or send occurs and that another caller can acquire the lock.

The Grok profile targets republic's `api.x.ai/v1` OAuth path. It does not cover Magpie's `cli-chat-proxy.grok.com` subscription bridge or its persistent Claude sessions.
