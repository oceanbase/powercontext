# Topic Memory native compatibility pilot

Contract: [PC-RUST-2740, 2026-11-22.1](https://github.com/oceanbase/powercontext/issues/2740).
The user authorizes local implementation, builds and tests; no commit, push or external publication.
The task owns only `rust/topic-chunk-rs/` and `rust/tasks/topic-chunk/`.

## Boundary

D1: builtin `str` → unbound `str.encode(..., "utf-32-le", "surrogatepass")` → owned Rust codepoints
→ final intervals → original Python slicing and `TopicMemoryChunk` construction.
Python owns model validation and its errors. Rust owns all chunking, trimming and Unicode classification.
Offsets count Python codepoints, including lone surrogates. No production selector or fallback is added.
The invalid inputs explicitly required by A-03 are recorded separately from the supported string calculation domain.
Do not normalize exception types or messages. Direct custom `str` override semantics are outside the experiment.

Adapter interval checks: integer but not bool, `0 <= start < end <= len(detail)`, nondecreasing starts and ends.
Equal intervals and overlap are legal. There is no unconditional 200-result cap.

## Stages and decisions

- S0 (1–1.5 person-days): shell and explicit new-file hooks; root ty discovery with injected failure;
  exact CPython 3.14/Unicode16; locked PyO3 wheel; reference fingerprint and baseline budgets.
- S1 (1.5–2): P0 upper bound, P1a/P1b diagnostics, bounded native slice and 1K/8K/125K comparisons;
  same-day checkpoint with costs and remaining work. Poor performance stops speculative optimization,
  and does not silently cancel mandatory compatibility acceptance.
- S2 (2–3): full semantics, frozen golden, strict native selection, negative controls.
- S3 (1.5–2): real SQLite/API consumers, installed artifacts, full resource report and independent review.

Estimated total: 6–8.5 person-days. These are effort estimates, not measured elapsed time.
Default continuous independent verifier: opening, first slice/key interface, final snapshot.
Wait for actual CONTINUE before dependent work. Two consecutive unsuccessful CORRECT rounds on the same
acceptance item require HOLD and human decision. Environment/scope ambiguities HOLD affected work.
Repeated commands require a new diagnosis; budget exhaustion requires re-estimation, never a false PASS.

P1a preserves P0 scanning and assembly while avoiding diagnostic overhead where possible.
P1b must eliminate a block regex whole scan for the no-LF/no-leading-marker class, with exact parity.
Each diagnostic stays near 150 lines, standard library only; report applicability per input structure.
They are diagnostic artifacts, not production optimizations. Algorithm, language and bridge attribution
is conditional, not an additive causal decomposition from unrelated algorithms.

## Predeclared performance policy and budgets

Target: synchronous single-thread entry; typical 1K/8K and validated upper bound 125,000 codepoints.
Keep direct-call >125K fixtures only as compatibility diagnostics, distinct from production scale.
S0 P0 baseline on target 3.14.5: medians 1K 24–91 µs, 8K 242–697 µs, 125K 3.63–11.75 ms
across plain/Markdown/Unicode/inert LF (7 batches × 30 calls; raw record outside the repository).
Before first native slice, pilot limits are fixed as: warm median ≤ max(2×matching P0, 1 ms),
125K individual watchdog 5 s, subprocess workload timeout 60 s, peak RSS incremental allowance 64 MiB
above matched fresh-process startup. These are experiment regression guards, not product SLAs.
Measure native/Python allocation separately and disclose RSS granularity; tracemalloc sees Python only.

Correctness precedes performance. No extra native benefit if R does not beat P1b beyond measured variation.
No default integration recommendation from repeated plain strings alone. Unknown call frequency or
unresolved release/platform/rollback/maintenance costs defaults to retaining Python, with conditional
savings per call × assumed calls/second. Record absolute savings, not only ratios.
No universal 0.5 ms requirement is invented without a business owner. Independent Rust capability
is the explicit issue goal, not proof of product acceleration.

## Acceptance and evidence

Implement A-01…A-10 from the issue without rewriting the authority matrix.
Freeze reference source/model/test hashes, interpreter build, Unicode/Pydantic, corpus and comparator;
fail on fingerprint drift, never silently rerecord. Record failures as well as successes.
Golden preserves string/tuple/exception values with versioned lossless tags; no wildcard.
Only synthetic or explicitly redistributable public corpus, total corpus ≤2 MiB.

Force public entry and aliases; trap private reference algorithm calls, sticky violations and lost
profile hooks; test swallowed trap errors, wrong native results with normal counters, and missing binary.
N=R+F+E is a processing-path metric separate from match/mismatch and invocation counts.
Known model errors after Rust computed intervals count R. Non-string A-03 errors are reported separately.

Official rust-anything3.1.0 is available as a checksum-verified precompiled executable in a temporary task directory.
Use the checked-in tool configuration and test-only lossless tool_outcome boundary for67 frozen business calls
plus1 malformed-protocol rejection. Reference outcomes must equal the existing golden before tool recording;
requests must preserve Python types/codepoints. Require official build/record/diff/bench/gate plus a complete,
identity-bound native receipt. Wrong exception type/text/details, ordinal, swallowed reference traps and missing
extensions must fail through the real tool. Preserve the original strict task gate and independent locked checks.
Tool-wrapper bench is protocol cost only. Direct-tool and wheel binaries have separate identities and acceptance.
No shared CI change is authorized: current Cargo/wheel evidence is manual for the current snapshot.

## Adoption and artifact disposition

Review three future delivery options: a second distribution/runtime dependency; main-package native
build hook; or main build backend migration. Include Linux x86_64/aarch64, macOS, Windows, ABI/3.14,
wheel publishing, rollback and ongoing ownership. This pilot chooses only the separate experimental wheel.
If gains are insufficient, retain Python by default and retain reproducible experimental sources/evidence
locally for review; do not publish a runtime package or imply permanent support. Maintainer decides merge.
Before later sharing, recheck master and #2735, rebase if necessary, and reconcile names without touching
another owner's work or the shared `rust/PORT-*` files.
