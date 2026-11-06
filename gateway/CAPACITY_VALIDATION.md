# Gateway capacity validation

Scope: G5-A of [#2464](https://github.com/oceanbase/powercontext/issues/2464), the independent capacity stop-loss stage.
The implemented boundary is startup-confirmed model input, final rendered-body admission, bounded provider-response
buffering, and separate model/fallback concurrency permits. The HTTP capability schema is unchanged.
G5-A acceptance is partial: possible billing is retained only in process-local lifecycle state, and readable
call-evidence integration with #2506 is still required.

## Source and environment

- Base: upstream master `24e0017e` (includes the shared managed-call lifecycle from #2499).
- Implementation: local branch `codex/issue-2464-capacity-budgets`, working tree based on that commit.
- Validation date: 2026-11-06, macOS / Darwin arm64, Rust/Cargo 1.95.0, Python 3.13.15.
- Related open work inspected: #2457 (protection modes), #2470 (native protocol behavior), #2429 (output defaults),
  and #2506 (bounded call evidence). Their unmerged implementation is not included in this branch.

## Executed checks

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
state without claiming complete response receipt. It does not produce a readable `record_call` export in this branch.
The production `BufferedEvidenceSink` has no operator-facing call stream, and these tests do not satisfy the
G5-A requirement that possible billing appear in readable call evidence.
The Python suites exercise the real Core runtime and ASGI authorization paths;
they are not evidence of spawned Core or real-provider acceptance through the new gateway.

No external model calls, paid provider calls, real host integrations, RSS measurements, or production deployment
were performed. Oversized provider response fixtures deliberately use a permissive controlled release verdict to
isolate transport buffering; they do not establish that real Core accepts such response content.

## Remaining issue acceptance

G5-A still requires integration with the bounded call-evidence exporter in #2506. A real gateway-process
regression must read the exported record for a response-buffer overflow and assert one attempt,
`Withheld` terminal, `PossiblySent` sending facts and unknown usage, without content or credentials.
Until that integration is executed, G5-A must not be described as fully accepted. The capacity changes
can be reviewed independently, with the evidence dependency stated in the PR.

G5-B remains open: output-capacity discovery where required; global and local retained-byte accounting for request,
response, replay/SSE and applicable evidence owners; accounting through final owner drop; mixed-size load/RSS
measurements; and cancellation/shutdown recovery under those budgets. These require the G1/G2/G3 configuration
and owner boundaries identified by #2464. The current concurrency/body limits are not a retained-byte ledger or a
process memory ceiling. This stage alone does not close #2464.
