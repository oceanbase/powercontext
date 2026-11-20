# PowerContext lexical analyzer pilot

This experimental extension implements only `analyze_text(str) -> str`. It does not change the PowerContext default runtime, database projections, query admission, offsets, gateway or root Python packaging. It is a local compatibility and performance trial.

The tested platform is Windows x64 with CPython 3.14.3 and Unicode 16.0.0. The wheel's `Requires-Python` is restricted to 3.14. Other Python versions, operating systems and free-threaded interpreters have not been validated. The generated ABI tag alone is not a support claim.

## Compatibility

- NFC precedes casefold. Casefold and Python word-character tables are exported from Unicode16; the Rust normalization crate is pinned to the same Unicode version.
- Only the explicit Han ranges are passed to Jieba with HMM disabled. The effective dictionaries of jieba0.42.1 and jieba-rs0.11.0 are compared before exporting data. Python's duplicate `B超` row adds 3 to its total frequency; a NUL entry outside the Han input domain restores that weight without adding a reachable match.
- Word order, duplicates, apostrophes, reserved prefixes, byte-length thresholds and SHA256 terms follow the reference.
- The PyString entry converts lone surrogates to nonword boundaries before Rust processing. This adaptation is valid only for this string-output contract, which discards offsets. rust-anything `refresh` overwrites the adapted bindings and requires reapplying and validating this change.
- The extension calls no original Python analyzer and has no fallback. It is selected only in explicit probe/test processes.

## Reproduce

Run from the repository root with a CPython 3.14 environment containing the reference dependencies, Jieba0.42.1, pytest, maturin and rust-anything. On Windows select an MSVC Rust toolchain at least 1.97. The tool's Python driver selects `python3` before `python`; ensure these resolve to the intended environment.

This crate's minimum Rust version is 1.97; the measured Windows build used 1.97.1 MSVC. It is independent of the gateway workspace, whose CI pins Rust 1.95.0 and cannot build this crate. Existing CI does not compile the pilot. A future pilot CI job must select its own toolchain rather than inherit the gateway pin. For the measured Windows configuration, explicitly select it before running the commands below:

```powershell
$env:RUSTUP_TOOLCHAIN = '1.97.1-x86_64-pc-windows-msvc'
```

```text
cargo fetch --locked --manifest-path rust/lexical-rs/Cargo.toml
python rust/lexical-rs/scripts/prepare.py
rust-anything record rust/lexical-rs --count 200 --inputs output/lexical-pilot/inputs.jsonl
cargo test --locked --manifest-path rust/lexical-rs/Cargo.toml
rust-anything build rust/lexical-rs
python rust/lexical-rs/scripts/verify.py
rust-anything diff rust/lexical-rs
rust-anything bench rust/lexical-rs
rust-anything gate rust/lexical-rs
python rust/lexical-rs/scripts/benchmark.py
python -m maturin build --locked --release --manifest-path rust/lexical-rs/Cargo.toml --out output/lexical-pilot/wheels
```

The tool manifest resolves the reference root relative to this crate. Ordinary recording does not carry surrogates; the strict recording uses codepoint arrays for them. Strict error checks supplement the tool's permissive exception comparison.

The committed `cases.jsonl` contains raw Unicode line separators inside JSON strings. Read records using LF boundaries, not Python `str.splitlines()`; see the [record-reading example](results/README.md#reading-the-recorded-cases).

To run existing tests, add the checkout `src` and this crate's `scripts` to `PYTHONPATH`, then run pytest with `-p native_pytest` and selected paths. The plugin activates native code only in that process and fails if no native calls were observed. It prints the native path/hash. Run `verify.py --installed` in a fresh environment after installing the wheel; importing the extension must not depend on the source package or reference dependencies.

## Evidence and limitations

See `../PORT-PLAN.md`, `../DECISIONS.md`, `../PORT-STATUS.md` and [the published results](results/README.md). Measurement samples and reference corpus paths are checked in under `results/`. Reproduction generates strict recordings, raw tool output, negative controls and native identities in the repository's ignored `output/lexical-pilot/` directory.

Data describes the fixed default analyzer only; private tokenizer mutation and custom dictionaries are outside the trial. Full Python/platform coverage, the complete Server request chain, root SDK wheel integration and release readiness are not established by this pilot.
