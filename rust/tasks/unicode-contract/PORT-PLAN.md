# Unicode compatibility investigation plan

Contract: `2026-11-22.1`; issue: #2739. This is a read-only research deliverable, not a product migration or default-backend change.

## Baseline and ownership

- Original base: `2f4997d9`; synchronized reference: upstream/master `ca935e10daa68e5302cc0a5b1392618f49265932`.
- Fixed read-only candidate: PR #2735 Head `a774fd0b8b2ac1729f5dd9121c34e968dd980905` (OPEN when checked). The older issue SHA is historical only.
- Platform: Windows x64. Discovered interpreters: CPython 3.11.14, 3.12.16 and 3.13.3; CPython 3.14 is BLOCKED unless an existing interpreter is found. No interpreter will be downloaded for this investigation.
- Writes are limited to `rust/unicode-contract/**` and this task's three files. Production lexical code, #2735, root configuration/locks and shared PORT files remain untouched.

## Workload and boundary

The observed boundary is `search.py`: NFC then casefold, Han/non-Han runs, Python `re` word matching, Jieba `HMM=False`, term encoding, and normalized-codepoint spans. `topic_memory/chunking.py` separately owns original-input codepoint offsets. Synthetic fixtures are compatibility probes only; they do not represent production load or establish a performance benefit. No native library is built or loaded, so native behavior, performance and R/F/E are N/A.

## Execution slices

1. **A-01/A-03/A-04/A-05:** run typed fixtures through the real Python public entry points on every available interpreter. Record exact return types, values, structured exceptions, normalized codepoint spans and separately derived UTF-8 byte offsets. Cover NFC-before-casefold, ǰ/İ/ß, combining marks, Han, non-ASCII numbers, apostrophes, empty/whitespace/None/0/False/default profiles, str subclasses, lone surrogates, explicit surrogate pairs, real scalar values, emoji and CRLF.
2. **A-02/A-08:** inventory the fixed generator, lockfile and UCD14/15/15.1/16 tables with SHA-256 and sizes. Execute the fixed generator only in a disposable directory for available interpreters; compare semantic JSON and LF-normalized bytes without changing #2735. Treat UCD16 table inspection as read-only evidence, not CPython 3.14 execution. Inspect public crate metadata without adding dependencies.
3. **A-07:** in a disposable copy, deliberately merge an explicit surrogate pair and supply a wrong UCD expectation. Require the targeted assertions to fail, then verify the reference fixture hashes are unchanged.
4. **Existing tests:** run `tests/builtin/artifacts/test_text_search_admission.py` and `tests/builtin/artifacts/topic_memory/test_chunking.py` with `-q -rs` in locked, temporary per-interpreter environments. Record commands, exit codes, test/skip counts and `powercontext.__file__`; never alter golden data to obtain green results.
5. **A-10:** report table bytes/counts and one generator wall-time sample per available UCD. These are maintenance-cost measurements, not native or end-to-end benchmarks. Compare versioned tables, per-interpreter generation and explicit support rejection; leave the product choice to maintainers.

A-06 and A-09 are N/A by scope: no product configuration, concurrency, cancellation or supervision is added. GIL release, free-threaded CPython, native wheels, other operating systems, default integration, CI and deployment remain unverified.

## Executed result

All three available interpreters completed every step with exit 0 (3.11/3.12/3.13), CPython 3.14
stayed `BLOCKED` (no interpreter, no download). 26 lossless fixtures per interpreter; 13 existing
tests passed per interpreter; the fixed generator reproduced all tables semantically and on
LF-normalized bytes; negative controls produce target assertion failures in both directions.
One behavioral difference was found: `ucd_1e4ec_combining` diverges on CPython 3.11 because
U+1E4EC is unassigned under UCD 14.0.0. See `rust/unicode-contract/REPORT.md`.

## Evidence and acceptance

`README.md` will give copyable commands. Lossless fixtures will encode strings as codepoint arrays with explicit type tags; UTF-8 will never be used to silently combine surrogate pairs. Results will distinguish real CPython execution, temporary-generator/table comparison, read-only metadata, BLOCKED and NOT_RUN. A manifest will bind source, fixture, command and result hashes using the issue's canonical JSON/SHA-256 rule; the post-commit clean-tree snapshot will be published with the PR evidence.

The independent reviewer gave opening `CONTINUE`. First-slice and final evidence require further independent checkpoints. Completion of this research does not imply that #2735, a native candidate, a product support matrix or a migration is accepted.
