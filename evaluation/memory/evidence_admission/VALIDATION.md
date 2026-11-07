# Validation

## v20 全部剩余请求补跑验证

执行提交 `1515a0d1` 的完整无模型回归为 556 passed、2032 warnings（Python 3.11.15，506.22 秒）。
v18/v19/v20 专项 48 项通过；新增 13 项在 Python 3.11.15、3.12.3、3.13.13、3.14.5 全部通过。
`make check`、issue Ruff check/format（227 个 Python 文件）、整个 evaluation 类型检查与评估包 wheel/sdist 构建通过，
四个 v20 包文件与归档逐字节匹配。本轮没有重跑根包全量 make test 或全项目四版本 tox。

351 次无网络 SDK/Pipeline 预检逐个匹配父会话冻结请求体；随后实际补跑 351 次，全部有响应及用量，未新增未知调用。
原 471 次计划均已尝试，未运行请求为 0；469 个可用响应的输出/评分全部离线回放匹配，2 个历史缺失响应保持不可用。
已知输出失败、未决语义和历史缺失仍阻止正式验收，不能将全部请求已尝试描述为所有门槛通过。
详见 [结果](V20_RESULTS.zh.md)和[验证凭据](results/v20-live/validation.json)。

## v19 剩余请求补跑验证

执行提交 `1c62e664` 的完整无模型回归为 543 passed、2000 warnings（Python 3.11.15，494.28 秒）。
v18/v19 专项 35 项通过；新增 12 项在 Python 3.11.15、3.12.3、3.13.13、3.14.5 全部通过。
`make check`、issue Ruff check/format（220 个 Python 文件）、整个 evaluation 类型检查与评估包 wheel/sdist 构建通过，
四个 v19 包文件与归档逐字节匹配。没有重跑根包全量 `make test` 或全项目四版本 `tox`。

467 个待运行请求已全部通过无网络实际 SDK/Pipeline 预检，请求体哈希与 v18 冻结值匹配。
真实补跑 116 次后新增未知 usage，保留停止状态和 351 次未运行；合并已有响应的 118 次离线回放全部匹配，
2 次缺失响应不可回放，不能描述为 120 次全部回放成功。详见 [结果](V19_RESULTS.zh.md)和[验证凭据](results/v19-live/validation.json)。

## v18 固定生成比较验证

执行提交 `90a3337e` 的完整无模型回归为 531 passed、1968 warnings（Python 3.11.15）。
新增 23 项专项在 Python 3.11.15、3.12.3、3.13.13、3.14.5 全部通过。
`make check`（含全量 hooks、根包/集成类型与 50 项清单检查）、issue Ruff check/format、
整个 evaluation 类型检查、根包与评估包 wheel/sdist 构建通过；八个 v18 文件与评估构建逐字节匹配。
凭据见 [validation.json](results/v18-live/validation.json)，结果见 [V18_RESULTS.zh.md](V18_RESULTS.zh.md)。

最终实际命令使用锁定环境：

```bash
uv run --locked --project evaluation pytest evaluation/memory/evidence_admission/tests -m "not live" -q
uv run --locked --project evaluation ruff check evaluation/memory/evidence_admission
uv run --locked --project evaluation ruff format --check evaluation/memory/evidence_admission
make check
uv run --locked --project evaluation ty check --project evaluation
make build
uv build --project evaluation
```

其他 Python 的完整 evaluation / 根包全量测试未为 v18 增量重跑；历史通过只覆盖其原有提交。
开发期沙箱停滞、空启动环境、SDK 异常归一化断言失败，以及修改文件期间的两项冻结拒绝均单独保留，
不计入最终通过。最后一次完整验证期间执行代码没有修改。

471 个 SDK/Pipeline MockTransport 预检全部通过，不访问 provider。
线上四次尝试中三次取得响应、一份缺失；三份可用响应回放一致，第四份不可用，不是全矩阵线上通过。
默认测试不需要凭据或网络；实际 opt-in 命令、费用与取消流程见 [冻结协议](V18_PROTOCOL.zh.md)。

Current engineering receipts are in [the readiness report](ENGINEERING_READINESS.zh.md) and its
[machine summary](results/engineering-readiness/summary.json). Root tests, doctests, coverage and types pass on
Python 3.11/3.12/3.13/3.14. The engineering baseline evaluation count is 1291; the complete v16 evaluation suite
passes 1320 tests with one native-lineage prerequisite skip on each version, with full types passing on each.
Required repository hooks, issue-scoped Ruff, full evaluation types, wheel and sdist builds pass.
Protocol-specific measurements below retain their historical execution limits; they are not substituted for these receipts.

