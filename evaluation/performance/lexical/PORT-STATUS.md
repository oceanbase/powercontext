# Migration status

| Unit | Runtime integration | Local verification | Delivery status |
| --- | --- | --- | --- |
| `analyze_text(str) -> str` | Default entry point directly calls Rust in `powercontext._lexical`; old Python wrapper removed | CPython 3.11–3.14 installed-wheel comparisons: zero mismatches; independent 4,924,563 comparisons: zero mismatches; focused pytest: 447 passed, 44 skipped | Windows root wheel and sdist rebuild checked; GitHub platform matrix pending |

Production source belongs to `src/powercontext/builtin/artifacts/lexical/`. Source builds use Rust 1.95.0, matching gateway. Root wheels include the native module without an opt-in plugin or a separately installed analyzer package. Python support is explicitly 3.11–3.14 pending future Unicode-version support.

Warm installed-default analyzer medians including the bridge: Chinese documents **11.90×**, English documents **3.33×**, repeated short queries **8.24×**. These are analyzer throughput measurements; no whole-server acceleration claim is made.

The Python span/query helpers retain their existing responsibilities. Schema and analyzer projection version are unchanged. Real installed SQLite close/reopen/search smoke passed on 3.11 and 3.14; 3.12/3.13 smoke is blocked by independently reproduced Windows stdlib asyncio initialization. Cross-platform wheel execution, Docker execution, CI and merge/release approval remain separate.

See the [production report](results/production.md), [raw measurement samples](results/production-benchmark.json), and [validation summaries](results/production-validation.json). Historical experimental evidence is indexed separately in [results](results/README.md).
