# Lexical analyzer implementation

The production extension lives under the owning Artifact module, while compatibility corpora and benchmarks live under evaluation. The root Hatchling backend packages it as `powercontext._lexical`; maturin is an internal build dependency, not a separately published runtime dependency. The default search export directly uses the native function.

Version-specific casefold, word and assignment tables preserve CPython 3.11–3.14 Unicode behavior. Rust normalization is pinned to Unicode 16. For older interpreters, characters unassigned in that interpreter split normalization runs, preserving their inert starter behavior. The binding reads the Unicode version once at module initialization; text processing uses Rust without Python analyzer callbacks.

The effective Jieba dictionaries match despite different raw files. Python counts a duplicate B超 row, contributing three to its total frequency. A NUL dictionary entry restores this weight in Rust without a reachable match in Han-only input. HMM remains disabled.

The PyString entry converts surrogate code points to nonword boundaries and releases the GIL for computation. This applies only to the string projection contract. Span-producing helpers retain their Python implementation and offset behavior.

Source builds use a locked Cargo graph and Rust 1.97.1. Stable source-root remapping and deterministic Windows linking preserve the release gate's byte-for-byte wheel content comparison after source-distribution rebuilds. Linux wheel auditing retains bundled shared libraries at their relative path depth when the extension moves into the root package.
