# ZCode host acceptance

This suite runs an existing ZCode CLI bundle against a real PowerContext Server and a disposable SQLite database.
It does not download a host, deploy a remote service, or operate the desktop UI.

## Continuous integration

[ZCode acceptance](../../../.github/workflows/zcode-acceptance.yml) runs on every pull request, every push to master,
weekly on Sunday at 03:23 UTC, and by manual dispatch. It builds the actual open-source CLI on Windows from ZCode commit
`29628c9acdb81b703bbd4080c207a0e7ce5e276e` (ZCode 3.14.3 / CLI 0.16.9), using Node 24.15.0, pnpm 10.33.2 and the
host's frozen lockfile. Dependency install scripts are disabled; only the CLI and its required workspace builds run.

The job runs all plugin Node tests, including the actual host test, then explicitly selects two independent
controlled-model acceptances covering P3-01 through P3-09. Host build errors, missing prerequisites and failed scenarios
fail the job. The controlled inference fixtures need no model keys or repository secrets. This is actual CLI + real
Server acceptance with controlled inference; it does not establish live-model or official desktop acceptance.

The artifact `zcode-controlled-<run-id>-<attempt>` retains build provenance, each run's `summary.json` and only selected
`evidence/*.json` for 14 days, including completed evidence from failed runs. Private profiles, model configuration,
databases, workspaces and raw transcripts are excluded. The Actions summary lists individual scenario results.
P3-10 stays `not_run`; compact and reconnect limitations remain explicit in the evidence.

To upgrade the supported host, change `ZCODE_REF` in the workflow to an exact reviewed commit, check its Node/pnpm and
build requirements, and run the complete job. Keep the host pin and this guide consistent. Scheduled runs check the
fixed host against current master and dependency availability; they do not silently adopt newer ZCode releases.

## Prerequisites

- Install this checkout with its development dependencies and Server extras.
- Build the open-source ZCode CLI separately; Node 24 or later must be on PATH.
- Use a directory writable by the same Windows user that runs Node and Python. An inaccessible plugin directory
  is an installation failure, even when its path appears in `plugins.dirs`.
- Keep `.powercontext/` ignored by Git. A run's profile contains private model configuration and may contain
  an opaque copy of account credentials. Share only reviewed `summary.json` and `evidence/` files.

## Controlled model

```powershell
$env:ZCODE_CLI_BIN = 'C:\path\to\ZCode\apps\zcode-cli\packages\cli\dist\zcode.cjs'
uv run pytest tests/e2e/test_zcode_host_acceptance.py `
  -m 'zcode_host_acceptance and not zcode_live_model' -q
```

Each of the two repetitions creates a new run ID, profile, Git workspace, Scope, database, listener and synthetic
verification value. The inference fixture supplies model responses only. The actual host loads the managed plugin,
emits Hooks and calls native MCP tools; PowerContext validates, processes and persists the results. HTTP observations
record actual Server request/response bodies in memory without headers. Persisted evidence contains selected identities
and assertions, rather than raw requests or model transcripts.

Controlled write sessions use `yolo` only in this disposable fixture environment. The model emits a fixed list of
synthetic PowerContext operations; this is not a recommended permission mode for an arbitrary real model.

## Live model

```powershell
uv run pytest tests/e2e/test_zcode_host_acceptance.py -m zcode_live_model `
  --zcode-model-config "$env:USERPROFILE\.zcode\v2\provider_config.json" `
  --zcode-generation-env 'C:\path\to\powercontext.env' -q
