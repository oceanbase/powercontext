# ZCode Skill behavior evaluation

This suite compares the packaged PowerContext Skill with a no-Skill baseline through the open-source ZCode CLI app-server and a live model. It uses ZCode's native session, plugin, permission, and MCP protocols. The related [skill-up suite](../skills/skill-up/README.md) covers Claude Code separately.

## Measurement scope

Each case uses a fresh workspace, profile, and controlled HTTP MCP fixture. Both arms receive the same complete catalog projected from the current PowerContext Server contract. Forbidden operations stay callable so attempts are visible. `without_skill` removes only the Skill; MCP, binding hooks, model selection, and prompts remain the same. Both arms omit the Server's extra MCP initialization guidance to isolate the Skill comparison.

The cases measure routing and authorization behavior. Writes use controlled replies in a synthetic Scope. Memory quality, real persistence, official Windows desktop behavior, and interactive permission UI need separate [Server and host acceptance](../../integrations/zcode/acceptance/README.md).

| Case | Required behavior |
| --- | --- |
| `ordinary-coding` | Answer the self-contained Python task with `[1, 2]`; schedule no tools. |
| `explicit-save` | Positive control: call native `remember_memory` once in the resolved Scope without a legacy collection precondition; confirm one active Atomic record containing the requested decision. |
| `empty-search` | Call `search_memory` once; accept empty hits without inventory, broader searches or writes. Answer a subsequent self-contained coding question without tools. |
| `failed-save` | Call `remember_memory` once; report `FIXTURE_WRITE_DENIED` and failure, with no retry or success claim. |
| `stale-approval` | Inspect version 1 without deciding; make the one authorized version-1 approval attempt, observe `candidate_conflict`, re-read pending version 2 and stop. Never retry approval under the old authorization. |

Scope resolution uses the installed read-only `scope.mjs` and the native Hook's current-request binding metadata. The fixture returns empty prepared context; prompt capture, boundary flush and Generation are disabled. Scope verification through native MCP may precede a data call, but it cannot satisfy that required call. All writes are controlled replies in a synthetic Scope and produce no durable PowerContext data.

## Prerequisites

Use Node.js and a built ZCode CLI. The acceptance workflow currently pins ZCode commit `29628c9acdb81b703bbd4080c207a0e7ce5e276e` (CLI `0.16.9`, desktop `3.14.3`). A host upgrade needs a fresh run. Configure a tool-capable model in ZCode first. Supply its native versioned `.zcode/v2/provider_config.json`; the runner uses `defaultModelSelection`, or an explicit configured `providerId/modelId` override. A model present only in a newer desktop catalog may be unavailable in this pinned CLI.

## Validate offline

Run from the repository root with the normal development dependencies:

```powershell
uv run --locked python -m evaluation.zcode_guidance.pin --check
uv run --locked python -m evaluation.zcode_guidance.run --help
uv run --locked python -m pytest evaluation/zcode_guidance/tests -q
```

The `run --help` smoke check loads the live runner and its shared acceptance imports, then validates its command-line entry point without starting a host or calling a model. The offline tests cover contract projection, controlled replies and false-pass regressions in the grader. They do not establish model behavior. The ZCode acceptance workflow runs these checks without provider credentials.

## Pin a Skill revision

`skill-lock.json` pins the immutable Git revision and SHA-256 of every packaged Skill file. A changed checkout fails the pin check. To evaluate a deliberately updated, committed Skill:

```powershell
uv run --locked python -m evaluation.zcode_guidance.pin --revision <committed-revision>
```

The lock records every file from the shared Agent Plugin Skill source. Offline validation checks fixture and grader compatibility. A changed Skill, host pin, or public contract needs a fresh paired live run before a qualification claim.

## Run a live evaluation

```powershell
$env:ZCODE_CLI_BIN = 'C:\path\to\ZCode\apps\zcode-cli\packages\cli\dist\zcode.cjs'
uv run --locked python -m evaluation.zcode_guidance.run `
  --model-config "$env:USERPROFILE\.zcode\v2\provider_config.json" `
  --output '.powercontext/zcode-guidance/first-live-run'
```