The synthetic preparation matrix uses actual local SQLite persistence and Source projection. It makes no provider requests.
Results and exact code/configuration hashes are in [the saved report](results/synthetic-v1-preparation/REPORT.md).

Validation commands:

```bash
uv run --project evaluation pytest evaluation/memory/evidence_admission/tests -m "not live" -q
uv run --project evaluation pytest -c evaluation/pyproject.toml \
  evaluation/memory/longmemeval_v2/tests evaluation/memory/evidence_admission/tests -m "not live" -q
uv run --project evaluation ruff check --no-fix evaluation/memory/evidence_admission
uv run --project evaluation ruff format --check evaluation/memory/evidence_admission
uv run --project evaluation ty check --project evaluation evaluation/memory/evidence_admission/src
uv run prek run -a
uv run ty check
```

The current evidence admission suite contains 508 passing tests, including 18 v17 regressions; its explicit issue command
emits 1935 warnings on Python 3.11.15. The v16 count is 490; the 461-test count belongs to the engineering baseline above.
V17 does not rerun the other Python versions or the complete root suite. These tests run without provider
credentials or live network access. Native LongMemEval lineage acceptance still requires the
`ScopedAtomicMemory.evidence` prerequisite from #1975.
The 24 related production window-recovery, SQLite wire-evidence and extraction-evidence tests pass separately.
SQLite tests in the evaluation environment emit an existing SQLAlchemy 2.1 `Result.tuples()` deprecation warning.

The evaluator package, root project and full evaluation type checks pass. LongMemEval narrows its optional evidence
capability explicitly and still rejects a missing or non-callable API. This does not add a production evidence API
or turn the skipped native-lineage acceptance into a pass. Historical frozen v14 JSON retains its exact bytes under
a dedicated hash hook and Git newline-conversion exclusion; JSON syntax validation remains enabled.

Wheel validation checks that the command and frozen fixture/manifest data are packaged and can be read outside the source
tree. Default tests and provider-adapter construction use no external model. No claim about live provider quality, remote
cancellation, actual host payloads, OceanBase or spawned production workers follows from these checks.

Decision regressions construct controlled gate inputs to exercise all three decisions, all-arm generation coverage,
unresolved control/candidate audits, candidate loss, provenance risk, input non-determinism and an uncommitted freeze.
The CLI integration substitutes a local generator and an in-memory committed flag solely to test run → private output →
human audit → regenerated report. These tests do not establish a real committed freeze, provider execution or model quality.
The generated test follow-up is temporary test output, not a recommendation from the synthetic preparation results.

Interruption tests stop inside a multi-window sample after its first returned response, both with caller cancellation and
request-cap exhaustion. The private journal retains that response and its known usage; no completed aggregate is consumable.
Private output modifications, duplicate call positions, unknown samples and unknown review identities are rejected.
Literal-scoring regressions distinguish a retained literal inside an omitted claim from an identical unrelated occurrence.

See [验收状态与限制](ACCEPTANCE.zh.md) for the distinction between implementation checks and issue completion.

V2 tests protect all eight original fixture/budget regressions, whole-record UTF-8/CRLF round trips, call and cumulative-token
exhaustion, continuing after an oversized Source, exact bundle support, omitted-support rejection, cancellation, versioned
manifest integrity and v1/v2 controlled CLI audit round trips. The comparison regression explicitly rejects a larger retained
count that replaces an old claim/literal identity. See [V2_PROTOCOL.md](V2_PROTOCOL.md) for commands and measurement limits.

The complete v2 preparation runs contain 720 original-matrix samples and 288 variant samples. Their recorded manifests bind the evaluator used for those runs;
they are historical artifacts and cannot be regenerated under a changed evaluator identity. All eight original failing combinations are fixed without observed
claim/literal/status regression; the three v1 arms also replay all 540 historical samples with identical inputs, support,
omissions and existing preparation scores. The variant dataset still has four loss combinations (12 repeated samples),
which remain visible in the [combined results](V2_RESULTS.zh.md). These are zero-provider-request runs, not live results.


V3 adds regression checks for the four known v2 losses at unchanged budgets and call counts, duplicate occurrences without
support aliases, label-independent input selection, missing-support rejection, cost-component accounting, resource caps and
all three cancellation phases. Controlled CLI generation/audit/report tests include v3, whose individual dataset report
remains insufficient evidence. The combined offline command rejects missing/duplicate datasets, known stress losses and
unexpected unavailability even if all sample records exist. Frozen v3 fixture/catalog integrity and all three wheel datasets
are checked separately. Exact-content scheduling performs no fuzzy deduplication or semantic classification.

