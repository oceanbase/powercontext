---
title: Plugin architecture and distribution
description: Shared execution, templates, host adapters, and setup.
---

# Plugin architecture and distribution

PowerContext plugins use the installed client to execute domain operations. Native adapters handle host events and permissions, and shared templates generate package resources. The server owns Scope and persisted artifacts. Plugins reuse the client's HTTP executor and Python environment.

## Execution

`powercontext.client.integration` accepts an operation ID, arguments, connection, absolute deadline, and optional Scope binding. The client validates requests and responses against its operation contract. Native Python hooks call it directly; TypeScript and Hermes use the serial JSON Lines worker exposed by `powercontext-hook`. Bub, framework adapters, and OpenDAL use the typed SDK directly.

```text
Codex / Claude Code / WorkBuddy -> Shared prompt -> Core
MiniMax ----------------------------------------> Core
TS hosts / Hermes -> JSONL worker ---------------> Core
                                                   |
Bub / framework adapters / OpenDAL ------------> Client SDK -> HTTP API

Native MCP -> host Scope/auth handling -> Server MCP
```

Core bounds Hook responses to 1 MiB, disables redirects, and enforces the request deadline. Ordinary SDK calls retain their HTTP client's transport policy, including large report downloads and explicitly configured redirects.

Adapters return results in the host's native format. Native MCP calls pass through the host's checks and MCP client to Server MCP. Codex binds tool arguments to the current Scope and supplies authorization through a native credential helper. Its adapter owns these steps alongside the shared prompt hooks.

Automatic prompt hooks resolve Scope, prepare context, and capture the prompt when capture is enabled. A checkpoint starts only after the Server acknowledges the Source. Cursor progress, call limits, and the shared deadline bound processing. If a write outcome is unknown, preserve that state and do not retry automatically. Source acceptance does not prove that Memory exists.

Shared checkpoint helpers take progress positions from confirmed Sources and record uncertain writes before propagating errors or cancellation. Python adapters use `Checkpoints.attempt()`; TypeScript adapters use the generated `Checkpoints.run()` and `flushThrough()`. Host code chooses when to checkpoint and owns its native pending-work state.

Adapters control event timing, tool visibility, user confirmation, Scope identity, and output channels. Pi runs checkpoints at lifecycle boundaries. OpenClaw limits tools to eligible sessions, and MiniMax reads its named MCP endpoint with private overrides. DSH uses a separate bounded GET for OpenAPI discovery.

Classify failures through typed results rather than exception text. Preserve rejection, conflict, invalid input, invalid response, and unknown write outcomes. Empty context is a valid result. Failed automatic recall lets the host continue; invalid tool bindings reject the call. Diagnostics omit credentials, prompts, response bodies, and stack traces.

`powercontext_integrations.host.HostAdapter` is the common boundary for setup, resource preparation, and doctor. Single-host setup and `setup select` call the same `install()` method: check the installed client, resolve connection policy, run the native installer, verify installation, then save connection settings. Failed verification does not persist the new connection. Each host supplies its native module, accepted options, and whether its installer already verifies the result. Native installers retain their rollback rules and call `prepare()` at the appropriate staging or cache boundary.

Setup, doctor, configuration selection, and distribution read the same Target catalog from the selected repository source. Every target has `powercontext setup <target>` and `powercontext doctor <target>`, including Python packages and portable plugins. Python owns installation and diagnostics. `system.py` registers commands, while native modules such as `codex.py` and `claude_code.py` supply installation/discovery and effective configuration; TypeScript only registers commands and forwards configuration to `powercontext-hook --doctor`.

Common diagnostics check client prerequisites and transport policy. Optional `doctor <target> --server` and native `/pc doctor` use the same read-only liveness, readiness, and context-schema checks. Dependency failures remain visible on HTTP 503; a failed liveness probe skips subsequent checks. Diagnostic output excludes private response text. Installation checks and Server probes have distinct inputs: a running host supplies its actual connection, while the CLI reads discoverable configuration. An unreadable native configuration fails instead of guessing an endpoint. Setup failures use `SetupError` with shared constructors for repeated command and input failures.

```text
Repository rules (source / ref)
     Target catalog + templates
          |
          +-- build -> shared renderer -> native package
          |
     HostAdapter (Python)
          +-- setup  -> prerequisites -> connection -> generate/install -> verify -> save
          +-- doctor -> prerequisites + native discovery + transport
          |                  +-- optional Server checks

Host command -> connection -> Client Server checks -> native display
```

## Agent Plugin baseline

- `integrations/agent-plugin/powercontext/` is the editable baseline: standard Skills, workflow references, and MCP configuration. `integrations/agent-plugin/operations.json` selects the minimum shared toolkit; request schemas and descriptions come from the client contract. Other hosts project this baseline rather than define different workflows.
- `integrations/distribution/powercontext_integrations/assets/` holds native format templates and bindings. `resources.json` supplies native MCP fields and registration formats; `tool-bindings.json` preserves existing tool names. Scope resolution, direct current-work Handoff, Memory inventory, and candidate inspection follow the same methodology.
- `resources.py` serves setup and distribution. DSH registers baseline references as runtime Skills; other Skill hosts receive files. Native MCP wrappers, endpoint paths, schema metadata, and credential fields are format adaptations. WorkBuddy's settings merge uses the same projection. Host code retains interactive approval and tool visibility.
- `integrations/distribution/powercontext_integrations/assets/targets/` declares source layout and native events, handlers, operations, effects, and failure behavior. Operation IDs come from the client contract. Hook declarations describe native bindings; they are not an executable lifecycle plan. Native event APIs remain adapter code. There is no separate capability manifest or source probe.
- `scripts/build_agent_distributions.py` overlays rendered resources on native adapters and records file hashes. Outside the baseline, Skills, MCP files, tool schemas, guidance, and bridges are generated outputs. Native packages include `tools.generated.json`; SDK adapters read it without embedding duplicate schemas in JavaScript bundles.

