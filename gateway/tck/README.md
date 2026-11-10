# Gateway protocol tests

`make gateway-tck` runs unmodified upstream tests against a controlled upstream directly, then through the built Rust gateway. A failing direct baseline stops that suite before gateway results are attributed to the proxy. Any suite failure makes the command exit nonzero. Gateway CI runs this command's steps and uploads its reports.

The source commits and selected test IDs are pinned in [upstreams.json](upstreams.json):

| Suite | Selected coverage |
| --- | --- |
| [OpenAI compatibility tester](https://github.com/beranekio/openai-compatibility-tester/tree/c8b48c263883e06109b9bdffcda1c0a90373dfa9) | 16 checks using the official OpenAI Go SDK: Chat/Responses text, streaming, tool calls, structured output, reasoning, Chat stream usage, logprobs, multi-turn messages, and invalid-model errors. |
| [Open Responses compliance](https://github.com/openresponses/openresponses/tree/dce9ddb91921427c2ba06901c7dfaf0cc7056799) | Response schema, SSE event schemas and termination, system messages, function calls, and conversation input. |

These are scoped compatibility checks, not full OpenAI or Open Responses certification. WebSockets, compaction, provider-managed stored responses, and multimodal inference are outside this gateway's tested text/tool profile. No upstream assertions are changed or marked as expected failures.

## Run

Install Node 22+, npm, Go 1.26+, and uv, then run from the repository root:

```sh
make gateway-tck
```

Sources are fetched by immutable commit into `.cache/gateway-tck`; Go dependencies use the upstream `go.sum` with `-mod=readonly`. Bun and Zod versions are locked in this directory's `package-lock.json`. Reports and process logs are written to `.artifacts/gateway-tck`.

To select a Go executable or a different report directory:

```sh
make gateway-tck ARGS="--go /path/to/go --output /tmp/gateway-tck"
```

## Execution boundary

The SDK suite uses its own standalone mock server. The Open Responses suite receives recorded JSON/SSE provider bodies. The gateway, HTTP transport, request rendering, and client SDK/schema validators are real; Core verdicts are scripted to isolate protocol correctness. The tests do not establish real Core authorization or binding correctness, model quality, provider compatibility, or incremental release latency.

Focused `gateway_behaviors` tests also verify exact request/response preservation, tool continuations, blocked send/release behavior, and reasoning/citation events using independent protocol fixtures. They run in the ordinary Rust workspace test command.