The current root and evaluator-module type checks pass. The full evaluation check retains the one documented existing
lineage capability diagnostic. Both lock checks and a real wheel build pass; the installed wheel loads all three v3 datasets
outside the checkout. New-protocol provider quality and actual usage remain unmeasured. Historical v5 execution and the v9 first pair
are described in [the acceptance status](ACCEPTANCE.zh.md); they do not establish formal v10 coverage.


The live-policy-v1 extension has controlled end-to-end preparation, generation, private-output, audit and combined-report tests.
They cover missing datasets, model/endpoint mismatch, output fact/literal loss despite complete input, missing control output,
incomplete usage, uncommitted freezes, changed audit usage/support, per-dataset request caps and immutable policy integrity.
The separate smoke keeps the full experiment unchanged, produces ten scoped samples/eight local generator calls, persists its
private journal, and regenerates its report. Actual uncommitted CLI preflights stop before provider initialization. Controlled
committed flags and generators are confined to tests and establish no real provider or committed-freeze evidence.
See [LIVE_RUNBOOK.zh.md](LIVE_RUNBOOK.zh.md) for the exact formal execution and manual review steps.


## Pipeline and parent/atom contract validation

The v10 regressions exercise persisted/re-read real Sources, the actual
`AtomicMemoryGenerationPipeline.extract()` and compact protocol extractor. A real SDK uses
`httpx.MockTransport` for a copy output containing CJK, emoji, combining characters and CRLF. This is a local
integration test, not live provider evidence. Another regression preserves a compound candidate without splitting it.
Cancellation propagates; stale origins and request bindings are rejected before the injected generator runs.

Scoring regressions require every child before counting its parent, bind reviews to case/input/support/output,
reject a literal change or false attribution, preserve unresolved semantics and prevent joining ranges across
materialization identities. The recorded v9 failure is replayed unchanged and remains a failure. New parent semantics
are diagnostic and never overwrite old whole-sentence scores or authorize historical acceptance.

```bash
uv run --project evaluation pytest evaluation/memory/evidence_admission/tests -m "not live" -q
uv run --project evaluation ruff check --no-fix evaluation/memory/evidence_admission
uv run --project evaluation ruff format --check evaluation/memory/evidence_admission
uv run --project evaluation ty check --project evaluation evaluation/memory/evidence_admission/src
uv run prek run -a
uv run ty check
```

The repository enables Ruff auto-fixes by default. `--no-fix` preserves historical frozen code when auditing it.
Evaluation's locked Ruff 0.16.0 reports 13 existing diagnostics in preserved files outside v10; v10 code and tests
have zero diagnostics. Both Ruff 0.16.0 and the hook's pinned Ruff 0.15.7 accept v10 formatting.
The hook's full tracked-file run passes. The evidence admission module and root project type checks pass;
the broader evaluation type check still reports the single `ScopedAtomicMemory.evidence` diagnostic above.
These existing results are not relabeled as clean evaluation-wide checks.

The archived v10 evidence admission run has 355 passed and 1566 warnings on Linux/Python 3.11.15. Warnings include the
existing SQLAlchemy deprecation and two intentional incomplete-usage cases. Other Python versions were not rerun.
Wheel and sdist builds include all six v10 source/contract files with bytes matching the checkout.

The three-dataset input comparison and historical output replay are separate commands in
[V10_PROTOCOL.zh.md](V10_PROTOCOL.zh.md); results and decision limits are in [V10_RESULTS.zh.md](V10_RESULTS.zh.md).
The commands do not read API keys, initialize a provider or issue new model requests. Committed-freeze, formal
live coverage, provider copy/Unicode behavior and independent critical review remain open acceptance requirements.


## Protocol-aware Source-window control

V11 adds 16 passing regressions covering the six historical protocol-budget failures, actual durable reduction hints,
Scope isolation, real task cancellation, single-Source and resource rejection with a retained prefix, prefix extraction
through the real Pipeline, and historical content identity independent of Git execution metadata. The identity regression
simulates metadata changes; it does not create a Git commit or certify a new committed freeze.

The v11 full evidence admission run has 371 passed, zero failures/errors/skips, and 1592 warnings in 354.50 seconds
on Linux/Python 3.11.15. Other Python versions were not rerun. The complete window comparison reexecutes 324 identities
(279 new cost plans and 45 unchanged fault/cancellation boundaries), verifies 1296 inherited rows unchanged, recovers all
six protocol rejections, and reports zero input-coverage regressions or nondeterministic groups. Six minimal replays pass
under the same final source/contract identity. Provider requests remain zero.

