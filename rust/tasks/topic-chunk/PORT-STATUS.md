# Topic Memory chunking status

Contract: PC-RUST-2740 /2026-11-22.1. HEAD1840db817ac6e3f39ce7645c89733648e8892419.
Unit: chunk_topic_memory_detail; D1 bridge; local Linux/cp314 implementation and executed acceptance PASS.
Official rust-anything3.1.0 build/record/diff/bench/gate PASS through the explicit lossless tool_outcome boundary.
Independent verification is recorded in the external report bound to the final source/artifact snapshot.
No default backend change, commit, push, PR or publication. Maintainer acceptance and Issue closure are separate.

## Actual results

- Rust fmt, locked clippy/test and2 Rust unit tests pass; no doctests exist.
- Root make check and explicit non-ignored new-file hooks pass; root ty remains strict.
- Target: CPython3.14.5 /Unicode16.0.0 /Pydantic2.13.4, Linux x86_64.
- Frozen corpus:67 unique business calls,67 matches,0 mismatches. Supported strings N=R=63, F=E=0;
  four invalid/missing calls are separate. Original golden, comparator, algorithm, adapter and monitor are unchanged.
- Official tool:67 business outcomes plus1 malformed-protocol rejection,68/68 matches in diff and gate.
  Reference outcomes are anchored to the original golden; surrogate requests round-trip exactly.
  Per-process native receipts bind run/context/source/golden/binary/request identities and explicit completion.
  Business N=R=63, F=E=0; protocol ValueError class/text is exact with native_delta0. Stale/partial receipts fail.
- Official negative controls: wrong exception type, text, Pydantic details, ordinal, swallowed reference trap
  and missing extension each fail at the intended comparison/path check. Restored positive replay passes.
- Original focused/model tests:12 pass,6 native calls. Real temporary SQLite and in-process FastAPI tests:
  92 pass,1196 native calls; F=E=0, zero skips. One third-party pyobvector SyntaxWarning is recorded.
  Embeddings are synthetic; real-provider and remote-host acceptance are NOT_RUN.
- Installed adapter/protocol tests:25 pass (12 adapter,13 protocol). Clean wheel install and187-package
  dependency check pass. Wheel has exactly one cp314 extension and no editable installation.
- Exact wheel-byte official diff and clean installed wrapper replay each match68 cases with valid receipts.
  Installed unwrapped strict67-case diff and task-owned gate (2 positives,6 negatives) pass.
- Fresh unwrapped benchmark satisfies all12 warm pilot budgets; all6 resource-worker rows pass.
  Budget evaluation is external to bench. Official wrapper bench includes test codec/golden/receipt costs
  and is not a product speed measurement. GIL retained; parallel throughput NOT_RUN.
- Manual Apache header check:26 applicable files, none missing. Actual license-eye CI and native CI NOT_RUN.

## Authoritative acceptance rows

| ID | Local status | Evidence / limit |
| --- | --- | --- |
| A-01 | PASS | Original focused tests, original strict golden and lossless official wrapper |
| A-02 | PASS | Threshold directions, forced overlap/tails, tail-before-fallback and duplicate ranges |
| A-03 | PASS | Empty/blank returns and exact invalid/missing errors;4 invalid calls separate |
| A-04 | PASS | Invalid UTF32/ranges, absent native and intended failure assertions |
| A-05 | PASS | Markdown/Unicode16/CRLF/CJK/emoji/combining and original surrogate/model errors |
| A-06 | PASS | Explicit selector, original private-call traps, real native binary identity and receipts |
| A-07 | PASS | Actual range/ordinal and detailed-error mutations fail; restored artifacts match |
| A-08 | PASS on Linux cp314 | Locked checks, local core/native wheel install, real official build/diff/bench/gate plus receipt, and strict task gate |
| A-09 | PASS on Linux target | Fixed pilot budgets and fresh-process resources; no concurrency/leak claim |
| A-10 | PASS as experiment | Fresh unwrapped complete-entry benchmark and P1 controls; no adoption claim |

The authority remains the issue matrix. This is local technical evidence, not CI, release, other-platform or
maintainer acceptance. The official static error-path check proves a recorded wrapper rejection, not complete
coverage of all environment/context/golden guards. The existing strict business-error and path checks remain required.

## Identity and evidence

Installed/wheel binary SHA256:943bd56d65712766ca3eaee835588d8c35d2a3acfb54b22f23291cf3b38d986b.
Official direct-Cargo binary SHA256:16d56db9fc5e945eeeb08a398942d22014163332bf70934b7924dc1914acfded.
These are distinct builds and are tested separately; neither is silently substituted for the other.
The generated _native.abi3.so filename is not ABI3 support. It is archived/removed before wheel building.

Raw commands, receipts, binaries, wheels, contexts, driver, generated recording and final snapshots are under
/tmp/pc-topic-2740/tool-adapter/. The final evidence index explicitly hashes ignored tool artifacts as well as
source, original golden and both binary identities. Do not use HEAD alone for this uncommitted delivery.
Public machine summaries: rust/topic-chunk-rs/evidence/{validation,performance,tool-acceptance}.json.
Reproduction and tool boundary: rust/topic-chunk-rs/TOOL-ACCEPTANCE.md.
Independent review is another context inspecting real artifacts, not an independent semantic oracle.

## Decision and remaining boundaries

Keep Python as default. Fresh8K warm R costs about33–47 microseconds versus237–694 for P0; no-LF1K can favor P1b.
Call frequency, production significance, platform release/rollback and long-term ownership remain unproven.
The official raw codec/comparator limitations are handled by the explicit lossless test boundary, not by a tool fork,
weakened expectations, dropped surrogate cases or a gate waiver. Tool availability and this local gate are resolved.
Retain the experimental sources/evidence for review; do not publish a runtime dependency or claim default integration.
Before later external sharing, refresh master/#2735 and reconcile names without touching another owner's work.
