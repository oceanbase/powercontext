---
title: Layered PowerContext Skills
description: Discover intent first, then load only the relevant Memory, Handoff, or Review workflow.
---

# Layered PowerContext Skills

The Skill entry is a small intent router. It explains when to search, list, save, hand off, or inspect candidates;
workflow references carry detailed arguments, result interpretation, and approval boundaries. This implements E of
[#1450](https://github.com/oceanbase/powercontext/issues/1450), tracked by
[#1620](https://github.com/oceanbase/powercontext/issues/1620), on top of the merged tool-routing work in #1522.

Ordinary coding and summaries with sufficient current context need neither a Skill load nor a PowerContext call.
An explicit operation still needs its real tool and observed result. An agent may call a self-contained tool directly,
or load the relevant domain when it needs detail. Reading the router first, loading all domains, and loading before
every response are not prerequisites. English and Chinese intent phrases live in actual Skill descriptions.

## Host layout and compatibility

| Hosts | Discoverable entry | Workflow detail |
| --- | --- | --- |
| Codex, Claude Code, WorkBuddy, portable Agent Plugin, Pi, OpenCode | Existing `project-context` | Local `references/scope-memory.md`, `work-handoff.md`, and `review-publication.md`. |
| Hermes | Existing `powercontext` | The same three reference domains with Hermes tool names and human Review commands. |
| MiniMax | Existing `powercontext-project-context` | Existing Scope/Memory, Handoff, Review and HTTP boundary references plus examples. |
| OpenClaw | `powercontext-project-context` | Packaged Scope/Memory and Handoff references; no inventory or candidate Review capability. |
| DSH | Existing runtime `project-context` router | Independently registered `powercontext-memory`, `powercontext-handoff`, `powercontext-review`; no filesystem reference dependency. |

File-backed hosts follow MiniMax's existing local-reference organization. Keep references beside the installed entry.
OpenCode's npm archive includes the complete Skill directory. WorkBuddy's installer resolves Python and Scope-helper
placeholders in all installed Markdown resources. OpenClaw uses the SDK's manifest `skills` directory mechanism.
DSH uses its existing runtime Skill service, so the host can discover domain descriptions and load one domain directly.

`using-powercontext` in the tracker describes a routing role, not a required new name. Existing names and installation
paths remain compatible; E does not introduce a competing distribution generator. Canonical names, generated
projections, and their migration remain owned by [#1405](https://github.com/oceanbase/powercontext/issues/1405) and
[#1410](https://github.com/oceanbase/powercontext/pull/1410). Framework adapters and Bub are outside this Skill migration.

## Workflow boundaries

Memory search finds relevant history; inventory lists a requested collection. An empty search does not authorize listing.
Explicit save uses the Memory write; automatic Source capture is not saved Memory. Preserve exact citations on revisions.

Temporary Handoff returns the complete prepared carrier, including required nullable fields and generation receipts.
Use each host's actual high-level or capture/activate/finalize workflow. Only an explicit durable-milestone request
permits commit. Inspected facts without exact PowerContext citations remain declared claims. Acknowledgement checks
current evidence, capability, and authority; it does not prove that work ran.

Review inspection and generation do not grant approval, publication, installation, or execution. Each host retains its
actual exposed operations and approval channel. OpenClaw retains private-session and tool-availability gates.

Skill absence does not disable independently sufficient tool guidance. A missing resource must identify its exact
Skill/path; a failed operation must identify its name and safe returned reason. Distinguish empty, rejected, unavailable,
and unknown outcomes. Do not invent a diagnosis, repeat an unconfirmed write blindly, or claim success without a result.

## Validation

Run `uv run pytest tests/test_layered_skills.py` to read each shipped entry and reachable reference, check actionable
missing-resource diagnostics, and inspect OpenCode/OpenClaw npm archive contents. Installer regressions exercise actual
OpenCode reference copying and WorkBuddy placeholder expansion. Pi's package test uses its SDK Skill loader. OpenClaw's
package test invokes `skills list --json` with an isolated profile. DSH's runtime suite verifies model-visible domain
metadata and loading via the real host `skill` tool. These tests are separate from live-model routing evidence.

Export catalogs as described in [tool-routing validation](integration-guidance.md), then opt into resource reads:

```sh
uv run python scripts/evaluate_integration_guidance.py \
  --catalog /tmp/pc-guidance/dsh.json --layered-skills --env-file .env \
  --output /tmp/pc-guidance/layered.json --skill-modes unloaded unavailable
```

Repeat `--catalog` for each host. The evaluator offers an optional reader of the actual shipped resource text and
records every requested resource. It does not force a Skill load or a data tool. Unavailable mode exposes no reader.
This controlled reader is evaluation infrastructure, not a native host loader or distribution source. MCP-backed
catalogs, including MiniMax, do not prove each product's native automatic discovery. Routing and argument validation
remain separate from transcript-bound reporting review. Preserve failed observations and evaluation limits.

## Recorded observations and completion boundary

Baseline master `0ed20c54` combined the domains in most entries, shipped no OpenClaw Skill, and registered only the
DSH router. MiniMax was already layered. This is source/packaging evidence, not a matched before/after model benchmark.

The [2026-09-16 raw record](https://github.com/oceanbase/powercontext/blob/master/e2e/integration-guidance/results/step37-layered-20260916.jsonl)
contains 400 observations from `step-3.7-flash`, temperature 0, 6,000 output tokens, automatic tool choice, ten host
catalogs, and English/Chinese prompts. The main 320-case matrix covers eight intents with optional reads and Skills
unavailable. Its original automatic verdict passed 289 cases; transcript review accepted **262/320**. All 80 ordinary
coding/sufficient-context cases avoided Skill and data calls. The 40 save calls selected the write, but one changed Scope
and one fabricated a citation in reporting. Missing-tool substitution, inactive inventory, invalid evidence claims,
response wrappers, and strengthened facts remain failed observations.

Two additional 40-case probes explicitly requested Skill reading before search/Handoff. They recorded actual resource
reads in 24 and 21 cases, respectively; automatic checks passed 24 and 15. These probes are not semantically qualified
acceptance results. The second uses strict standalone-carrier validation. Early combined read-error messages could hide
whether a batch mixed reading with an operation; the final evaluator records rejected calls and reports distinct missing
reader, mixed-operation, budget, and missing-path errors. Scope changes are now rejected on non-Handoff calls as well.
Regression tests protect these evaluation corrections; historical verdicts are preserved rather than relabeled.

The raw record separates complete routing/reporting acceptance from a selected tool or a successful file read. It does
not show that every model always follows Skill instructions, and does not qualify automatic discovery in every native
product. Model failures and D's earlier observations remain visible under #1450. E's implementation PR closes its child
issue only; the parent stays open for aggregate acceptance and disposition of residual model behavior.