The current root and evaluator-module type checks pass. Evaluation-wide types retain the one LongMemEval diagnostic;
Ruff 0.16.0 retains 13 existing diagnostics outside v10/v11. V11 code/tests have zero diagnostics, and both Ruff versions
accept formatting. Full tracked-file hooks and explicit hooks over new v11 files pass. Both offline lock checks pass.
Wheel/sdist validation confirms all six v11 source/contract files match checkout bytes. Public result scans find no
Source text, prompt, response, credential or physical Scope ID; the `body` key only appears as an integer metric category.

The frozen baseline and source hashes, final comparison, minimal replay, validation and acceptance status are bundled in
[results/v11-offline](results/v11-offline). Historical artifacts remain unchanged. See [V11_PROTOCOL.zh.md](V11_PROTOCOL.zh.md)
for commands and [V11_RESULTS.zh.md](V11_RESULTS.zh.md) for costs and remaining live acceptance requirements.


## Committed Pipeline smoke readiness

V12 adds 17 regressions for real SDK/Pipeline copy and Unicode, repeated calls awaiting review without resending,
protocol failures, unknown/excess usage, budget and plan/scope authorization, actual task cancellation, compound output,
missing copy, unresolved semantics, receipt recovery, modified raw responses and actual Git blob checks. The tests use
MockTransport and explicit controlled reviews; they establish neither provider quality nor an actual new committed freeze.

The full local run has 388 passed and 1721 warnings in 388.63 seconds on Python 3.11.15. New code/tests pass both Ruff
versions and the evaluator-module type check. The 13 preserved evaluation Ruff diagnostics and one unrelated broader
LongMemEval type diagnostic remain. Both locks, root types and repository hooks pass. Wheel and sdist each include all
nine v12 source/contract files with matching bytes. Other supported Python versions were not rerun.

The actual SDK mock preflight executes two requests through the real Pipeline. Message-text estimates are 3682 and 3831
at 4096 budgets; full HTTP JSON estimates are 3918 and 4243. HTTP serialization costs are not billed-token measurements.
Controlled outputs cover 4/4 and 2/2 atoms and exercise copy, emoji, combining characters and CRLF. Provider usage is null.
The public [readiness bundle](results/v12-readiness) binds source/configuration identities and request-body hashes.
[V12_PROTOCOL.zh.md](V12_PROTOCOL.zh.md) documents the separate actual-HEAD and explicit-live gates.


The committed v12 live diagnostic issued one request after actual HEAD verification. HTTP 200 returned `length`,
2048 reasoning tokens and empty final content; it remains `response_incomplete`, with no semantic scoring or retry.
A recorded-response replay through the actual SDK/Pipeline reproduces the category, finish reason, usage and response hash
with zero new provider requests. Unicode and the formal matrix remain unrun. These results do not turn mock success into
live quality success. See [V12_RESULTS.zh.md](V12_RESULTS.zh.md) for measured usage, conditional fees and audit-field limits.


## Explicit thinking diagnostic validation

The v13 full evidence admission run has 414 passing tests and 1922 warnings in 478.33 seconds on Python 3.11.15,
including 26 new execution regressions. SDK/Pipeline mock preflight and recorded v12 response replay issue no provider
requests. The window comparison reexecutes 324 identities, verifies 1296 inherited rows, recovers all six rejections,
and observes no input-coverage regressions or nondeterministic groups.

Ruff 0.16 and formatting, module/root types, both locks and all tracked-file hooks pass. Six former diagnostics are
resolved by code cleanup; seven use documented local exceptions for immutable fixture-loader style, sanitized boundaries,
cancellation preservation and injected SDK failures. The unchanged broader LongMemEval capability diagnostic remains.
Wheel and sdist each include all 11 v13 source/contract files with matching bytes. These checks do not establish provider
quality, other Python versions, remote cancellation, OceanBase, production workers or physical model snapshot pinning.
See [V13_READINESS.zh.md](V13_READINESS.zh.md) and [V13_PROTOCOL.zh.md](V13_PROTOCOL.zh.md).


The committed v13 diagnostic issued one real request. It returned a valid structured candidate with four legal copy parts,
but retained zero of four exact literals and failed the bound quality gate. A raw-response SDK/Pipeline replay reproduces
the same output hash and score without new provider requests; the stopped-session guard prevents advancing to Unicode.
These results establish neither successful Unicode generation nor formal all-arm acceptance.
See [V13_RESULTS.zh.md](V13_RESULTS.zh.md) for measured usage, review limits and the unchanged historical results.