```

`--zcode-model-config` accepts the host's versioned `provider_config.json`, or a legacy CLI JSON with `provider` and
`model.main`. A versioned config must have a default model selection. An `account:` selection also requires the sibling
`credentials.json`; it is copied opaquely into the run profile, with the original process's credential cipher secret.
The host owns credential decoding. The original config and credentials are never updated by this suite.
Use `--zcode-host-model '<existing-provider>/<model>'` to choose another model in the disposable profile.
The selected model must also exist in the CLI `app-server` registry; a headless fallback does not prove that.
No key is accepted as a command-line argument.

The Server environment file must configure Generation independently. It is loaded with PowerContext's normal
environment-file loader, including provider variables such as `OPENAI_API_KEY`. Existing database, listener and Bearer
settings are replaced by this run's isolated settings; the existing deployment is not started or modified.

Live write sessions use an actual CLI `app-server` and a permission client that accepts only the named PowerContext
operations in the run's synthetic Scope. Other permissions are denied. Read-only prompts use `plan`.
Actual runtime model selection is recorded when the host reports it; a configured default is not proof of selection.

Live mode makes billable requests to both configured model services. A model response alone does not establish automatic
Memory extraction: the suite requires a generated entry with the captured Source reference and a fresh session's answer.

## Result and failure semantics

Each run writes `.powercontext/zcode-acceptance/<run-id>/summary.json` with schema
`powercontext.zcode.acceptance-run.v1`, host/model/Server metadata, scenario status and evidence references.
Missing prerequisites, rejected model requests, timeouts and failed assertions exit nonzero. Daily pytest runs deselect
these tests. Deselection and unsupported features are not acceptance success.

| Scenario | Evidence |
| --- | --- |
| P3-01 | Managed installation, upgrade preservation and actual CLI discovery |
| P3-02 | Missing/wrong Bearer rejection and successful native MCP reads |
| P3-03 | Hook Source, scheduler Memory with Source citation, fresh session recall |
| P3-04 | Explicit/session/workspace/default priority; separate workspace, profile and endpoint ownership |
| P3-05 | Memory revision conflict; exact nullable Handoff, Receipt and Outcome association |
| P3-06 | Unchanged inspection, stale approval rejection and separately authorized current approval |
| P3-07 | Delayed accepted response, concurrent CLI sessions, unavailable runtime directory |
| P3-08 | Actual startup/resume/compact; default and opt-in Stop behavior |
| P3-09 | Actual Server stop/restart; ordinary tasks; native MCP recovery and any required refresh/resume |
| P3-10 | Optional existing HTTPS endpoint; not selected by the local suite |

The delayed capture fault holds an actual Server response after the write completes; it does not replace Server behavior.
The storage fault places a file at the observation directory path, exercising inability to store state without changing
the user's ACLs. These are controlled fault injections, not claims about a production outage.

Inspect the failed scenario and its evidence first. A cursor reaching a Source position is not proof that the extractor
kept the fact. Model policy can discard temporary test facts; the automatic scenario uses an ongoing coding constraint.
No explicit `remember_memory` or manual `flush_memory` is allowed to substitute for that scenario.

Core success requires P3-01 through P3-09. Lifecycle subfeatures are reported separately: a successful `/compact` command
does not imply `SessionStart(compact)` was emitted. The optional HTTPS scenario does not block local core acceptance;
SSH forwarding must be described as forwarding, not direct remote-port HTTPS.

On Windows with Node 24.15.0 and CLI 0.16.9, the local core suite passed two independent controlled-model runs and
two independent live-model runs. Live runs used GLM-5.3 for the host and `openai-chat:glm-5.3-flash` for Generation.
Each repetition used a new profile, database and Scope. `/compact` completed without a compact Hook, clear was not
executed, and P3-10 remained `not_run`; these results do not establish those subfeatures or desktop behavior.

CLI 0.16.9 retained an invalid MCP session after a Server restart. A native MCP list refresh did not repair the
active session; closing and resuming that same persisted session in the same process restored its tools. Evidence
records `automatic_mcp_recovery=false` and `recovery_mode=session_close_resume`. This is a manual recovery path,
not an automatic reconnect guarantee.

### Optional existing HTTPS endpoint

Use a separate user-owned test profile and an existing test Scope. Configure the managed plugin with the endpoint's
`https://` URL, set `POWERCONTEXT_ZCODE_AUTHORIZATION` locally, and use the service's trusted CA through `SSL_CERT_FILE`
and `NODE_EXTRA_CA_CERTS` when it is not publicly trusted. Run `powercontext doctor zcode`, then ask a fresh CLI session
to call native `list_scopes` and `get_scope` for that exact Scope. Keep capture disabled for this read-only check.
Record actual native tool objects, certificate verification, Bearer rejection/success and the route: direct ingress
or SSH forwarding. This optional check is manual; local summaries leave P3-10 `not_run`. It never deploys a service,
changes SSH settings or disables TLS verification.

Processes and listeners created by a run are closed on exit. Private run directories are retained for diagnosis.
After reviewing a completed run, remove its private profiles, credentials, workspaces and databases while retaining evidence:

```powershell
uv run python -m tests.e2e.zcode_acceptance.cleanup --run-id '<32-character run ID>'
```

For a diagnosed failed run, add `--include-failed`. A custom output directory requires `--output <directory>`.
Cleanup checks the completed summary, exact run-ID child and resolved targets before deletion, and rejects root reparse
points. It never removes the output parent, summary, evidence or unknown files. Do not clean a running test.

## Official Windows desktop

Use [the manual acceptance steps](desktop.md). CLI execution does not establish desktop support. Record the desktop
version and actual MCP objects; an HTTP request, shell command or model summary cannot replace a native MCP result.

Desktop 3.14.3 with GLM-5.3 has separately passed native Scope reads, automatic capture/Generation/fresh-session recall,
Memory citation conflicts, nullable Handoff continuation and commit, exact-revision acknowledgement and Outcome
Receipt association, and candidate inspection/old-version rejection/separately authorized approval. Public Server
readback confirmed the persisted references and approved proposal content. Candidate creation and revision were
Server API fixtures; the desktop checks used native MCP for inspection and approval. Startup and default-disabled
Stop were observed. Desktop resume emitted its actual lifecycle Hook: both empty and ready results were recorded;
the ready fixture emitted context without an extra Source capture. Opt-in Stop returned `unknown` after a 778 ms
timeout and retained its receipt and pause. This establishes bounded handling, not successful flush completion.
Desktop `/compact` completed without a `SessionStart(compact)` Hook. Readonly runtime diagnostics reported the exact
bound Scope and retained unknown-flush pause without changing state. During a real Server outage, an ordinary task
continued; after restart, the same task's single native MCP call failed with `Session not found`. No retry or refresh
was substituted for that attempt. Creating a new task in the same desktop process restored native MCP and the correct
Scope binding. The tested UI did not offer closing/reopening the original task. This records a manual new-task recovery
path; it does not establish automatic reconnection or recovery of the original task.
