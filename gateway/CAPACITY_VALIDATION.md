# Gateway capacity validation

Scope: G5-A of [#2464](https://github.com/oceanbase/powercontext/issues/2464), the independent capacity stop-loss stage.
The implemented boundary is startup-confirmed model input, final rendered-body admission, bounded provider-response
buffering, and separate model/fallback concurrency permits. The HTTP capability schema is unchanged.
Readable call-evidence integration with #2506 is validated on the combined implementation; response overflow
retains possible billing in both lifecycle state and the production JSONL stream.
See [Gateway evidence validation](EVIDENCE_VALIDATION.md) for the combined commit, platform, commands and limits.

## Capacity-only source and environment

- Base: upstream master `24e0017e` (includes the shared managed-call lifecycle from #2499).
- Implementation: local branch `codex/issue-2464-capacity-budgets`, working tree based on that commit.
- Validation date: 2026-11-06, macOS / Darwin arm64, Rust/Cargo 1.95.0, Python 3.13.15.
- Related open work inspected: #2457 (protection modes), #2470 (native protocol behavior), #2429 (output defaults),
  and #2506 (bounded call evidence). Their unmerged implementation is not included in this branch.

## Capacity-only checks

Run from the repository root with Cargo 1.95.0 available on PATH:

```bash
cargo fmt --manifest-path gateway/Cargo.toml --all --check
cargo clippy --locked --manifest-path gateway/Cargo.toml --all-targets -- -D warnings
cargo test --locked --manifest-path gateway/Cargo.toml --workspace -- --nocapture
uv run pytest tests/builtin/runtime/test_context_check.py tests/e2e/test_context_check_authorization.py tests/test_capabilities.py -q
make check
make docs-test
```

All checks passed. Cargo reports 153 passing tests; four existing live-Core tests return early with an explicit
`SKIP` because no real Core is listening on `127.0.0.1:8000`. Thus 149 tests executed their assertions, including
eleven new gateway process/socket scenarios and two executor regressions. The focused Python suites passed 40 tests.
`make check` passed lock consistency, hooks, typing, integration guidance and the 61 integration-manifest checks.
`make docs-test` passed website lint, tests, build and static-export verification of 1,082 public pages and their
internal links. The build emitted two dependency CSS `::highlight` warnings and completed successfully.

## Evidence boundaries

`crates/pc-gateway-app/tests/capacity_process.rs` spawns the compiled gateway binary with isolated environment
settings and controlled HTTP Core/provider receivers. Its combined-deployment cases use a real Unix socket and
HTTP/1.1 forwarding. The receivers validate received bytes and counts; they do not implement Core's privacy policy
or cryptographic bindings.

| Scenario | Observed result |
| --- | --- |
| Discovery HTTP failure, missing capacity, incomplete required capability DTO, zero/negative/fraction/string capacity, unsupported check/payload versions | Model route returns 503; no provider sends |
| Zero deployment byte limit with a valid Core capability | Model route returns 503 with `input_capacity_disabled`, not a Core discovery error; no provider sends |
| Core/deployment intersection, known Content-Length and chunked UTF-8 requests | Initial overflow returns 413; a later small request succeeds |
| Exact rendered UTF-8/escaped JSON boundary | Exact wire-byte limit succeeds; one byte less rejects before provider send |
| Renderer expansion, prepared context, Responses steering | Final overflow returns 403 with `request_payload_too_large`; no provider sends |
| Configuration raised above 1 MiB | Compatibility ceiling still rejects oversized model input |
| Core check limit lowered after startup | The next Core check rejects before any additional model send |
| Fixed-length and chunked provider responses at 1 MiB / 1 MiB + 1 | Buffer boundary is inclusive; overflow returns 409, no response prefix, no automatic resend; later small response succeeds |
| Output larger than the input-only capability | Response buffer uses its own ceiling and still requires a release check |
| UDS fallback request larger than the model allowance | Fallback retains its separate deployment byte limit |
| Open UDS SSE with concurrency set to one | Another fallback call is rejected; model call and health probe succeed; dropping SSE allows later fallback work |
| Known / unknown-length fallback overflow | Known length returns 413 before forwarding; unknown length returns 502 and the receiver observes an incomplete body; the next small request succeeds |

The executor regressions check prepared-context replay and the lifecycle observation on response overflow:
`PossiblySent` plus `Withheld`, with one provider attempt. This preserves possible billing in process-local
state without claiming complete response receipt. The combined implementation also checks the same facts against
readable call/attempt records, including degraded passthrough and partial-response status/first-byte timing.
Production uses the bounded `JsonEvidenceSink`; `BufferedEvidenceSink` is retained for tests/demos.
The Python suites exercise the real Core runtime and ASGI authorization paths;
they are not evidence of spawned Core or real-provider acceptance through the new gateway.

No external model calls, paid provider calls, real host integrations, RSS measurements, or production deployment
were performed. Oversized provider response fixtures deliberately use a permissive controlled release verdict to
isolate transport buffering; they do not establish that real Core accepts such response content.

## Remaining issue acceptance

The combined implementation at `b1f03eb59cc9327e9c1e8ad3df60a2fbcf914713` includes master
`b8b189091e7a412300cf1292531208a8ead0e325`. On 2026-11-07, the Gateway workspace reported 185 passing
cases, including the capacity and evidence subprocess tests; four live-Core cases returned early, so 181
behavior tests exercised their assertions. Fmt, clippy, repository checks and the documentation build passed.
The real Gateway process regression reads exported JSONL for fixed-length and chunked response-buffer overflow:
one `buffer_limit_exceeded` attempt, `withheld` terminal, `possibly_sent`, unknown usage, retained observed
status/first-byte timing, no content or credentials, no delivery prefix and no resend. This satisfies the
G5-A readable-evidence integration requirement with local controlled Core/provider HTTP fixtures; it does not
establish real Core, paid provider or native host acceptance.

G5-B remains open: output-capacity discovery where required; global and local retained-byte accounting for request,
response, replay/SSE and applicable evidence owners; accounting through final owner drop; mixed-size load/RSS
measurements; and cancellation/shutdown recovery under those budgets. These require the G1/G2/G3 configuration
and owner boundaries identified by #2464. The current concurrency/body limits are not a retained-byte ledger or a
process memory ceiling. This stage alone does not close #2464.