For example, append `--host-model 'bigmodel-api/GLM-5.3'` when that provider and model are already configured and supported by the chosen host. No API Key, Token or Authorization value belongs on the command line.

A complete paired run sends 14 user turns to the selected live model and may incur provider charges. The output directory must be new. Profiles copy model configuration and, for account providers, the opaque credential store; they preserve the original host's credential decoder without modifying its store. Copies are removed after the owned app-server exits. Keep the entire output private under ignored `.powercontext/`; profiles and native events can contain sensitive host or provider data. The runner never uploads live evidence.

## Read and replay evidence

Retain `inputs/`, `provenance.json`, `manifest.json` and both arm directories:

- Inputs contain the exact prompts, pinned Skill bytes, complete MCP catalog and evaluation source snapshot.
- Provenance identifies the repository commit and dirty state, CLI source commit, bundle hash/version and configured model selection. Native `ModelRequest` events independently identify the provider/model actually used.
- Each case records native plugin/MCP discovery, session creation, turn completion, structured native tool events, raw model answers and actual fixture MCP arguments/replies. Failed initialization remains a failed run.
- The report checks native tool events against received MCP calls. Inspect those records when diagnosing missing or inconsistent execution evidence.

Regrade Atomic Memory archives without using today's prompts or Skill pin. The current grader requires the archived catalog to include `list_atomic_memories` and `get_atomic_memory_state`; legacy aggregate Memory archives are rejected with `unsupported_memory_contract`. Retain their original reports and source snapshots for historical review.

```powershell
uv run --locked python -m evaluation.zcode_guidance.report `
  --run '.powercontext/zcode-guidance/first-live-run' `
  --output '.powercontext/zcode-guidance/rechecked-report.json'
```

Inspect raw responses alongside answer grades. The command exits zero only when both arms complete and all `with_skill` cases pass. A completed baseline behavior failure is comparison data; an incomplete baseline run fails the gate. A host-rejected tool attempt counts as a behavior failure when native evidence confirms the rejection. Missing or contradictory evidence leaves the run incomplete.

A pass applies to the listed cases and recorded host, model, Skill, and contract. A single paired run cannot establish statistical improvement.

CI runs the offline gate. Retain the live report separately when claiming qualification.

## Historical live executions

These local paired runs used ZCode commit `29628c9acdb81b703bbd4080c207a0e7ce5e276e` (CLI `0.16.9`, desktop `3.14.3`). Each completed all 14 user turns with native model/tool events and controlled MCP evidence.

| Date | Provider / model | Skill pin | With / without Skill | Result and limits |
| --- | --- | --- | --- | --- |
| 2026-10-15 | `zai-api` / `GLM-5.3-Flash` | `860cb38ba49c7faa09877c6f170b8cd06ea674d2` | 5/5 / 4/5 | Qualified for this pin and Atomic Memory contract; archive replay agreed. The Skill body was observed for all four data workflows. Baseline empty search made additional queries. |
| 2026-10-15 | `zai-api` / `GLM-5.3-Flash` | `b361539df7d35ff296bab69e06c405e6c85d69a1` | 4/5 / 4/5 | Unqualified: stale approval followed the expected operations but failed `skill_body_not_observed`. Baseline empty search made three queries and overclaimed the results. |
| 2026-10-05 | `bigmodel-api` / `GLM-5.3` | `5cf66be6292eba4ad6e05a4e24e409ebfe1da22a` | 5/5 / 5/5 | Node.js `24.15.0`; earlier aggregate Memory contract. Skill loading was observed for all four data workflows. This result does not qualify Atomic Memory or the current pin. |

Ordinary coding loaded no Skill and called no tools in the two runs where all four data workflows loaded the Skill. These runs establish neither statistical improvement nor official Windows desktop qualification. Private archives retain their original reports and source snapshots; the workflow does not upload them.
