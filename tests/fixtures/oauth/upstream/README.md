# Upstream subscription test vectors

These fixtures preserve inputs and expected assertions from upstream source tests. Each `source` record pins the repository, commit, file, test and license. Keep the original values when changing adapters; expected results must remain independent of the PowerContext implementation.

## Sources and adaptations

| Fixture or record | Pinned source | Adaptation |
| --- | --- | --- |
| `republic-grok.json` | [republic Grok tests, `2fa76b5407ba1cf035293121bfab9e782ea199f3`](https://github.com/bubbuild/republic/blob/2fa76b5407ba1cf035293121bfab9e782ea199f3/tests/test_grok.py) | Six scenarios preserve refresh forms, principal fields, token rotation and failure responses. Successive refreshes use separate scenarios and a fixed clock; the synchronous Authlib client is not migrated. |
| `provider-vectors.json`: `codex`, `codex_tool_roundtrip` | [republic Codex tests, same commit](https://github.com/bubbuild/republic/blob/2fa76b5407ba1cf035293121bfab9e782ea199f3/tests/test_codex.py) | Original SSE deltas, empty terminal output, usage, reasoning and tool items remain intact. Python consumes text/usage through the native SDK; Rust also consumes the tool round trip. `adapter_credentials` and `adapter_event_defaults` add synthetic SDK-required metadata separately. |
| `provider-vectors.json`: `copilot` | [republic Copilot tests, same commit](https://github.com/bubbuild/republic/blob/2fa76b5407ba1cf035293121bfab9e782ea199f3/tests/test_github_copilot.py) | The native provider receives the original synthetic token, messages and response. `adapter_response_defaults` and `adapter_message_defaults` supply SDK-required metadata. CLI login and SDK version headers are outside this case. |
| `provider-vectors.json`: `gemini` | [Goose Gemini tests, `0edf2bdc8161d06f79daf639c148bf3559e26cf0`](https://github.com/block/goose/blob/0edf2bdc8161d06f79daf639c148bf3559e26cf0/crates/goose/src/providers/gemini_oauth.rs) | Request wrapping and response unwrapping retain the original JSON. The HTTP test removes the SSE `data:` framing; Python's adapter remains non-streaming. The random prompt ID is outside the expected assertions. |
| `provider-vectors.json`: `claude` | [Goose Claude tests, same commit](https://github.com/block/goose/blob/0edf2bdc8161d06f79daf639c148bf3559e26cf0/crates/goose/src/providers/claude_code.rs) | Usage values and error events retain their original values. `adapter_wrapper` is a synthetic print-mode envelope. A test executable emits that envelope; context and generic CLI errors share PowerContext's static error result. Persistent sessions, MCP and rewind are outside this adapter. |
| `provider-vectors.json`: `qwen` | [Qwen Code OAuth tests, `37e29fd65681595d6dc66fe629bdb4b3f0bb5073`](https://github.com/QwenLM/qwen-code/blob/37e29fd65681595d6dc66fe629bdb4b3f0bb5073/packages/core/src/qwen/subscription-oauth-client.test.ts) | Two rotations, incomplete tokens and an invalid device interval retain their original inputs and assertions. Expired temporary files drive the same grants; revoke is outside these cases. |
| `provider-vectors.json`: `kimi` | [Kimi Code OAuth tests, `322d0363f4c5cf332034ff81759653bf341f165c`](https://github.com/MoonshotAI/kimi-code/blob/322d0363f4c5cf332034ff81759653bf341f165c/packages/oauth/test/oauth.test.ts) | Device and token responses retain their original values inside PowerContext's credential format. Python consumes refresh and malformed-token cases; project-authored tests cover its device-login flow. |

## Runners

Python uses `tests/builtin/inference/test_oauth_contract.py` for Grok scenarios and `tests/builtin/inference/test_upstream_subscription_vectors.py` for the other records. Rust reads the same files from its authentication and transport tests. Run commands are in the [parent README](../README.md).

The original 27 scenarios in `contract.json`, process and cancellation tests, destination checks and Core admission regressions are project-authored coverage. All vectors use synthetic data; passing them does not establish live subscription access.

Copyright notices and license terms are in [LICENSES/oauth.md](../../../../LICENSES/oauth.md).
