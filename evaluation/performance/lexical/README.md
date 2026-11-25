# Native lexical analyzer verification

This suite verifies and measures the production `powercontext.builtin.artifacts.search.analyze_text(str) -> str` entry point. Its Rust implementation belongs to `src/powercontext/builtin/artifacts/lexical/`; corpora, comparison scripts and measurement evidence belong here.

Normal PowerContext wheels include `powercontext._lexical`. The search module imports its function directly. No plugin, backend setting or Python fallback is required. The retained Python span analyzer provides query/span behavior and an independent output reference. Database projections, analyzer version, query admission and public signatures are unchanged.

## Build and verify

Source builds require Rust 1.97.1, matching gateway, and a platform C toolchain. On Windows select the MSVC toolchain. Hatchling builds the extension through maturin and includes it in the root `cp311-abi3` platform wheel. Installing a compatible wheel requires no compiler. The source distribution includes the hook, locked crate and Unicode data.

```powershell
$env:RUSTUP_TOOLCHAIN = '1.97.1-x86_64-pc-windows-msvc'
uv sync --frozen
uv run --no-sync pytest tests/builtin/artifacts/test_native_lexical.py -q
cargo fmt --manifest-path src/powercontext/builtin/artifacts/lexical/Cargo.toml --all --check
cargo clippy --locked --manifest-path src/powercontext/builtin/artifacts/lexical/Cargo.toml --all-targets -- -D warnings
cargo test --locked --manifest-path src/powercontext/builtin/artifacts/lexical/Cargo.toml
uv build --out-dir output/lexical-production/dist
```

Install the root wheel with the `builtin` extra in a fresh Python environment, then use that environment's interpreter for these commands. `-I` excludes checkout import injection; scripts do not prepend `src` or activate a test backend.

```text
python -I evaluation/performance/lexical/scripts/verify.py --output output/lexical-production/validation.json
python -I scripts/ci_role_smoke.py builtin
python -I evaluation/performance/lexical/scripts/benchmark.py --output output/lexical-production/benchmark.json
```

Run correctness checks on CPython 3.11, 3.12, 3.13 and 3.14. The verifier compares the real default native function with the running interpreter's Python span implementation, covering the frozen corpus, all casefold/decomposition characters, all supported Unicode word-range edges, surrogate strings, seeded mixed inputs and public documents. It also checks native identity and non-string errors. Read `cases.jsonl` using LF boundaries, because Unicode line separators occur inside recorded strings.

## Compatibility inputs

Unicode 14, 15, 15.1 and 16 tables come from the corresponding CPython versions. Regenerate each table with that interpreter using `scripts/generate_lexical_unicode.py`. NFC precedes casefold. Characters unassigned in older versions remain normalization boundaries. Jieba uses the fixed 0.42.1 effective dictionary, Han-only input and HMM disabled; a frequency correction of three preserves the reference total despite its duplicate dictionary row.

Lone surrogates become nonword boundaries during native conversion. This preserves this projection-only contract; it does not provide character offsets. Private tokenizer mutation and custom dictionaries are outside the fixed analyzer contract.

## Measurements

The benchmark compares the retained Python projection with the installed production entry point, including the native call boundary and output allocation. It checks equality before alternating measurement order over nine warm, single-thread rounds. Document loading is outside timing. These measurements describe analyzer throughput, not full HTTP, inference, database or host latency.

See [production results](results/production.md) for installation and timing evidence. The JSON files dated 2026-11-20 and [their report](results/README.md) retain the bounded experiment's original identities and measurements; they do not establish the production build's performance or platform support.
