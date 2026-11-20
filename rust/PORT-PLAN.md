# Lexical analyzer Rust pilot

## Contract

Reference: `5b557788c33b57b8ef55a3f3446ee72d693270a4` (same tracked tree as upstream `6763b07661846c19934044184995adf9183c28cc`). Unit: `powercontext.builtin.artifacts.search.analyze_text(str) -> str` and its normalization, segmentation and term encoding. Scope: an experimental extension submitted for review, with no default-backend switch or deployment.

The pilot lives in `rust/lexical-rs`, outside gateway and desktop. The existing Python runtime and root packaging remain the reference. This trial must determine whether a native implementation can preserve output and provide useful speed after crossing Python's extension boundary. It does not claim a production-ready optional dependency or universal Python support.

Preserve NFC-before-casefold, Han ranges, Jieba 0.42.1 dictionary and HMM=False behavior, Python word boundaries, apostrophe normalization, token frequency and order, pcu/pch escaping, UTF-8 length thresholds and SHA-256. Do not change admission, stopword policy, schemas, analyzer version, persistent projections, gateway or HTTP contracts.

## Design and dependencies

Use rust-anything's generated PyO3 scaffold for the real source file, keeping every other export in Python. `src/port.rs` owns the scalar Rust algorithm. Evaluate jieba-rs 0.11.0 (MIT), unicode-normalization 0.1.24 (MIT/Apache-2.0, Unicode 16), and sha2 0.10 (MIT/Apache-2.0); pin resolved dependencies in the crate lock file. Verify the bundled Rust dictionary against the reference by its complete effective word/frequency/tag map. The duplicate B超 row contributes +3 only to Python total weight; restore that mass on a NUL entry outside the Han-only input domain.

For this bounded trial, generate casefold and word classification data from CPython 3.14's Unicode 16 data as versioned compatibility data, with explicit source metadata. Older/newer Unicode versions require separate verification; do not claim the root project's Python 3.11–3.14 support from this trial alone. A small compatibility adapter may convert Python surrogate-containing strings into the equivalent nonword boundaries before native extraction. It must not invoke the original analyzer or catch-and-retry failures.

Build and package a separate experimental wheel; leave Hatchling and the root wheel unchanged. Validate the installed extension in a fresh environment. Actual native activation for existing tests occurs only in a test process, with explicit extension identity and no fallback. Final independent review remains pending; executor checks are not independent acceptance.

## Execution and acceptance

- [x] Read current rules and compare candidate code with the previous analysis. Core behavior unchanged; nearby admission helper changed and stays out of scope.
- [x] Run the related Python baseline: 13 passed (query admission and Topic chunking).
- [x] Run rust-anything analyze on the new Head and bridge only analyze_text.
- [x] Record deterministic boundary cases plus tracked English/Chinese docs using the unchanged Python reference; retain source and dictionary hashes.
- [x] Prove the comparison and forced-native tests fail on incorrect output/unavailable native code before implementing the Rust algorithm.
- [x] Implement the Rust algorithm and meaningful algorithm unit tests; iterate on actual mismatches, preserving the recording.
- [x] Run locked Cargo tests, fmt and clippy, rust-anything build/diff/bench/gate, targeted Python behavior tests with native analyze_text and native identity evidence.
- [x] Build a wheel and install/call it in a fresh environment; record supported platform and Python version precisely.
- [x] Compare warmed release throughput on recorded inputs and larger public docs, including bridge and conversion costs. Report thread count and cold initialization separately.
- [ ] Independent verification of final code, outcomes, limitations and actual snapshot; save decision and status.

The output comparison is exact, with no wildcards, tolerance or ignored order. Expected business errors and unsupported inputs are reported separately. A compatibility mismatch or material regression prevents default integration; a demonstrated blocker is a valid trial conclusion, not a passed migration. Root production files are not changed merely to make a trial pass.

## Evidence

Task-local logs and reports belong in `output/lexical-pilot/`; generated build outputs and environments are ignored; the Unicode compatibility data is reviewed source. Keep reference and candidate hashes with the final report. Baseline environment currently reuses the existing project Python dependencies while explicitly placing this worktree's `src` first on PYTHONPATH. Installed-wheel validation must use a fresh environment without the source package on its import path.
