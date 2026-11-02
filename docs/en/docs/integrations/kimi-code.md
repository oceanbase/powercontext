---
title: Kimi Code
description: Project memory, task handoffs and reviewed Experience/Skill workflows through Kimi Code native MCP.
status: community
---

# Kimi Code

The integration is experimental and supports minimal, recommended and full profiles. The default minimal profile provides Memory read/write, automatic Source capture and context injection. An explicit Scope can be shared across projects, or workspace bindings can select a separate Scope for each working directory.

## Install

Install Kimi Code and Node 24+, and install PowerContext with CLI/Server extras from the same checkout used for the plugin:

```text
uv pip install -e ".[cli,server]"
powercontext server run --env-file .env
```

For an authenticated Server, set POWERCONTEXT_KIMI_TOKEN before setup so native MCP authentication is enabled. For an unauthenticated Server, leave it unset. An existing endpoint keeps its established authentication mode; a missing runtime token reports an error.

In another terminal, create or select an existing Scope using the Server/CLI/Dashboard, then run:

```text
powercontext setup kimi-code --source /path/to/powercontext --server-url http://127.0.0.1:8000 --scope-id <existing-scope-id>
powercontext doctor kimi-code
kimi
```

The source must be a local checkout or the unconfigured plugin directory from it; the installed managed copy is not an update source. Kimi and Node must be on PATH; KIMI_CLI_BIN can point to a real binary or built main.mjs for diagnostics and acceptance. KIMI_CODE_HOME selects the Kimi home, defaulting to ~/.kimi-code.

Setup installs a managed copy at <Kimi home>/plugins/managed/powercontext and registers it in plugins/installed.json. It leaves model configuration and other plugins intact, preserves plugin/MCP disablement, and restores previous registration and plugin contents if publication fails. If rollback itself is denied, setup reports where the previous contents remain; restore the reported target from that path before retrying. Repeat setup to update; omitted endpoint, Scope and capture options preserve installed settings. Reinstall from the source to change files in the managed copy.

Open a **new session** after installation or reconfiguration; an existing session may retain its old Skill or MCP configuration. The plugin is user-wide, not repository-local. Setup does not create a Scope or modify bindings. HTTPS and loopback HTTP are supported; non-loopback plaintext HTTP is rejected.

## Authentication and first use

For an authenticated Server, export the raw token in the terminal that launches Kimi:

```powershell
$env:POWERCONTEXT_KIMI_TOKEN = '<raw-server-token>'
```

On Bash use export POWERCONTEXT_KIMI_TOKEN='<raw-server-token>'. Hook and MCP consume the same variable; no token is stored in the plugin. Omit it for an unauthenticated Server. Setup does not load a separate environment file for Kimi.

Ask Kimi to save a project decision, then search it in a fresh session. The plugin exposes search_memory, remember_memory and get_artifact_revision. Current Memory results carry Atomic Memory Artifact references; exact readback uses the returned family, artifact_id and revision. Automatic Source capture is separate from an explicit save.

Ordinary text prompts attempt context preparation and Source capture. Mixed prompts retain text only; unsupported or pure-media input is skipped. Capture skips oversize/sensitive input and can be disabled:

```text
powercontext setup kimi-code --source /path/to/powercontext --no-capture-prompts
```

The Hook has a 4-second total budget and returns context through Kimi's message field. Failures continue ordinary work and emit redacted stderr diagnostics. A capture response lost after sending is unknown and is not retried automatically. Separate submissions, including identical text, get separate Source IDs.

Explicit Memory operations need no Generation API. Automatic Memory requires the Server's Generation configuration and scheduled processing; Source acceptance alone does not prove Memory creation, and the plugin does not flush. Kimi credentials do not supply Server Generation credentials.


## Workspace Scope binding

To use separate project Scopes within one Kimi home, install or update in workspace mode:

```text
powercontext setup kimi-code --source /path/to/powercontext --workspace-scope
node "<Kimi home>/plugins/managed/powercontext/scripts/scope.mjs" bind --cwd /absolute/project --scope-id <existing-scope-id>
node "<Kimi home>/plugins/managed/powercontext/scripts/scope.mjs" resolve --cwd /absolute/project
powercontext doctor kimi-code --cwd /absolute/project --json
node "<Kimi home>/plugins/managed/powercontext/scripts/scope.mjs" unbind --cwd /absolute/project
```

Open a new Kimi session after changing the installation mode. An explicit `--scope-id` overrides workspace bindings; `--workspace-scope` clears that explicit selection, and the options are mutually exclusive. Omitting both on an update preserves the installed mode. Initial installation requires one of them.

The Hook, Scope script and doctor use the same Server resolution: explicit Scope, then the workspace binding, then the Server default. A missing or broken binding target fails resolution without client fallback or Scope creation. Explicit Scope resolution does not require a workspace key: `scope.mjs resolve` may omit `--cwd` in that mode. Workspace-mode resolution and all bind/unbind operations require an absolute working directory.

