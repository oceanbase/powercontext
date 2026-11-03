# Hermes boundary conformance

The default adapter regressions use synthetic messages and a disposable real PowerContext
HTTP Server with SQLite. They observe capture bodies and metadata, flush requests,
opt-in evaluation traces, operational logs, exact Source readback after a Server restart,
and late HTTP work across Scope changes and shutdown:

```sh
uv run pytest tests/integrations/test_hermes_provider.py -q
uv run pytest tests/integration_conformance -q -rs
```

Without `POWERCONTEXT_TEST_HERMES_CHECKOUT`, the external-host test explicitly skips.
This means external Hermes acceptance has **not executed**. The default regression suite
does not need a Hermes installation or network access.

## Run the pinned real manager

Supply an existing Hermes Git checkout at commit
`4131fd95700685444c3522bf1ea07578c4003523`. The harness requires that exact HEAD and
no tracked modifications. It never downloads, installs into, or modifies that checkout.
Use Python 3.14 for this pinned Hermes revision; its older-Python metadata exists for
the updater bridge. The interpreter needs the dependencies used by Hermes' manager and
this repository's development dependencies (including pytest, httpx, uvicorn and the
PowerContext SQLite/Server dependencies). `POWERCONTEXT_TEST_HERMES_PYTHON` defaults
to the interpreter running pytest. Missing dependencies, wrong versions, invalid commands
and scenario failures are failures, with no mock fallback.

For these host-entry paths, an isolated environment can be prepared from the PC lock
file plus Hermes' pinned YAML and process dependencies. From the PowerContext repository in PowerShell:

```powershell
$env:UV_PROJECT_ENVIRONMENT = Join-Path (Get-Location) '.powercontext\hermes-conformance-env'
uv sync --locked --python 3.14
uv pip install --python "$env:UV_PROJECT_ENVIRONMENT\Scripts\python.exe" 'ruamel.yaml==0.18.16' 'ruamel.yaml.clib==0.2.15' 'psutil==7.2.2'
Remove-Item Env:UV_PROJECT_ENVIRONMENT
```

This dependency profile exercises the real manager and compression entry with the local stub models described below;
it is not a full Hermes CLI installation or a validation of Hermes' complete dependency
lock. Running `uv sync` again removes the additional packages, so reinstall them after
syncing this environment. Environment preparation is separate from the conformance harness.

For example, in PowerShell, after preparing those dependencies outside the harness:

```powershell
$env:POWERCONTEXT_TEST_HERMES_CHECKOUT = 'D:\checkouts\hermes-agent'
$env:POWERCONTEXT_TEST_HERMES_PYTHON = 'D:\venvs\hermes-conformance\Scripts\python.exe'
uv run pytest tests/integration_conformance/hermes -q -rs --hermes-conformance-output .powercontext/hermes-conformance.json
```

On POSIX systems:

```sh
POWERCONTEXT_TEST_HERMES_CHECKOUT=/opt/checkouts/hermes-agent \
POWERCONTEXT_TEST_HERMES_PYTHON=/opt/venvs/hermes-conformance/bin/python \
uv run pytest tests/integration_conformance/hermes -q -rs \
  --hermes-conformance-output .powercontext/hermes-conformance.json
```

The subprocess receives a temporary HOME, Hermes profile, configuration/cache directories
and working directory. It receives no inherited model credentials or PowerContext
connection overrides, and disables user site packages and bytecode writes. The current
repository's adapter is loaded directly; no plugin is copied into Hermes. The real
`MemoryManager.add_provider`, `initialize_all`, `sync_all`, `on_session_end`,
`on_pre_compress`, `flush_pending` and `shutdown_all` run without replacing their methods.
PowerContext runs over loopback HTTP with enforced Access, a synthetic bearer identity,
temporary SQLite databases and a deterministic empty extraction model stub. The actual
Hermes summary client targets a separate loopback Chat Completions stub, including streamed
responses. An invalid
bearer identity is rejected on the restarted Server's exact Source read path.

## Interpret the evidence

This is **real host code + stub models**, not live-model end-to-end acceptance. Hermes' summary
requests go only to the local HTTP stub. Messages are fixtures supplied to its manager; the test does not establish
that an interactive Hermes UI produces those message shapes.

The JSON distinguishes three evidence levels:

| Level | Executed path and claim |
| --- | --- |
| `adapter-only` | HTTP barriers verify Scope A's late capture stays in A, B's capture and recall use B, and shutdown's active failed write and dropped queued write are not persisted. The default provider tests exercise this same scenario. |
| `real-manager` | Real Hermes manager dispatch → current provider → actual HTTP Server → SQLite → new Server/runtime → authorized exact Scope/Source readback. Covers sync, session end, pre-compress, required capture failure/oversize propagation and durable capture surviving an HTTP extraction failure. |
| `real-compaction` | The real `AIAgent._compress_context` entry with `compression.checkpoint_required` enabled. A controlled HTTP capture rejection must preserve the live/SessionDB transcript and session identity, without a summary-model request. The same entry then accepts capture, calls the local summary-model stub, replaces the returned transcript and active SessionDB transcript, and reads back that replacement after reopening SessionDB. |

Hermes `pre_compaction` remains `unsupported` in the capability manifest. The compression
evidence covers checkpoint-failure blocking and successful replacement for one synthetic
manual, in-place entry with a custom model endpoint. It does not establish automatic/gateway
compression across all host configurations or live model behavior. External acceptance requires an actual successful
configured run; a default skip cannot satisfy that requirement.

The compression scenario constructs an actual Hermes AIAgent and SessionDB. It configures
checkpoint requirements through the temporary profile, passes the actual initialized
MemoryManager, and rejects capture at the local HTTP boundary before retrying with capture
accepted. Resumed inputs come from SessionDB with their durable row identities. Host
event/compression methods are not replaced. A subprocess-only audit hook refuses non-loopback
network traffic. The rejected attempt makes no summary request; the successful control runs
the actual summary client against the local HTTP stub and commits through Hermes' normal
SessionDB compaction code. JSON contains counts and boolean outcomes, not prompts or responses.

The default output is `.powercontext/hermes-conformance.json`, including on skips and failures.
It contains a schema version, PC SHA/dirty flag, expected/observed Hermes SHA and commit
version, OS, fixture ID/hash, entry point, model mode, scenario statuses, fixed failure
categories, request counts and boolean checks. Successful manager evidence includes its
Python version, accepted/read-back Source counts, restart
readback and checkpoint results. Source IDs, Scope IDs, paths, messages, credentials,
transcripts and subprocess logs are excluded. Source identities and sensitive evaluation
traces exist only in pytest's temporary local directory. Share the JSON, not that directory.

HTTP barriers use events and bounded deadlines. Scope switching uses a one-second configured
drain; shutdown uses the provider's actual five-second drain while the barrier remains closed.
There are no timing sleeps to decide whether a capture completed. Extraction failure is
injected at the HTTP boundary after actual Source acceptance; it is not a live provider failure.
