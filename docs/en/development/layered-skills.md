---
title: Layered PowerContext Skills
description: Discover intent first, then load only the relevant Memory, Handoff, or Review workflow.
---

# Layered PowerContext Skills

The Skill entry is a small intent router. It explains when to search, list, save, hand off, or inspect candidates;
workflow references carry detailed arguments, result interpretation, and approval boundaries.

Ordinary coding and summaries with sufficient current context need neither a Skill load nor a PowerContext call.
An explicit operation still needs its real tool and observed result. An agent may call a self-contained tool directly,
or load the relevant domain when it needs detail. Reading the router first, loading all domains, and loading before
every response are not prerequisites. English and Chinese intent phrases live in actual Skill descriptions.

## Host layout

| Hosts | Discoverable entry | Workflow detail |
| --- | --- | --- |
| Codex, Claude Code, WorkBuddy, portable Agent Plugin, Pi, OpenCode | `powercontext-project-context` | Local `references/scope-memory.md`, `work-handoff.md`, and `review-publication.md`. |
| Hermes | `powercontext:powercontext-project-context` | The same three reference domains with Hermes tool names and human Review commands. |
| MiniMax | `powercontext-project-context` | Existing Scope/Memory, Handoff, Review and HTTP boundary references plus examples. |
| OpenClaw | `powercontext-project-context` | Packaged Scope/Memory and Handoff references; no inventory or candidate Review capability. |
| DSH | Runtime `powercontext-project-context` router | Independently registered `powercontext-memory`, `powercontext-handoff`, `powercontext-review`; no filesystem reference dependency. |

File-backed hosts follow MiniMax's existing local-reference organization. Keep references beside the installed entry.
OpenCode's npm archive includes the complete Skill directory. WorkBuddy's installer resolves Python and Scope-helper
placeholders in all installed Markdown resources. OpenClaw uses the SDK's manifest `skills` directory mechanism.
DSH uses its existing runtime Skill service, so the host can discover domain descriptions and load one domain directly.

All maintained Skill entries use `powercontext-project-context`, including runtime registration and installation
paths. There are no alternate entry names. DSH's separately loadable domain Skills keep their domain-specific names.
Framework adapters and Bub do not expose these Skill entries.

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

Run `uv run pytest tests/test_layered_skills.py` to read each shipped entry and reachable reference, check missing-resource diagnostics, and inspect OpenCode/OpenClaw npm archive contents. Installer regressions exercise actual
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

Explicit `skill_search` and `skill_handoff` probes must successfully read their respective Memory or Handoff workflow
before the data operation. Reading only the router, a different domain, or reading after the operation does not qualify.
The report records the expected resource and resources read before the operation, preserving any more specific failure.
Ordinary requests do not require a Skill read, and unavailable mode still evaluates independently usable tools.

Run-specific configuration, transcripts and findings belong in an evaluation report; the PR records delivery status.