The binding key uses integration `kimi-code`, kind `workspace`, and SHA-256 of the absolute working directory after native real-path resolution; it contains no raw path. Existing directories share a binding across symlink/junction aliases and case variants recognized by the filesystem. On POSIX, symlinks are followed before interpreting subsequent `..` components; Windows uses its native path semantics. Use the Kimi session's working directory; subdirectories retain separate identities, without Git-root discovery. If real-path resolution fails, the key uses the lexically normalized absolute path. Bind existing, accessible directories and bind again if their resolved identity changes. Session bindings are outside this integration's scope.

Bind/unbind changes only that workspace's Server-owned binding. The JSON result keeps `binding` separate from subsequent resolution: a saved binding may be masked by an explicit Scope (`bound_scope_is_current: false`), or remain saved when resolution fails. An unconfirmed write stays `unknown` and is not retried automatically. Resolve again before claiming the active Scope changed.

For native MCP operations, use this request's Hook binding metadata or a successful read-only Scope-script result for the actual workspace; never infer a Scope from historical content. Doctor reports the current Scope ID and selection strategy (`explicit` or `workspace_or_default`); the latter does not assert which fallback matched.

## Select a workflow profile

Advanced tools are opt-in. Initial setup defaults to `minimal`; omitted profile options on an update preserve the installed choice, including legacy installations without a profile setting. Profile changes preserve the endpoint, authentication mode, Scope, capture preference, other Kimi plugins and existing disablement.

```text
powercontext setup kimi-code --source /path/to/powercontext --profile recommended
powercontext setup kimi-code --source /path/to/powercontext --profile full
powercontext doctor kimi-code --mcp --json
```

To change only the installed profile without replacing plugin files, use `powercontext config agent kimi-code --profile full`. Omit `--profile` when updating only the connection or Scope to preserve the current native tool selection.

Open a new session after updating. Profile selection controls native MCP tool registration:

| Profile | Tools | Workflows |
| --- | --- | --- |
| `minimal` | 3 | Search, explicit save and exact Artifact readback. |
| `recommended` | 27 | Memory inventory/changes, Source operations, Work Contract, Handoff, continuation, acknowledgment and Task Outcome. |
| `full` | 44 | Recommended workflows plus Experience, managed/external Skill and candidate inspection/decisions. |

The checked-in lists are generated from the Server MCP catalog and capability contract. Larger profiles add model tool-schema overhead on each call; only enable the workflows needed. Optional Handoff reporting, automatic flush, Scope administration and Artifact publication are not enabled by these profiles. Tools requiring Server Generation or an external Skill registry still require that Server capability; selecting a profile does not configure providers or grant authorization.

The router Skill uses the active profile and loads workflow references on demand. For a requested Work Contract, inspect the objective, facts, scope and completion criteria before writing. An ordinary Handoff request prepares a temporary carrier; only an explicitly requested durable milestone is committed. Continue with the exact prepared carrier or committed Revision, acknowledge the same target after checking live state/capability/authorization, and preserve the returned Receipt SourceRef in the Task Outcome. A partial prepare/commit failure is not a completed handoff and does not authorize another boundary write.

In full mode, propose or generate an Experience/Skill only on request, inspect its candidate, and use the current `expected_version` for an authorized approve/reject/revise decision. After a conflict, reread the changed proposal before deciding again. Pending candidates are not approved Artifacts. Read approved results using their exact ArtifactRef. External Skill resolution/import uses the inspected `external_skill_id` and `fingerprint`; import does not authorize approval, installation or execution. External registrations belong to the Server and do not imply Kimi-local Skill discovery.

Use the current Scope for all operations and the actual native tool names in Kimi's catalog; Kimi may shorten long qualified names. Missing, denied or failed MCP operations remain incomplete. Guidance never authorizes HTTP/shell domain writes as a substitute. Setup and profile selection do not bypass host approvals.

## Diagnose and remove

doctor is readonly: it checks registration/enablement, Node, MCP/Hook declarations, Server readiness and Scope access. It does not prove that a real MCP call or model injection succeeded. Missing/wrong tokens surface as authentication_failed; a disabled plugin/MCP stays disabled after setup. Incorrect endpoints or inaccessible Scopes require explicit correction and a new session.

`--mcp` additionally initializes a bounded MCP transport session, discovers Server tools (including pagination), compares the selected profile, and closes the session. `mcp` reports installed declarations; `mcp_discovery` reports current Server discovery. Neither proves that the current Kimi window loaded those tools or that a domain operation succeeded. Discovery executes no domain tools and does not capture or flush.

Use Kimi's /plugins remove powercontext to remove registration. Kimi keeps the managed copy on disk. Delete only the PowerContext-owned managed directory after verifying its .powercontext-owned marker and removal of its registration. No global Hook or project configuration is added.

## Inspect Hook observations

Run from the workspace where the problem occurred:

```text
powercontext doctor kimi-code --runtime --json
# Select one exact session; omission selects the workspace's latest history:
powercontext doctor kimi-code --runtime --cwd /path/to/workspace --session-id <Kimi session ID> --json
```

`runtime` reports the matching Scope, start time and `scope`, `prepare`, `capture`, and `injection` stages. Prepare `ready` means validated context was returned; `empty` is normal. Capture `accepted` confirms only the Source receipt, not Memory generation. Injection `emitted` confirms local historical-context output, not host/model receipt or adoption. Failures retain safe codes. A sent capture whose response cannot be confirmed remains `unknown`, without automatic retry.

Missing history is `not_observed`; changed configuration is `configuration_mismatch`. Attempts older than five minutes are `stale`; attempts without a completion marker are `incomplete`. These optional results do not fail otherwise healthy installation/connectivity checks. Records do not establish that the current window loaded the plugin, and workspace history does not identify the current session. Corrupt, unreadable or over-capacity storage reports a separate failure.

Observations live under `<Kimi home>/powercontext/runtime`, isolated by workspace, session and configuration fingerprints. Configuration covers endpoint, Scope, capture preference and authentication mode, excluding tokens. Independent files prevent late completion from overwriting newer attempts. They contain no prompts, context bodies, answers, raw workspace/session/endpoint identities or credentials, and survive plugin updates. Writes use bounded directory enumeration; at 64 records, retain the latest 32 completed records and unfinished attempts. Concurrent writes may temporarily exceed this retention threshold; within the scan budget, history stays readable and the next write prunes completed records. Enumeration stops at 128 directory entries and reads at most 4 KiB per record. Unowned files are preserved. Storage failures degrade observation while existing prepare/capture continues.

The local status script needs neither Server nor runtime token and never maintains or deletes records:

```text
node "<Kimi home>/plugins/managed/powercontext/scripts/status.mjs" --cwd /path/to/workspace --session-id <Kimi session ID>
```

In workspace mode, history retains the Scope resolved at that time; offline queries do not recheck later binding changes. Use the doctor scope result for current resolution. Doctor still probes current Server/Scope connectivity. `--runtime` adds local history without capture or flush; successful current probes do not rewrite historical outcomes.

## Verification

Pinned Kimi 2.1.1 with Node 24.21.0 is qualified on Windows and Linux for minimal/recommended/full native MCP registration, temporary and durable cross-session Handoff, exact continuation/Receipt/Task Outcome readback, Experience and managed Skill proposal/approval/readback, candidate version conflicts, external Skill fingerprint resolution/import, missing tools, host Hook denial, and full-profile workspace isolation/explicit override. These tests use a real CLI, Server and SQLite with controlled agent inference; they do not qualify live-model workflow routing.

Windows, Kimi 2.1.1 (npm CLI), Node 24.15.0 passed the real CLI + real Server + SQLite chain, including native MCP save/read/search, actual prompt injection, scheduler worker Generation with exact Source evidence, capture disablement, incorrect credentials and Server outage. Both model services in this repeatable acceptance test used controlled inference.

The same Windows, Kimi 2.1.1 and Node 24.15.0 environment also passed a manual live acceptance run against the official DeepSeek OpenAI Chat Completions API (model = deepseek-v4-flash): the live model called native MCP to save, read the exact revision and search in a fresh session; live Generation created Memory from Hook-captured Source with provenance; a fresh session answered from automatically injected Memory without calling tools. This run used an isolated Kimi home, Scope and SQLite database. It is separate from the automated tests and does not establish correct tool selection for arbitrary prompts. That live-model run does not qualify other versions/platforms, HTTPS ingress or TUI reload.

Run the focused checks from the checkout:

```text
node --test integrations/kimi-code/plugins/powercontext/tests/plugin.test.mjs
uv run pytest tests/test_cli_kimi_code.py
```

Set KIMI_CLI_BIN to the installed real binary or built main.mjs, then run uv run pytest tests/e2e/test_kimi_code_host.py tests/e2e/test_kimi_code_workflows.py. This opt-in test uses disposable Kimi homes, workspaces, Server database and local inference; it needs no live model key and does not modify the user's Kimi profile. An unset variable skips host acceptance and is not a passing qualification.

The [Kimi Code acceptance workflow](https://github.com/oceanbase/powercontext/actions/workflows/kimi-code-acceptance.yml) pins Kimi 2.1.1 and Node 24.21.0 and runs Hook tests plus real-host acceptance on Linux and Windows for pull requests and master updates. Both inference services are controlled; no live provider key is required. A skipped host test fails CI. Artifacts contain CLI provenance, JUnit results and acceptance summaries, excluding Kimi profiles and databases.

## Automatic input privacy

See [Automatic prompt privacy](index.md#automatic-prompt-privacy) for this host's capture default, disable control, credential recognition limits and independent read-only query handling. Recognized inputs are skipped for capture; accepted Source content and event identity are not rewritten.
