---
title: Agent guidance evaluation record
description: Measured routing, execution evidence, and remaining model limitations for tracking issue 1450 D.
---

# Agent guidance evaluation record

The implementation and reproducible commands are described in [Agent tool routing](integration-guidance.md).
The baseline is upstream `fe4002d37663213294ebffe4c080b6676b1c8014`. Measurements were made on Windows on
2026-09-09 with `step-3.7-flash` through the configured StepFun endpoint, temperature 0, a 6,000-token output budget,
and no forced tool selection. Fixtures use fictional Aurora facts and an isolated `fixture-scope`.

## Measured model behavior

The [recorded calls, arguments, controlled results, and replies](https://github.com/oceanbase/powercontext/blob/master/e2e/integration-guidance/results/step37-20260909.jsonl)
retain failures. These measurements are **not an all-pass certification**, and a prompt is not an authorization mechanism.
The initial matrix uses 11 English/Chinese scenarios with the Skill loaded and unloaded, 44 observations per host.
Handoff scores in this matrix measure the first selected operation only; they do not establish successful arguments,
execution results, finalization, or the absence of a later commit.

| Surface | Accepted routing / observations |
| --- | --- |
| DSH baseline | 36 / 44 |
| DSH implementation | 41 / 44 |
| Pi | 42 / 44 |
| OpenCode | 41 / 44 |
| OpenClaw | 34 / 44 |
| Codex MCP catalog and Skill | 38 / 44 |
| Claude Code MCP catalog and Skill | 39 / 44 |
| WorkBuddy MCP catalog and Skill | 38 / 44 |
| Hermes | 41 / 44 |
| Portable Agent Plugin Skill with MCP | 35 / 44 |

The DSH baseline's unloaded Skill cases included unnecessary preview retrieval and premature Handoff preparation.
The implementation's ordinary work, sufficient context, search, inventory, save, preview, Handoff, Review, empty search,
and failed-save cases all passed in that sample (40/40). Its three failures attempted a save tool removed from the
evaluation catalog. No calls were executed by this catalog evaluator.

The matrix's Handoff classifier initially accepted only the high-level MCP work-transfer operation. Some recorded
failures instead selected valid low-level Source capture; the evaluator accepts both paths. Original observations
are retained rather than retrospectively presented as a stronger measured success rate.

Boundary qualification covers sufficient context, inventory, Handoff, preview, and empty search in all three Skill
states. It recorded WorkBuddy 30/30, Agent Plugin 29/30, and OpenClaw 27/30. Separate Skill-unavailable runs recorded
125/132 accepted selections across DSH, Pi, OpenCode, Hermes, Codex, and Claude Code. Residual cases include unnecessary
preview retrieval, premature Hermes preparation, and missing-tool calls. An OpenClaw catalog regenerated through its
actual availability-aware prompt builder recorded 1/2 on missing-save-tool scenarios. OpenClaw has no packaged Skill;
its Skill-state labels are repeated catalog conditions, not evidence of Skill loading.

Result reporting was inspected separately. Successful explicit writes produced acknowledgements only after the
controlled success; failed writes and empty searches produced corresponding explanations. Some empty searches continued
with broader queries or list, which remains visible as a failed bounded scenario. A provider timeout and an empty reply
are retained as failures, not excluded to improve the score. Additional description constraints address supplied facts,
temporary handoffs, and exact Handoff evidence; broad model compliance remains subject to qualification.

The description-boundary sample recorded **48/48 first-turn selections** for Handoff and preview requests across
OpenClaw, Hermes, OpenCode, and the Agent Plugin, in English/Chinese and all three Skill states. It did not continue
Handoff calls through tool results or inspect subsequent writes. This score is **not multi-turn Handoff acceptance**
and does not qualify exact evidence, finalization, or temporary-versus-durable behavior. Original observations remain
available, including failures and missing-tool stress results.

## Multi-turn Handoff qualification

The evaluator validates each model call against the exported catalog and generated HTTP request model, returns
contract-valid controlled results, and continues through capture, activation/preparation, and finalization, or the
high-level current-work operation. It rejects unavailable tools, foreign Scopes, invalid arguments, invented evidence,
altered Drafts, parallel dependent writes, and any commit in a temporary or ordinary transfer request. A pass also
requires a terminal answer containing the complete unchanged prepared carrier. Result wording and the truth of the
supplied facts still require inspection; these are controlled-result measurements, not native-host execution.

The `handoff` and `handoff_request` cases cover explicitly temporary transfers and ordinary handoff imperatives.
The fixture uses the HTTP response field `source`, while evidence wraps it as `{kind: "source", source_ref: source}`.
The high-level input uses WorkClaims with `text`, `basis`, and `evidence`; inspected facts without exact existing
PowerContext references use `declared` and an empty evidence list. Skill discovery names remain unchanged.

The 2026-09-10 Step 3.7 Flash qualification uses two cases, two languages, and three Skill states per surface.
The latest observation for each condition totals **64/96**, composed from the recorded batches below; it is not
a single simultaneous run or an all-host acceptance result.

| Surface | Complete sequences / observations | Evidence batch |
| --- | --- | --- |
| Codex MCP catalog | 12/12 | `contract-sequence` |
| Claude Code MCP catalog | 11/12 | `contract-sequence` |
| WorkBuddy MCP catalog | 8/12 | `contract-sequence` |
| Portable Agent Plugin with MCP | 10/12 | `contract-sequence` |
| Hermes | 10/12 | `structured-native-parameters` |
| DSH | 5/12 | `real-dsh-model-request` |
| OpenCode | 3/12 | `structured-native-parameters` |
| Pi | 5/12 | `structured-native-parameters` |

The [raw JSONL evidence](https://github.com/knqiufan/powercontext/blob/bcfdc9fc726021fce3b20a46f3788096794a2b60/e2e/integration-guidance/results/step37-handoff-20260910.jsonl) retains all batches,
including the initial WorkClaim probe and intermediate failures. Each batch includes its exported catalogs and model
configuration. DSH's final batch uses the actual compiled SDK model request; intermediate DSH catalogs were exported
from registration specifications and do not establish that runtime contract. Model calls still receive controlled
results in this evaluation, including in the final DSH batch.

Remaining failures include invalid provenance, malformed or unfinished Drafts, and incomplete or changed carriers.
The exact-carrier check also rejects omitted nullable fields. No commit call occurred in these latest 96 observations,
but early failures truncate those sequences and cannot establish the behavior of a later successful continuation.
These model scenarios remain unqualified; deterministic schema and runtime regression checks do not convert them
into passes.

## Execution and regression evidence

- Actual DSH 0.1.2-rc.1 SDK/host requests contained the system guidance and all 19 native PowerContext tools before a
  Skill load. The real host suite passed recall, injection, restart recovery, direct errors, and Scope isolation.
- A live Step 3.7 run through the real DSH host selected `pc_remember`. The pinned SDK has no interactive approval
  channel, so the host rejected the write. The model reported **not saved** and identified the unavailable approval
  channel. Server inspection confirmed no Memory write. A subsequent explicit search returned the seeded Memory and
  its exact citation. No forced fixture tool choice was used.
- The existing real Codex acceptance passed actual Memory save, subsequent search, and no false success when the MCP
  endpoint was unavailable. It used desktop-bundled Codex CLI 0.153.4 with the configured `gpt-6-astra` model, UTF-8
  subprocess output, and loopback proxy bypass. The fixture bounds graceful server shutdown.
- DSH package/HTTP tests, Pi package tests including the real CLI, OpenCode and OpenClaw package tests, MCP initialization,
  Hermes provider tests, API contract checks, and capability-manifest checks protect executable behavior and tool identity.
  OpenClaw validation uses Node 24.15.0, supported by its pinned SDK.

## Qualification boundary

Registered catalogs and controlled Skill text do not establish automatic Skill discovery or full execution in every
external host. MCP-backed catalog measurements share the same Server tools; they are not three independent native CLI
execution runs. Missing-tool stress intentionally tests catalog filtering and can elicit model hallucinations even with
explicit guidance. Those results remain unqualified; the runtime must reject absent tools and preserve approval checks.
Do not mark those model scenarios passed or infer publication, installation, commitment, or execution from generated text.
