# Gateway follow-up recall evaluation

The [24 labeled cases](../../../gateway/crates/pc-gateway-app/tests/fixtures/context_disambiguation.json) cover ambiguous follow-ups, specified tasks, task switches, and tool continuations. The Gateway binary tests run both routes with scripted replies and deterministic fixture retrieval.

Run the paid comparison using the Gateway binary and project Python environment:

```sh
uv sync --locked
cargo +1.95.0 build --locked --manifest-path gateway/Cargo.toml -p pc-gateway-app
uv run python tests/e2e/gateway_recall/evaluate.py \
  --route responses --upstream https://api.deepseek.com/responses \
  --model deepseek-flash --deepseek --enforce --runs 3 \
  --rates 0.003 0.15 0.6 --output /tmp/gateway-recall-responses.json
```

Supply the upstream key in `PC_GATEWAY_UPSTREAM_API_KEY`. The example rates are off-peak USD per million cached input, uncached input, and output tokens. Use the provider's rates for your test window. For DeepSeek Chat, use `--route chat/completions --upstream https://api.deepseek.com/chat/completions` and omit `--enforce` to allow the provider-specific thinking field. Both configurations run the actual Core send and release checks. The Responses example also enables Core carrier and privacy enforcement. [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing)

The runner creates a temporary SQLite Core and seeds six Atomic Memories via the public API. It starts one Gateway per arm. Each case gets a unique provider-facing prefix per arm and a fresh Gateway conversation ID per repetition. The first repetition exercises a cold session, and the remaining repetitions send the same prefix to exercise a warm session. Only synthetic test conversations go to the provider. A local Core observer records the actual prepare query, matched memory IDs, final injection, and usage reported at release. Live cost estimates cover calls with known usage at release.

Review the output by case and category. Score the auxiliary query against the fixture's attribution label and preceding conversation. Count unsupported factual assertions separately from an old task's facts incorrectly attached to the new task. Report invalid outputs and the original query sent on fallback separately. The synthetic cases and stable prefix are controlled test inputs. Host latency and cache performance depend on production traffic.
