# Decisions — migration harness (`PC-RUST-2737`)

One decision per row, with the evidence that made it and what it constrains. Decisions that would
change product behaviour are explicitly out of scope.

| # | Decision | Why | Constraint / consequence |
|---|---|---|---|
| D1 | Record **codepoint arrays** for `str`, not JSON text | JSON text cannot guarantee an explicit surrogate pair survives a reader round-trip; the contract's worked example is exactly that distinction | Records are larger; the comparator compares codepoints, so a fingerprint difference is observable, not inferred |
| D2 | Closed, tagged type set with `UnsupportedType` | The contract forbids `repr` as a stand-in for losslessness and forbids building a general framework with no consumer | `dict`, `set`, `object`, `complex` are rejected today; `Decimal`/`Fraction` are only added if a real RRF consumer needs them |
| D3 | `bool` before `int`, `int` as decimal string, `tuple` vs `list` distinct | `isinstance(True, int)` and JSON's number model silently merge these | Arbitrary-precision integers and `1` vs `True` differences are detectable |
| D4 | `float` stored as text with explicit `nan`/`inf`/`-inf`/`-0` | `json` cannot emit `NaN` under `allow_nan=False`, and `-0.0` is a distinct behavioural input | Evidence files never contain non-standard JSON |
| D5 | Structured exception records (`module`, `qualname`, `args`) | An exception identity is part of the contract; matching message strings is brittle | A candidate raising a different type fails even with an identical message |
| D6 | Lines split on `\n` only | U+0085/U+2028/U+2029 make `splitlines()` cut a record into pieces | Readers must not use `splitlines()`; the self-test asserts the raw file has no literal separators |
| D7 | Recording refuses to run after a native/compiled module was imported | The contract forbids recording a golden with a substituted import | `ensure_reference_only()` checks the module suffix and the already-imported `powercontext.artifacts.search` |
| D8 | The selector set is closed and non-empty; unknown selectors are errors | "Silently skip and report green" is the failure mode the contract names | Missing native is `ERROR`, not `PASS`; a `--limit` below the selection size is refused instead of truncating |
| D9 | `replay` reports `candidate.native` from the module that actually answered | A green run against the Python reference says nothing about native | `native: false` is recorded in every replay result, so the state is auditable |
| D10 | Negative controls use *observation* mutation, not input mutation | An input-metadata mutation fails before any observation is compared, so it would prove nothing (this exact review point was raised on the sibling task #2739) | Controls assert the mismatch path starts at `cases[i].observations...` |
| D11 | "wrong order" swaps observations between two cases instead of reordering records | Reordering self-describing records is a legitimate input order, not an error (asserted by a test) | The control proves pairing is by record content |
| D12 | Missing-native control uses an unknown selector | There is no native module to remove yet (#2735 OPEN) | It proves fail-closed behaviour; once a native component exists, the control is replaced by removing the built artifact |
| D13 | Reference evidence is recorded on CPython 3.11.14 / UCD 14.0.0 | It is the interpreter this checkout's `uv` resolves; the contract requires real interpreter identity rather than inference | The manifest records version/UCD/source hash/Jieba dict hash, so a replay on another interpreter is comparable rather than assumed equal |
| D14 | Committed evidence is limited to one reference recording + replay + controls + snapshot | The contract asks for reproducible evidence, not a matrix of machines | Other interpreters are recorded when the product stage needs them; no fabricated rows |
| D15 | `snapshot.py` records a canonical `snapshot_id` | The contract defines snapshot identity over status/diffs/untracked bytes | Paths are repository-relative and `.venv` is excluded, so evidence is portable and contains no home directory |
| D16 | Product replay is `BLOCKED`, not simulated | No native component exists until #2735 merges | No stacked implementation branch, no guessed `R/F/E`, no performance claim |