```text
Agent Plugin baseline + API contract + Target/templates
                         |
                   Shared generator
                         |
            Generated resources + native adapter
                         |
                 Target-native plugin/package
```

`integrations/distribution/` owns host rules and templates independently of the client release. The client supplies `powercontext-hook` and shared Server diagnostics. Setup projects resources before native installation and refreshes installed caches where required. Prompt hooks supply the resolved Scope even when retrieval is empty, so MCP callers reuse the same binding without a host-specific resolver command. Existing MCP configuration survives regeneration; an explicit endpoint change updates only its URL. Credentials and user settings stay with the host's configuration flow. Restart the host after setup.

## Develop and distribute

Install `powercontext[cli]` and expose `uvx`, `npx`, and `powercontext-hook` on PATH. No additional management package is installed. Offline source bundles preserve the repository layout: the marketplace manifest, distribution rules, Agent Plugin baseline, and selected host sources. Select the bundle root with `--source`, not its plugin subdirectory. Setup loads Python rules directly from `integrations/distribution/` in the selected `--source/--ref`. Remote sources use a shared Git checkout; local sources are read in place. An explicit remote setup refreshes only an unmodified managed checkout. Successful setup saves its source alongside the connection and installation location. Doctor reuses that source without fetching; `--source/--ref` can explicitly select another source.

The client retains the source loader, Core Hook, and shared Server diagnostics. Profiles, templates, native installation, and discovery remain repository code. `powercontext-hook --doctor` and the default CLI doctor need no integration source. For development, use the checkout directly:

```bash
make agent-resources
make agent-distributions
uv run python scripts/build_agent_distributions.py --list
powercontext setup pi --source /path/to/powercontext
powercontext setup minimax --source /path/to/powercontext
powercontext setup langchain --source /path/to/powercontext --python /app/.venv/bin/python
powercontext setup agent-plugin --source /path/to/powercontext --destination /app/plugins/powercontext
powercontext doctor langchain --server
```

`agent-resources` materializes template outputs for local development and native source loading. `agent-distributions` produces standalone packages under `build/agent-distributions/<target>/`; packages contain their rendered resources and adapters. Build before using a raw checkout with a host's plugin command. The builder preserves foreign or modified output files. To change generated content, edit its template and rebuild.

Each generated plugin or integration package loads through its native host and uses the installed client for shared execution. Repository rules can change independently when they use existing client operations and the worker protocol. New client capabilities require a compatible client release.

Python packages install through `uvx` into the explicit application interpreter, the saved interpreter, or the current project's `.venv`, in that order. Setup never selects the CLI's tool environment implicitly. MiniMax uses its native plugin directory and verifies discovery with `mcode plugin list`; portable plugins require a destination and registration with the loading agent. Setup records installation locations for later doctor calls. A multi-target selection with more than one directory target places each under `<destination>/<target>`.

The catalog command reports profiles used by the builder, including language and hook operations. It describes declared native bindings, not runtime service availability or every dynamically permitted tool. Use `powercontext doctor integrations` for installed host status and each host's actual tool catalog for its current permissions.

To add a host, implement its native event, approval, and output adapter, add a Target profile, and bind its native formats. Use the baseline toolkit and workflows. Adapt host response envelopes at the boundary; do not fork the methodology.

## Where to make changes

Use the behavior being changed to choose the source to edit:

```text
Change or fix
+-- Shared workflow / Skill -> Agent Plugin baseline -> regenerate
+-- Execution / validation / deadline -> Core / Client -> client release
+-- Native event / permission / auth / output -> host adapter -> plugin update
`-- Install / discovery / format / argument wiring -> distribution -> regenerate/setup

Core already expresses the required behavior?
+-- No  -> implement it at the boundary above
`-- Yes -> does the adapter pass the needed inputs and handle the result?
           +-- No  -> repair Target / adapter wiring
           `-- Yes -> confirm with an observable behavior test
```

The shared executor enforces request timeouts and the overall deadline. Codex, Claude Code, and WorkBuddy settings default to a request timeout of three seconds and a prompt budget of six seconds. Pi, DSH, and OpenCode Target profiles must pass the resolved Server URL to their installers so credentials bind to the runtime endpoint. Codex's adapter owns its native MCP credential helper and authorization diagnostics; the helper runs with the installed client's Python interpreter. A shared implementation covers a fix only when the host passes the right configuration and handles the result correctly.

## Method and validation

Start from observable constraints: who selects Scope, which event owns a write, what confirms completion, and which boundary stops execution. Keep event, operation, effect, failure policy, and transport independent. Model-generated content uses the same operation boundary; text cannot grant permissions or replace a confirmed result.

Use ablation to justify a boundary: remove validation, deadline, binding, or unknown-outcome handling in an isolated fixture and confirm the relevant failure becomes observable. Preserve that behavior in the existing Core or adapter test. Do not maintain a second evaluation pipeline, guessed support table, or tests for generated implementation details.

Verify the distribution boundary with only the client installed: add a Target to a separate source checkout and select it with setup. Doctor must reuse that source while the client version and Core remain unchanged.

Run `make check` and the affected Python or native package tests. Distribution tests cover resource completeness, reproducibility, and preservation of user configuration. Host tests cover event mapping, permissions, and outputs; shared Core tests cover validation, deadlines, and uncertain writes. Live model quality is a separate measurement.
