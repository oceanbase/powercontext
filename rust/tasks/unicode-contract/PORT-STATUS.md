# Status

Contract `PC-RUST-2739` / `2026-11-22.1`. Research state: **complete for the executable scope**.
Product migration / default integration: **NOT_RUN / N/A**.

| Item | Value |
|---|---|
| Reference master | `ca935e10daa68e5302cc0a5b1392618f49265932` |
| Fixed candidate (#2735, read-only) | `a774fd0b8b2ac1729f5dd9121c34e968dd980905` (OPEN) |
| Platform | Windows 10 x64 |

## Matrix (real execution)

| Interpreter | UCD | Recorded cases | Existing tests | Generator | Negative controls |
|---|---|---|---|---|---|
| CPython 3.11.14 | 14.0.0 | 26 PASS | 13 passed | PASS (semantic+LF) | PASS |
| CPython 3.12.16 | 15.0.0 | 26 PASS | 13 passed | PASS | PASS |
| CPython 3.13.3 | 15.1.0 | 26 PASS | 13 passed | PASS | PASS |
| CPython 3.14 | 16.0.0 | BLOCKED (no interpreter; not downloaded) | BLOCKED | BLOCKED | BLOCKED |

Reproduce: `python rust/unicode-contract/probes/run_matrix.py …` (see `rust/unicode-contract/REPORT.md`).
Full matrix log: `rust/unicode-contract/evidence/matrix.json` — 23 commands, `unexpected_nonzero=0`.

## Findings

- 26/26 fixtures identical on 3.12 and 3.13; **1 differs on 3.11**: `ucd_1e4ec_combining`, because
  U+1E4EC is `Cn` under UCD 14.0.0 and `Mn`/`combining=232` from UCD 15.0.0. NFC composes the mark
  cluster on 3.12+ (`a`+U+1E4EC+U+0300 → `à`+U+1E4EC) but not on 3.11, which changes `analyze_text`
  output and term offsets.
- Explicit surrogate pairs are never merged with real scalars, but the observable difference is
  boundary-specific: codepoint counts differ (4 vs 3) and `utf-8 strict` raises while
  `surrogatepass` yields `61eda0bdedb88062` vs `f09f9880`. `analyze_text` returns the **same** value
  for both (`pcu61 pcu62` with context, empty when bare), so the distinction is only visible in
  `analyze_text_with_spans` / `lexical_terms_with_spans` offsets (`b` at `(3,4)` vs `(2,3)`) and in
  the chunk outcome (pair raises `ValidationError`, scalar accepted).
- `str`-level analysis tolerates lone surrogates; the validated chunk boundary raises
  `ValidationError (string_unicode)`. Two distinct contracts, recorded separately.
- Candidate tables are 337,951 B total and regenerate in ~0.2 s per interpreter; raw bytes differ
  only by CRLF vs LF, semantic JSON and LF-normalized bytes match exactly.

## Not run / blocked

- CPython 3.14 / UCD 16 — `BLOCKED`; no interpreter present, download not permitted by the contract.
- Any native/Rust path — `NOT_RUN`; no extension built or loaded, so R/F/E = `N/A`.
- GIL and free-threaded interpreters, other platforms, wheels, default backend selection,
  independent review — `NOT_RUN`.