## Inline cell protocol validation

V15 adds 22 behavior regressions. The full evaluator run passes 447 tests with 1926 warnings in 508.98 seconds
on Python 3.11.15. Recorded v14 templates replay through their historical Pipeline with no provider requests;
the Unicode loss remains 1/3, and the earliest template-free probe is not claimed replayable.

Three reexecuted datasets cover 1620 identities, one content identity, all arms, three budgets and three repetitions.
Selected ranges and wire roundtrips match; candidate executable required claims are 495/495, including same-request
coverage. Additional protocol budget rejections and nondeterministic groups are zero. Existing native preparation
rejections, unavailability and cancellation remain separate. All costs are local estimates and calls are potential plans.

The frozen minimal manifest requires one request per case. Unicode needs four requests with 1195 inline cells;
its ordinary per-call and cumulative packing budgets pass, but this stricter manifest fails. Execution stops before
SDK preflight or credentials. No v15 provider request, live semantic success or production authorization is claimed.

Ruff, v15/root/Pydantic AI integration types, both lock checks and integration manifest/guidance checks pass.
All repository hooks were executed: historical v14 contract JSON formatting fails; automatic formatting was restored
to preserve historical bytes. Other hooks pass, and all new JSON files pass the same format hook separately.
The broader evaluation type check retains the existing LongMemEval ScopedAtomicMemory.evidence diagnostic.
Other Python versions and a real SDK/provider preflight were not run. Sandbox SQLite thread wakeups stalled;
persistence tests and comparisons completed in the approved local execution environment without provider traffic.

See [V15_RESULTS.zh.md](V15_RESULTS.zh.md) and [results/v15-offline](results/v15-offline) for counts, hashes and limits.


## Protected-cell protocol validation

V16 changes only copy-cell selection and retains the v15 instructions, schema, evidence selection and resource gates.
The 29 regressions cover byte restoration, protected clusters, immutable references, tampering, compound candidates,
the known one-request readiness failure, and individual historical input/literal/executability loss despite complete aggregate coverage.
The original development goal failed (25 passed, one failed); its hashes and failed goal remain in the public receipts.
Passing regressions assert that the three-request Unicode plan is refused by the unchanged one-request gate.

All three datasets are reexecuted after actual HEAD/blob verification under one frozen core identity: 1620 rows,
candidate input retention 495/495, no new protocol budget rejections, historical regressions or nondeterministic groups.
The stricter minimal manifest still fails for Unicode (three requests); SDK preflight and provider generation are not run.
The unique final decision is `insufficient_evidence`. Intermediate offline pending states do not authorize further execution.

Current multi-version evaluation results, required checks, wheel/sdist byte verification and actual installed CLI receipts are
in [v16 validation](results/v16-offline/validation.json). Root runtime code is unchanged since the engineering commit;
the complete root four-version matrix is retained there rather than claimed rerun for this evaluation-only addition.
See [V16_PROTOCOL.zh.md](V16_PROTOCOL.zh.md) for offline commands and [V16_RESULTS.zh.md](V16_RESULTS.zh.md)
for the final decision, failure preservation, no-model measurement limits and read-only summary regeneration.


## Serial multi-request live diagnostic

V17 adds 18 passing regressions for audited SDK/Pipeline copying, complete-context billing reservation, unknown usage,
literal credential loading without shell execution, no resend after failure or pending review, output-bound review,
whole-case coverage, actual task cancellation, and preflight request-hash enforcement. The full evidence admission run
passes 508 tests with 1935 warnings on Python 3.11.15 after the committed execution freeze. Four-request SDK preflight,
Ruff, formatting, whole evaluation types, `make check` and byte-matched wheel/sdist builds pass.

Two actual requests execute after freeze: exact literals 4/4, then Unicode 2/3 with required CRLF missing. The real
Pipeline returns both outputs; the two remaining planned requests are unrun after bound assistant review fails the
Unicode gate. Recorded HTTP-response SDK/Pipeline replay reproduces both hashes and scores without new provider calls.
Input/output usage is 8841/306 tokens, with no missing usage. Provider-reported Unicode input is 5679, so the local 4K
character-estimator gate is not a hard provider-token bound. No formal all-arm comparison or independent semantic
acceptance follows. See [V17_RESULTS.zh.md](V17_RESULTS.zh.md) and [the receipts](results/v17-live/validation.json).
