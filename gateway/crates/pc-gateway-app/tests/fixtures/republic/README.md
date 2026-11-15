# Gateway protocol replay fixtures

These fixtures come from [bubbuild/republic](https://github.com/bubbuild/republic/tree/2fa76b5407ba1cf035293121bfab9e782ea199f3/tests/test_provider_golden), pinned to commit `2fa76b5407ba1cf035293121bfab9e782ea199f3`. Response bodies in `cases/` are preserved byte for byte.

Upstream sources:

- Fantasy: `b0fadc9ae2e7da81a5d0262563d3ced9b594bd75`, OpenAI Chat and Responses recordings under `providertests/testdata/` for text, tool calls, and parallel tool calls. Chat fixtures include streaming recordings.
- Goose: `9560429ff982bbfecec5094963e5f34696b63a1c`, constructed protocol cases under `crates/goose-provider-types/src/formats/` for Chat length truncation and empty tool arguments, and Responses refusals and tool call identity.

Original licenses and notices are retained in `LICENSE.fantasy`, `NOTICE.fantasy`, and `LICENSE.goose`.

`gateway_behaviors.rs` exercises the gateway's public contract through a real Rust gateway subprocess and scripted Core and upstream HTTP peers. Requests reconstruct the source scenarios and add gateway behavior checks. Returned tool calls and caller-supplied tool results become input to the next request. Assertions compare complete upstream requests, the exact bytes submitted to send and release checks, and client-visible bodies, tool arguments, refusals, usage, and SSE events.

The gateway's Responses contract uses non-streaming upstream requests and generates client SSE from an approved JSON response. Native Responses SSE recordings provide the direct baseline for the upstream compliance runner in `tests/e2e/gateway_tck.py`. Unsupported Anthropic and Gemini protocols are excluded. Scripted Core verdicts establish proxy behavior and verdict enforcement; they do not establish real Core policy coverage, provider compatibility, or host compatibility.

Gateway CI includes these tests in its workspace test command. To run them directly:

```sh
cargo test --locked --manifest-path gateway/Cargo.toml -p pc-gateway-app --test gateway_behaviors
```
