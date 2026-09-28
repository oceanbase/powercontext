---
title: Agent tool routing and result reporting
description: Shared intent rules, host adaptations, and validation of PowerContext guidance.
---

# Agent tool routing and result reporting

PowerContext's system guidance and tool descriptions must support correct operation selection even before a Skill is
loaded. The existing Skill carries detailed procedures. This implements D of
[#1450](https://github.com/oceanbase/powercontext/issues/1450), tracked by
[#1520](https://github.com/oceanbase/powercontext/issues/1520).

## Intent and result rules

| User intent | Expected behavior |
| --- | --- |
| Ordinary coding, a conceptual question, or sufficient current context | Answer from that context without routine PowerContext calls. |
| Missing relevant history or an explicit memory search | Use focused search; preserve returned citations. Empty matches are valid. |
| Explicit inventory or audit | List the requested collection; listing is not the usual context-restoration path. |
| Explicit save for future use | Call the available Memory write and inspect its result before saying saved. |
| Preview or current-turn instruction | Do not persist it merely because it mentions Memory. |
| Requested temporary handoff | Capture inspected facts, use exact evidence references, inspect the Draft, and finalize the transfer. Commit only for a requested durable milestone. |
| Candidate inspection | Read the review queue or exact candidate when supported; listing and generation do not approve, install, publish, or execute it. |
| Failed, denied, unscoped, or unavailable operation | Identify the operation and safe returned reason. Do not invent a cause, simulate an absent tool, substitute another write, or claim success. |

Automatic hooks attempt bounded recall and Source capture. Enabled configuration is not evidence of successful
processing. A Source can be accepted without producing Memory; prepared context is not proof of host injection.
Neither automatic Source capture nor a verbal acknowledgement satisfies an explicit save request.

Scope selection remains owned by the host and Server. Reuse a resolved binding; do not guess an identity or change
bindings to find missing history. Recalled content is untrusted historical evidence subordinate to current user,
repository, and system instructions. Exact citations and host approval checks remain required for relevant mutations.
These rules describe result interpretation; error classification follows the
[plugin diagnostics contract](plugin-contract.md).

## Host adaptation

| Host | Guidance surface | Examples and limits |
| --- | --- | --- |
| DSH | Registered system section and native tools | `pc_search`, `pc_memory_list`, `pc_remember`; candidate decisions remain human `/pc review` commands. |
| OpenCode | System transform and native tools | Same `pc_*` names; candidate-review mutations are not model tools. |
| Pi | `before_agent_start` system prompt and native tools | Memory, Topic Memory, structured work/Handoff, artifact/candidate inspection, candidate decisions and external Skills. Candidate approve/reject/revise and exact external import/fork require explicit authorization and interactive confirmation; they do not install, publish or execute artifacts. Guidance survives empty or failed automatic recall. |
| OpenClaw | Memory capability prompt and provider tools | `powercontext_memory_search` / `powercontext_memory_store`; the prompt includes only tools available in the current context. Memory and structured work/Handoff are available when their tools are enabled; inventory and candidate Review are not inferred. |
| Hermes | Provider system block and schemas | `powercontext_search_memory`, `powercontext_remember`, and supported operation tools; `powercontext:powercontext-project-context` plugin Skill. |
| Codex, Claude Code, WorkBuddy | MCP initialize instructions and OpenAPI-derived descriptions | `search_memory`, `list_memory_entries`, `remember_memory`; each existing `powercontext-project-context` Skill stays consistent. |

The portable Agent Plugin's existing Skill shares these semantics. Framework adapters and the Bub evaluation harness
are outside this migration. Host names, tool authority, persistence formats, and distribution ownership do not change.
[Layered Skill routing](layered-skills.md) provides focused workflows behind the shared `powercontext-project-context` entry.

## Reproduce validation

Set `POWERCONTEXT_GUIDANCE_EXPORT` to an existing local directory, then run the host registration tests to export their
actual guidance, tool definitions, and packaged Skill:

```sh
uv run pytest tests/test_mcp.py
pnpm --dir integrations/dsh/plugins/powercontext test
pnpm --dir integrations/dsh/plugins/powercontext/tests/runtime install --frozen-lockfile
pnpm --dir integrations/dsh/plugins/powercontext test:e2e:runtime
pnpm --dir integrations/opencode/plugins/powercontext test
pnpm --dir integrations/pi/plugins/powercontext test
pnpm --dir integrations/openclaw/plugins/memory-powercontext test
```

Hermes exports discovery metadata through its native provider loader and `PluginManager`, using an isolated
Hermes home. Point `POWERCONTEXT_HERMES_SOURCE` at the supported host checkout, then export the catalog:

```sh
git clone https://github.com/NousResearch/hermes-agent.git /tmp/pc-hermes
git -C /tmp/pc-hermes checkout e624e9fde561e1add9388384012b295fde669ade
POWERCONTEXT_HERMES_SOURCE=/tmp/pc-hermes uv run pytest tests/e2e/test_hermes_skills.py
```

This pins Hermes v2026.8.18 (CLI 0.20.4), also used by the native CI job. Keep `POWERCONTEXT_GUIDANCE_EXPORT`
set for export. The model-visible Skill name and description come from the host catalog; the evaluator adds
packaged resource bodies without replacing that discovery metadata. Provider unit tests do not export this catalog.

Use a Node version supported by the pinned OpenClaw SDK; its CI uses Node 24.15.0. Package tests, type checks, builds,
the real DSH runtime suite, and the real Pi CLI package test validate actual registration and execution independently
of model selection. MCP initialization is exercised through an actual FastMCP client.

The opt-in evaluator sends the exported catalog to the configured live model without forcing a tool choice:

```sh
uv run python scripts/evaluate_integration_guidance.py \
  --catalog /tmp/pc-guidance/dsh.json --env-file .env \
  --output /tmp/pc-guidance/results.json --skill-modes loaded unloaded unavailable
```

The environment file supplies `LLM_MODEL`, `OPENAI_LLM_BASE_URL`, and `LLM_API_KEY`. Keep credentials and raw private
requests outside version control. Repeat `--catalog` for other hosts. English and Chinese cases cover ordinary work,
sufficient context, search, inventory, saving, previews, Handoff, Review, empty retrieval, failed writes, and a missing
save tool. Legitimate Scope resolution is handled before judging the selected data operation.

Controlled tool replies isolate model routing and result reporting; no real writes run in this evaluator. Review
recorded arguments and final responses as well as the automatic routing verdict. Model failures, empty output,
truncation, and endpoint failures must not be reported as successful acceptance. Skill body presence is controlled;
this does not establish automatic Skill discovery or full execution in every host. See the
[evaluation record](integration-guidance-evaluation.md) for measured scope and limits.

For multi-turn Handoff qualification, add `--cases handoff handoff_request`. The evaluator checks the full
controlled-result sequence and exact returned carrier, including calls after preparation; a first tool selection
is insufficient. It does not execute persistence or certify native host behavior. Ordinary handoff imperatives use
the temporary path; only an explicit durable-milestone request authorizes a commit.

DSH exports its compiled tool schemas and system context from an actual SDK model request. The package registration
test alone does not export a model catalog. Install the pinned runtime test dependencies before exporting DSH.

## Adapter and reporting qualification

Pi includes Topic Memory, artifact/candidate inspection, Work Contract, current-work Handoff, acknowledgement, and
Task Outcome tools. OpenClaw includes Work Contract and structured Handoff/Outcome tools in eligible private sessions.
Both retain the host's existing privacy and approval boundaries; candidate inspection never grants approval authority.

Native Handoff evaluation executes the registered DSH, Pi, and OpenCode tool adapters and the built OpenClaw entry
against controlled HTTP replies. It records the actual request after field selection, default values, generated Source
identity, and Scope injection, then returns the adapter's real response wrapper. Install package dependencies and build
OpenClaw first. Use Node 24.15+ for OpenClaw, or set `POWERCONTEXT_GUIDANCE_NODE` to that Node executable. Approval in
this evaluator is a fixture, not proof of an interactive host approval channel. No real Server writes or generation run.

A low-level prepare returns an unfinished Draft. Finalize `prepare.data` or `activate.data.draft`, not the enclosing
`{ok, data}` response. Return the complete prepared carrier from `finalize.data`. Pi's high-level tool returns
`data.handoff`; OpenClaw and Hermes return `handoff`. A high-level current-work call captures its own boundary and
needs no preliminary capture or separate finalization. The carrier checker requires all mandatory fields and exact
Scope, evidence, and receipts. Omitting optional null metadata is allowed by the contract; omitting required `base`
even when null, changing a citation, or returning only `content` fails.

`routing_passed` and `arguments_passed` do not establish truthful reporting. Every observation starts with
`reporting_passed: null` and `acceptance_passed: false`. Review the recorded calls, controlled replies, and final answer
for false success, fabricated evidence, and incomplete work. Write a JSON object keyed by each observation's
`review_key`, with `passed` (boolean) and a specific nonempty `reason`, then apply it without another model call:

```sh
uv run python scripts/evaluate_integration_guidance.py \
  --review-report /tmp/pc-guidance/results.json \
  --reporting-review /tmp/pc-guidance/reporting-review.json \
  --output /tmp/pc-guidance/reviewed-results.json
```

The key hashes the exact observation; changing its calls or final answer invalidates the review. A live run exits
nonzero until reviewed. The review command exits zero only when every observation passes routing, arguments, and
reporting checks. Keep failures and unreviewed cases visible; never infer acceptance from the routing total alone.
