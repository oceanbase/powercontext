---
title: Agent guidance evaluation guide and record
description: Run routing and Skill evaluations, interpret their evidence, and inspect recorded measurements.
---

# Agent guidance evaluation guide and record

This guide is for contributors validating PowerContext guidance. The [routing design](integration-guidance.md) defines expected Agent behavior; [layered Skills](layered-skills.md) describes the shared resources and host discovery.

## Evaluation scope

Resource consistency, native host execution, and model behavior are separate checks. Resource checks compare packaged files and references. Native tests exercise discovery and registered tools. Model evaluation measures operation selection, arguments, and final answers against controlled replies. Each result applies to the host, model, Skill, and contract recorded with it.

The [Claude Code skill-up suite](https://github.com/oceanbase/powercontext/blob/master/evaluation/skills/skill-up/README.md) and [ZCode suite](https://github.com/oceanbase/powercontext/blob/master/evaluation/zcode_guidance/README.md) maintain their own setup, pins, and paired with/without-Skill runs. Their READMEs describe their execution and permission fixtures. Keep those results separate from native approval, persistence, memory-quality, and task-outcome measurements.

## Prepare the environment

Use the repository's locked Python development environment and install the dependencies for the host packages being tested. Build OpenClaw before exercising its entry point. Its pinned SDK needs Node 24.15 or newer; `POWERCONTEXT_GUIDANCE_NODE` can select that interpreter for adapter evaluation.

Live evaluation reads `LLM_MODEL`, `OPENAI_LLM_BASE_URL`, and `LLM_API_KEY` from the specified environment file. Keep credentials and private transcripts outside version control.

## Check packaged resources

```sh
make integration-guidance-check
uv run --locked pytest tests/test_layered_skills.py tests/test_guidance_installation.py
```

These checks cover shared-copy drift, local references, packaged resources, and installation. They do not call a model or establish that every external host discovers or executes the Skill.

## Export native catalogs

Set `POWERCONTEXT_GUIDANCE_EXPORT` to an existing directory. The following commands export the model-visible guidance, tools, and Skill metadata from their respective host tests:

```sh
mkdir -p /tmp/pc-guidance
export POWERCONTEXT_GUIDANCE_EXPORT=/tmp/pc-guidance
uv run --locked pytest tests/test_mcp.py
pnpm --dir integrations/dsh/plugins/powercontext/tests/runtime install --frozen-lockfile
pnpm --dir integrations/dsh/plugins/powercontext test:e2e:runtime
pnpm --dir integrations/opencode/plugins/powercontext test
pnpm --dir integrations/pi/plugins/powercontext test
pnpm --dir integrations/openclaw/plugins/memory-powercontext test
```

DSH exports compiled schemas and system context from an actual SDK model request. Its package registration test alone does not export that catalog. Pi uses its SDK Skill loader, and OpenClaw's package test invokes native `skills list --json` with an isolated profile. MCP initialization is checked through a FastMCP client.

Hermes exports native `skills_list` and `skill_view` metadata from an isolated home. Use the source revision pinned by its CI job, Hermes v2026.8.18 (CLI 0.20.4), and retain the export environment variable:

```sh
git clone https://github.com/NousResearch/hermes-agent.git /tmp/pc-hermes
git -C /tmp/pc-hermes checkout e624e9fde561e1add9388384012b295fde669ade
POWERCONTEXT_HERMES_SOURCE=/tmp/pc-hermes uv run --locked pytest tests/e2e/test_hermes_skills.py
```

DSH and Hermes discovery names and descriptions come from the native catalogs. The evaluator supplies packaged reference bodies without replacing that metadata. Hermes provider unit tests do not export a native catalog.

## Run model evaluation

```sh
uv run --locked python scripts/evaluate_integration_guidance.py \
  --catalog /tmp/pc-guidance/dsh.json --env-file .env \
  --output /tmp/pc-guidance/results.json --skill-modes loaded unloaded unavailable
```

Repeat `--catalog` for other hosts. Cases cover ordinary work, sufficient context, search, inventory, saving, previews, Handoff, Review, empty retrieval, failed writes, and a missing save tool in English and Chinese. Legitimate Scope resolution is treated as a prerequisite to the data operation. Tool choice is left to the model.

For multi-turn Handoff, add `--cases handoff handoff_request`. The evaluator checks the sequence through preparation and the complete returned carrier. Ordinary and explicitly temporary transfers must not commit a durable milestone.

To measure reference reads, add `--layered-skills --skill-modes unloaded unavailable`. The optional reader exposes actual packaged text and records requested resources. `skill_search` and `skill_handoff` require the relevant Memory or Handoff reference before the data operation; the entry alone, another domain, or a later read does not pass. Ordinary requests need no Skill read. Unavailable mode provides no reader and checks whether the remaining tool guidance is sufficient.

## Interpret execution evidence

The model evaluator returns controlled replies and performs no real Server writes or generation. Native adapter evaluation runs registered Pi and OpenCode tools and the built OpenClaw entry against controlled HTTP replies, recording translated arguments and wrapped results. Fixture approval does not establish a real interactive approval channel. DSH uses controlled MCP replies here; its SDK export and runtime acceptance are separate checks. MCP catalogs do not establish each product's native Skill discovery.

Handoff validation requires a complete transferable carrier with its Scope, exact evidence, receipts, and required nullable fields. Optional null metadata may be omitted.

Model errors, empty output, truncation, endpoint failures, invalid arguments, and incomplete carriers remain failures. A correct first tool choice alone does not establish a completed workflow or an accurate final answer.

## Review result reporting

`routing_passed` and `arguments_passed` cover operation selection and arguments. Each observation starts with `reporting_passed: null` and `acceptance_passed: false`. Review the recorded calls, controlled replies, and final answer for false success, invented evidence, and unfinished work.

Create a JSON object keyed by each observation's `review_key`. Each value needs a boolean `passed` and a specific nonempty `reason`. Apply the review without another model call:

```sh
uv run --locked python scripts/evaluate_integration_guidance.py \
  --review-report /tmp/pc-guidance/results.json \
  --reporting-review /tmp/pc-guidance/reporting-review.json \
  --output /tmp/pc-guidance/reviewed-results.json
```

The key hashes the exact observation, so changed calls or answers invalidate an earlier review. Live evaluation exits nonzero until reviewed; the review command exits zero only when all observations pass routing, arguments, and reporting. Retain failures, unreviewed observations, catalogs, and transcripts with the run.

## Historical measurements

These records apply to their recorded revisions and contracts. They do not qualify the current checkout. The September model evaluations used `step-3.7-flash`, temperature 0, automatic tool selection, fictional Aurora facts, and an isolated `fixture-scope` with controlled replies.

| Date | Revision and evidence | Measurement conditions |
| --- | --- | --- |
| 2026-09-15 | master `847203dc`; [all 276 observations](https://github.com/knqiufan/powercontext/blob/codex/align-powercontext-tool-routing/e2e/integration-guidance/results/step37-native-20260915.jsonl) | Final comparison combines DSH's `qualification` batch (10,000 output tokens) with eight surfaces from `qualification-final` (16,000 tokens). Codex reviewed result reporting against the exact transcripts. |
| 2026-09-10 | [all Handoff batches](https://github.com/knqiufan/powercontext/blob/bcfdc9fc726021fce3b20a46f3788096794a2b60/e2e/integration-guidance/results/step37-handoff-20260910.jsonl) | Two cases, two languages, and three Skill states per surface. Results combine the last observation for each condition from several batches. |
| 2026-09-09 | Baseline `fe4002d37663213294ebffe4c080b6676b1c8014`; [routing evidence](https://github.com/oceanbase/powercontext/blob/master/e2e/integration-guidance/results/step37-20260909.jsonl) | Windows, configured StepFun endpoint, 6,000 output tokens. The initial matrix covers 11 English/Chinese scenarios with the Skill loaded and unloaded. |

### Measured model behavior

The columns measure different behaviors. September 9 scores cover the first selected operation only; September 10 scores require complete Handoff sequences; September 15 adds transcript-bound reporting review. A dash means the surface was not part of that comparison.

| Surface | Sep 9: routing | Sep 10: complete Handoff | Sep 15: sequence and carrier | Sep 15: after reporting review |
| --- | --- | --- | --- | --- |
| DSH baseline | 36/44 | - | - | - |
| DSH implementation | 41/44 | 5/12 | 12/12 | 12/12 |
| Pi | 42/44 | 5/12 | 12/12 | 12/12 |
| OpenCode | 41/44 | 3/12 | 12/12 | 12/12 |
| OpenClaw | 34/44 | - | 12/12 | 11/12 |
| Hermes | 41/44 | 10/12 | 12/12 | 12/12 |
| Codex MCP catalog | 38/44 | 12/12 | 12/12 | 12/12 |
| Claude Code MCP catalog | 39/44 | 11/12 | 11/12 | 11/12 |
| WorkBuddy MCP catalog | 38/44 | 8/12 | 12/12 | 12/12 |
| Portable Agent Plugin with MCP | 35/44 | 10/12 | 11/12 | 11/12 |
| Total | - | 64/96 | 106/108 | 105/108 |

In the September 15 comparison, two Handoff failures claimed verified facts without evidence. Reporting review also rejected an OpenClaw answer that strengthened "no code changes" into "no code changes required". A separate DSH/Codex reporting sample passed 17/24 selections and 16/24 after review: all 12 failed-write cases reported no save, seven missing-tool cases failed, and one answer suggested unauthorized Source capture. These failures remain in the evidence.

### Multi-turn Handoff qualification

The September 10 sample remains unqualified. Failures included invalid provenance, malformed or unfinished Drafts, and incomplete or changed carriers. Its historical carrier check also rejected omitted nullable fields. No commit occurred in the final 96 observations, but early failures truncated sequences and cannot establish what a successful continuation would do.

The final DSH batch used the actual compiled SDK model request; intermediate DSH catalogs came from registration specifications. All model calls in this evaluation received controlled replies. Native adapter checks establish argument and response handling, not real persistence, generation, or interactive approval.

In the September 9 DSH sample, the ten ordinary and data-workflow categories passed 40/40; three missing-save-tool observations attempted the unavailable tool. The initial routing classifier also rejected some valid low-level Source capture choices. The published scores retain the original observations.

### Execution and regression evidence

| Recorded execution | Observed behavior | Limit |
| --- | --- | --- |
| DSH SDK/host `0.1.2-rc.1` | System guidance and 19 native PowerContext tools reached the model before Skill loading; tests covered recall, injection, restart recovery, direct errors, and Scope isolation. | Applies to that host revision. |
| Live Step 3.7 through DSH | The model selected `pc_remember`; the host rejected it because interactive approval was unavailable. The model reported no save, Server inspection found no write, and a later explicit search returned seeded Memory with its exact citation. | Tool choice was not forced; this is a rejected-write and search observation. |
| Codex CLI `0.153.4`, configured `gpt-6-astra` | Actual Memory save, subsequent search, and no false success with an unavailable MCP endpoint. | Applies to the recorded desktop-bundled CLI and model. |

### Qualification boundary

Additional September routing samples retain these narrower results:

| Sample | Result | Limit |
| --- | --- | --- |
| Boundary cases across three Skill states | WorkBuddy 30/30; Agent Plugin 29/30; OpenClaw 27/30 | Sufficient context, inventory, Handoff, preview, and empty search. |
| Skill unavailable across DSH, Pi, OpenCode, Hermes, Codex, and Claude Code | 125/132 | Residual failures included unnecessary retrieval, premature preparation, and missing-tool calls. |
| OpenClaw availability-aware catalog | 1/2 | Missing-save-tool scenarios. |
| Handoff and preview description boundaries | 48/48 | First-turn selection across OpenClaw, Hermes, OpenCode, and Agent Plugin; no tool-result continuation or later-write check. |

OpenClaw had no packaged Skill in the September 9 measurements; its Skill-state labels repeated catalog conditions. MCP-backed catalogs share Server tools and do not establish independent native CLI execution or Skill discovery. First-operation scores do not qualify finalization or temporary-versus-durable behavior. Runtime permission checks remain necessary; generated text cannot establish approval, publication, installation, or execution.
