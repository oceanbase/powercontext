# ZCode Skill behavior evaluation

This suite runs the packaged PowerContext Skill through the **actual open-source ZCode CLI app-server and a live
model**. It adapts the paired guidance evaluation used by [skill-up](../skills/skill-up/README.md) to ZCode's native
session, plugin, permission and MCP protocols. It does not invoke skill-up's Claude Code harness.

Each case has a fresh workspace, user profile and controlled HTTP MCP fixture. Both arms receive the same complete
catalog projected by the real PowerContext Server from its current contract. Forbidden operations remain callable:
attempts are recorded and fail the relevant rule. The `without_skill` arm removes only the PowerContext Skill; MCP,
binding Hooks, model selection and prompts remain identical. Neither arm receives the real Server's additional MCP
guidance in `initialize`, keeping that guidance from obscuring the Skill comparison.

## Cases and rules

| Case | Required behavior |
| --- | --- |
| `ordinary-coding` | Answer the self-contained Python task with `[1, 2]`; schedule no tools. |
| `explicit-save` | Positive control: call native `remember_memory` once in the resolved Scope, save the requested decision and report the confirmed success. |
| `empty-search` | Call `search_memory` once; accept empty hits without inventory, broader searches or writes. Answer a subsequent self-contained coding question without tools. |
| `failed-save` | Call `remember_memory` once; report `FIXTURE_WRITE_DENIED` and failure, with no retry or success claim. |
| `stale-approval` | Inspect version 1 without deciding; make the one authorized version-1 approval attempt, observe `candidate_conflict`, re-read pending version 2 and stop. Never retry approval under the old authorization. |

Scope resolution uses the installed read-only `scope.mjs` and the native Hook's current-request binding metadata.
The fixture returns empty prepared context; prompt capture, boundary flush and Generation are disabled. Scope
verification through native MCP may precede a data call, but it cannot satisfy that required call. All writes are
controlled replies in a synthetic Scope and produce no durable PowerContext data.

These cases measure **routing and authorization behavior**, separately from the
[real Server acceptance](../../integrations/zcode/acceptance/README.md). They establish neither memory quality nor
official Windows desktop behavior. Fixture MCP operations are pre-authorized within the disposable test; this suite
does not evaluate an interactive permission UI.

## Validate offline

Run from the repository root with the normal development dependencies:

```powershell
uv run --locked python -m evaluation.zcode_guidance.pin --check
uv run --locked python -m evaluation.zcode_guidance.run --help
uv run --locked python -m pytest evaluation/zcode_guidance/tests -q
```

The `run --help` smoke check loads the live runner and its shared acceptance imports, then validates its command-line
entry point without starting a host or calling a model. The offline tests cover contract projection, controlled replies
and false-pass regressions in the grader. They do not establish model behavior. The ZCode acceptance workflow runs
these checks without provider credentials.

`skill-lock.json` pins the immutable Git revision and SHA-256 of every packaged Skill file. A changed checkout fails
the pin check. To evaluate a deliberately updated, committed Skill:

```powershell
uv run --locked python -m evaluation.zcode_guidance.pin --revision <committed-revision>
```

Update the pin together with new live evidence; changing the pin alone grants no qualification.

## Run a live evaluation

Use Node.js and a built ZCode CLI. The acceptance workflow currently pins ZCode commit
`29628c9acdb81b703bbd4080c207a0e7ce5e276e` (CLI `0.16.9`, desktop `3.14.3`). A host upgrade needs a fresh run.
Configure a tool-capable model in ZCode first. Supply its native versioned `.zcode/v2/provider_config.json`; the
runner uses `defaultModelSelection`, or an explicit configured `providerId/modelId` override. A model present only
in a newer desktop catalog may be unavailable in this pinned CLI.

```powershell
$env:ZCODE_CLI_BIN = 'C:\path\to\ZCode\apps\zcode-cli\packages\cli\dist\zcode.cjs'
uv run --locked python -m evaluation.zcode_guidance.run `
  --model-config "$env:USERPROFILE\.zcode\v2\provider_config.json" `
  --output '.powercontext/zcode-guidance/first-live-run'
```

For example, append `--host-model 'bigmodel-api/GLM-5.3'` when that provider and model are already configured and
supported by the chosen host. No API Key, Token or Authorization value belongs on the command line.

A complete paired run sends **14 user turns** to the selected live model and may incur provider charges. The output
directory must be new. Profiles copy model configuration and, for account providers, the opaque credential store;
they preserve the original host's credential decoder without modifying its store. Copies are removed after the
owned app-server exits. Keep the entire output private under ignored `.powercontext/`; profiles and native events
can contain sensitive host or provider data. The runner never uploads live evidence.

## Evidence and replay

Retain `inputs/`, `provenance.json`, `manifest.json` and both arm directories:

- Inputs contain the exact prompts, pinned Skill bytes, complete MCP catalog and evaluation source snapshot.
- Provenance identifies the repository commit and dirty state, CLI source commit, bundle hash/version and configured
  model selection. Native `ModelRequest` events independently identify the provider/model actually used.
- Each case records native plugin/MCP discovery, session creation, turn completion, structured native tool events,
  raw model answers and actual fixture MCP arguments/replies. Failed initialization remains a failed run.
- The report compares scheduled native tools with received MCP calls. ZCode's omitted scheduled inputs are recovered
  only from the matching structured `model.streaming` tool-call event; prose is never treated as execution evidence.

Regrade archived bytes without using today's prompts or Skill pin:

```powershell
uv run --locked python -m evaluation.zcode_guidance.report `
  --run '.powercontext/zcode-guidance/first-live-run' `
  --output '.powercontext/zcode-guidance/rechecked-report.json'
```

SHA-256 checks detect changed or missing archived bytes; they are integrity checks, not signed attestations. Literal
answer rules are deliberately transparent, and raw responses remain necessary for semantic review. Equivalent plain,
inline-code and fenced-code list rendering is accepted. Regraded reports identify the actual grader's source hash.
CLI exit status
is zero only when both arms complete and all `with_skill` cases pass. Baseline results are reported separately;
baseline behavior failure does not fail the gate, while incomplete baseline execution does. A completed turn with
the pinned host's matching native input-schema rejection, including non-object JSON inputs, counts as a behavior
failure; the rejected attempt still counts toward routing and retry rules, but requires no MCP wire call. Missing
rejection evidence, contradictory handler events or unexplained native/wire differences leave execution incomplete.
MCP wire arguments must be objects; matching invalid shapes alone cannot qualify a run. One paired run is not
statistical evidence that the Skill improves behavior. A passing run means the listed cases passed for that exact
host, model, Skill and contract, not that every possible instruction is safe.

CI runs the offline gate. Live model evaluation is an explicit maintainer run after changing the Skill, host pin or
public contract; retain its report before claiming live qualification. CI does not replace missing live evidence with
a scripted model.

## Recorded live execution

A local paired execution on 2026-10-05 used the CLI commit and version above, Node.js `24.15.0`, provider
`bigmodel-api`, model `GLM-5.3`, and the Skill pinned to `5cf66be6292eba4ad6e05a4e24e409ebfe1da22a`.
All 14 user turns completed with native model/tool events and controlled MCP wire evidence. Both arms passed **5/5**
cases. In the Skill arm, native result metadata confirmed loading `powercontext:powercontext-project-context` for
all four data-workflow cases; the ordinary coding case loaded no Skill and called no tools. The no-Skill arm loaded
no PowerContext Skill. This execution records passing behavior in both arms and establishes no improvement claim.
The full private input/evidence archive supports replay; it is not uploaded by the workflow.
