# Primary-model request counts: 24-task OFF/MS run

[Project README](../../../README.md) · [中文](request-counts_CN.md) · [日本語](request-counts_JP.md) · [Per-task data](request-counts-24.json)

In this separate 24-task run, MS used **539 primary-model requests versus 708 for OFF: 23.87% fewer**. Both arms solved the same 15 tasks, and all 24 records in each arm were technically valid.

| Arm | Primary requests | Completed responses | Requests per task | Solved | Technically valid |
| --- | ---: | ---: | ---: | ---: | ---: |
| OFF | 708 | 708 | 29.50 | 15/24 | 24/24 |
| MS | 539 | 538 | 22.46 | 15/24 | 24/24 |

The reduction is `(708 − 539) / 708 × 100 = 23.87%`, calculated from totals across all 24 tasks. The unrounded MS average is `539 / 24 = 22.458333…`.

## What is counted

A request is an observed physical request to the primary solving model. It is not a tool call, a user turn, or a reasoning token. The count includes one MS request without a completed response: case 388 has 25 requests and 24 completed responses. Hidden provider-side retries remain unknown.

Auxiliary requests, including memory generation and embedding, are outside this primary-model count. It is therefore not a total count of all model or system work.

## Conditions and limits

The experiment defines OFF as M/C/S disabled, and MS as Memory plus explicit Steering, with CodeGraph disabled. MS reused historical memory seeds. The source report lists historical seed generation, exploration, and embedding costs separately from current-run spend; those costs are not included in the request counts above.

Each arm/task uses its single state-selected `r1` record, without selecting by success or token count. All 48 selected records are included; the source reports no extra executions. [The JSON](request-counts-24.json) contains each case ID, arm, request and completed-response counts, result, validity flag, and outcome hash.

This uses the same 24 case IDs as the [earlier OFF/MCS token comparison](README.md), but it is a **different run and configuration**. The earlier 32.57% token reduction and 16/24 solved result do not describe this OFF/MS run. These request counts do not establish a wall-time reduction, equivalent quality on other tasks, or an isolated causal effect of Memory or Steering.

## Provenance and verification

Source summary timestamp: `2026-11-22T21:56:44.214489+00:00`. Its SHA-256 is `98333adce1892a283e55fc551c321c6a3411a6e0eefd6cf7158579c17ead504e`, also recorded in the JSON. The export contains no raw prompts, traces, credentials, or private paths. Hashes identify the source records; they do not make the unbundled source report or traces independently auditable.

From the repository root:

```bash
python3 docs/benchmarks/token-savings/verify.py
```

The offline verifier checks the 24 paired cases, counts, matching solved sets, validity, the single incomplete response, and the aggregate reduction. It checks the bundled data without model calls or rerunning graders.
