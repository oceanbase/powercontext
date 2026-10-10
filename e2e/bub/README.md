# End-to-end workload harness

This directory contains PowerContext's end-to-end workload catalog. LoCoMo is used as a pinned input sample, not as a
benchmark suite. The Terminal-Bench case retains its native task and verifier, while PowerContext acceptance is based
on Memory collection, grounding, and recall rather than the native task reward.

The common architecture separates workload selection, execution, evidence, Memory evaluation, and reporting. Bub is
the execution adapter for acceptance workloads because its model, tools, context injection, capture, and checkpoints
are observable. The OFF/ON comparison can also run on Codex, Claude Code, OpenCode, and Pi, without changing the
workload or evaluation contracts.

Every workload follows one execution path:

```text
Pydantic manifest and native runtime settings
  -> isolated PowerContext scope
  -> Harbor Job
  -> Harbor ACP runner
  -> Bub ACP server
  -> PowerContext
  -> Pydantic Memory evaluation
  -> Pydantic JSON evidence and Marko report
```

Harbor owns task and agent execution. Local multi-step Harbor tasks model independent capture and recall sessions;
registry-backed tasks such as Terminal-Bench use the same `Job.run` call. The harness does not contain a second Bub
runner. Pydantic validates configuration, manifests, observations, and evaluation reports. Marko renders the Markdown
summary.

## Layout

```text
e2e/bub/
  tasks/                  # PowerContext manifests and evaluation expectations
  paired-tasks/           # OFF/ON manifests for the paired command
  harbor-tasks/           # Local Harbor tasks used by built-in samples
  src/powercontext_e2e/   # One Harbor runner and one Memory evaluator
```

All manifests use the same schema:

```yaml
schema: powercontext.e2e-task/v1
id: project-database-decision
categories:
  - acceptance
  - sample
dataset:
  path: e2e/bub/harbor-tasks
  task_id: project-database-decision
  checksum: <harbor-task-checksum>
execution:
  type: bub
  model: false
  max_steps: 10
  max_tokens: 4096
evaluation:
  expected_memory:
    - OceanBase
  probes:
    - id: database-decision
      query: What database did this project select, and why?
      expected_context:
        - OceanBase
      forbidden_context:
        - SQLite
```

The dataset can be a local Harbor dataset path or a registry dataset name and version. `execution` selects the
adapter and its budget. `model` declares only whether the workload requires a model. The runtime selects the model,
provider, endpoint, and credentials. `evaluation` declares only externally observable Memory behavior.
Recall probes with `expected_context` contribute to `probe_coverage` and require every expected fragment. Probes
with only `forbidden_context` express abstention and do not contribute to that coverage. If there are no positive
probes, `probe_coverage` is `1`. Every probe rejects prepared context containing a forbidden fragment. Fragment
matching is case-insensitive and Unicode-normalized. Any forbidden match fails acceptance independently of the
`probe_coverage` threshold.
Two or more compatible selected tasks with the same `batch:<name>` category share one run-local Harbor task and
container. Their scopes, evidence, and evaluation remain independent; selecting one task uses the normal path.

Runtime configuration keeps the native ownership of each component. The harness Client reads
`POWERCONTEXT_CLIENT_*`, the Bub adapter forwards native `BUB_*` settings for model-backed workloads, and the
PowerContext integration reads `POWERCONTEXT_BUB_*`. Harness-owned settings are limited to workload selection,
evidence paths, database identity, repository mounting, and nested-container orchestration. Harbor does not require
model provider credentials.

The built-in manifests are:

| ID | Dataset | Categories | Purpose |
| --- | --- | --- | --- |
| `locomo-*` (four manifests) | one local Harbor task each | `acceptance`, `sample`, `batch:locomo` | Pinned LoCoMo-derived cases |
| `project-database-decision` | local Harbor multi-step task | `acceptance`, `sample`, `smoke`, `batch:acceptance` | Durable project decision |
| `terminal-bench-db-wal-recovery` | `terminal-bench@2.0` | `long-horizon`, `terminal-bench` | Long-running capture and recall |
| `failure-policy-*` | local Harbor tasks | `acceptance`, `fixture`, `batch:acceptance` | Collect-all and fail-fast behavior |

## Run acceptance workloads

Against an existing PowerContext Server, run the default `acceptance` category:

```bash
export POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:8000
export POWERCONTEXT_BUB_BASE_URL=http://host-gateway:8000
# host-gateway is plaintext non-loopback; the harness network is private, so vouch for it explicitly.
export POWERCONTEXT_BUB_TRUST_TRANSPORT_SECURITY=true
make harness-acceptance
```

The default run executes all acceptance tasks with collect-all, then reruns `batch:acceptance` with fail-fast. LoCoMo
stays in its own `batch:locomo`; the database decision and failure-policy tasks share `batch:acceptance`. Explicit
selection runs only the selected workloads with the requested failure policy.

Selection uses the `acceptance` command's repeatable `--id` and `--category` options:

```bash
make harness-acceptance ARGS='--id locomo-support-group --id project-database-decision'

make harness-acceptance ARGS='--category acceptance --category sample'
```

ID and category selection are additive. The same selectors work in the fixed Compose harness:

```bash
make harness-compose-acceptance

POWERCONTEXT_E2E_DATABASE=oceanbase \
make harness-compose-acceptance \
ARGS='--id locomo-support-group --id project-database-decision'
```

Each selected workload writes the same layout:

```text
<output>/<workload-id>/
  replay.json
  eval-report.json
  report.md
  harbor-jobs/
```

Shared runs write the same v1 files per source task under `batch-<name>/tasks/<workload-id>/`, plus one aggregate
evaluation and report at `batch-<name>/`. `collect-all` reports every failed task; `fail-fast` stops only that shared
Harbor trial at its first failed step. Runtime batch steps are flat and task-prefixed. Each agent invocation starts an
independent ACP session and Bub tape. Bub keeps every tape as a file in its home, which Harbor does not clear between
steps, so before each invocation the harness removes the earlier tapes: Bub does not search another session's tape, but
an agent could read the files.

## Compare PowerContext off and on

A continuation workload is a Harbor multi-step task written in plain language, so any agent host can run it. An
earlier session mentions a fact only in the conversation, next to an unrelated small job. The final recall session
asks for that fact and has the agent write its answer to a file as structured values, so the grader checks what the
answer asserts rather than keywords that a contradictory or hedged answer could also contain. The recall step's own
tests grade the answer, and the answer key lives only there; the harness also empties `/tests` before each session,
so the recall session cannot read the capture step's tests either. The recall step's own reward decides the run
whatever the task's multi-step reward
strategy; earlier steps' rewards are recorded for diagnosis only. A task may not set `min_reward` on an earlier
step, because Harbor would then skip the recall step when that step's unrelated job falls short.

`e2e/bub/paired-tasks/` holds three such workloads, each with a different answer shape:
`project-decision-continuation` asks for a name and a count, `api-contract-continuation` for a URL path and a header
name, and `revised-decision-continuation` for a value that was revised before the session together with the value it
replaced, both stated in one message, so a run that keeps only the first value scores 0. All three run by default, so
a default run takes about three times as long as one workload; `--id` selects one. The three share one capture-session
shape, a fact stated in the first user message next to a one-word edit, and differ in what is asked back, so three
passes show three answer shapes recalled, not three ways of capturing. Harbor keeps the container between the two
sessions, so each capture step's verifier, after recording its diagnostic reward, resets the workspace to the
corrected README: notes the capture agent wrote there cannot stand in for memory in the recall session. The reset
covers the workspace only; a file the agent writes elsewhere in the container is not reset.

The `paired` command runs each selected workload with PowerContext off and on, in separate containers, and repeats
this for `--trials` trials. The arm that runs first alternates between trials. `--host` selects the agent host for
both arms: `bub` by default, `codex`, `claude-code`, `opencode`, or `pi`. Each host uses its own PowerContext
integration, so ON means what that integration does for its users.

- OFF is the host as a user without PowerContext has it. The agent is not told that PowerContext is off, and it
  cannot find PowerContext: no integration is installed, no PowerContext sources are mounted in its container, and it
  gets no `POWERCONTEXT_*` settings or Server credential. Continuation tasks ask about earlier sessions, and agents
  that find PowerContext files go looking for it. For Codex this differs from the published SWE-bench Pro protocol,
  whose OFF arm has the plugin installed and runs with `--disable plugins`.
- ON installs the integration, binds it to a new Scope, and gives it the harness Client's Server token. For Bub this
  means the plugin with `capture_events` enabled, so that, like the other host integrations, it captures what the
  user says without relying on the model to call a memory tool. This is not the plugin's default setting. Codex runs
  with `--enable plugins`, Claude Code has the plugin enabled, OpenCode loads it from its plugins directory, and Pi
  loads the installed package. In all four, the plugin captures each user prompt and asks for context before each
  turn, and the plugin's tools (MCP for Codex and Claude Code, native tools for OpenCode and Pi) and Skill are
  available to the model.
- Everything else is the same in both arms: image, host version, model, reasoning settings, and budget.

Both arms' containers can reach the Server, and the Server keeps the ON arms' Memory across trials. The command
therefore runs only against a Server that requires authentication: it stops before the first run if the Server lists
its Scopes to a client without a token. Start the Server with `POWERCONTEXT_SERVER_ACCESS_MODE=enforced` and a
`POWERCONTEXT_SERVER_AUTH_TOKEN`, and give the harness the same value as `POWERCONTEXT_CLIENT_API_TOKEN`; the harness
passes it to the ON arm's integration as `POWERCONTEXT_BUB_API_TOKEN` or `POWERCONTEXT_<HOST>_AUTHORIZATION`. The
harness holds that value in its own environment and gives Harbor a reference to it, so Harbor's job files record the
reference and no part of the token. The token still lets an ON agent read other Scopes on the same Server, including
earlier trials'.

After each ON session the harness records the Scope's Server statistics. When another session follows, it first flushes
the Scope, standing in for the time that passes between real sessions, and repeats the flush until the Scope has
processed every captured Source, a flush makes no progress, or 20 rounds pass. This runs from a Harbor agent-end hook
after the agent's timed phase, so it does not use the agent's time budget. A failed flush or statistics read is recorded
as a treatment failure rather than replacing the agent's own outcome, so a timed-out session still counts as a timeout.
The one exception is HTTP 503 `artifact_owner_pending`: a host plugin's own end-of-session flush can still be running on
the Server after the plugin stops waiting for it, and until that request records who owns the Memory it created, the
Server answers a flush of the Scope, and reads that list its Memory, with this code. The harness retries such a call
after 1, 2, 4, and 8 seconds before it records the failure. Host plugins flush on different schedules, so the harness
flushes the same way for every host. The Server's generation model therefore takes part in the ON arm; the run fails
early when the Server does not report `memory_extraction`.

An ON run counts only when Server statistics for its Scope show that Sources were captured before the scored session
(during it, for a single-session workload) and that the integration asked PowerContext for context during it.
Otherwise it is an integration failure. Whether a flush creates Memory and whether recall returns content are
PowerContext's own behavior, so the snapshots record them but a run that gets nothing useful still counts as an ON
attempt. Integration failures and harness or infrastructure errors are reported but left out of success rates and
paired differences. A session whose model request failed is such an error on every host: Codex and Claude Code exit
non-zero, Harbor reads OpenCode's error events, and the harness reads Pi's last message, because Pi exits 0 in the
JSON mode Harbor uses. The harness reads Pi's output through the logs that Harbor's Docker environment mounts and
stops with an error when the file is not there. An agent timeout counts as a failed attempt in either arm.

The harness Client waits for each flush, which runs the Server's generation model, so raise its 10-second default
timeout; the Bub plugin also flushes during a session.

```bash
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN=replace-me
powercontext server run  # in another shell, with the Server's inference settings
```

```bash
export POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:8000
export POWERCONTEXT_CLIENT_API_TOKEN=replace-me
export POWERCONTEXT_CLIENT_TIMEOUT=150
export POWERCONTEXT_BUB_BASE_URL=http://host-gateway:8000
export POWERCONTEXT_BUB_TIMEOUT=150
export POWERCONTEXT_BUB_TRUST_TRANSPORT_SECURITY=true
export BUB_MODEL=openrouter:openai/gpt-5.4
export BUB_API_KEY="$OPENROUTER_API_KEY"
make harness-paired ARGS='--trials 2'
```

Codex 0.153.4 runs through Harbor's Codex agent. The plugin reads its Server URL only from its installed
`.mcp.json`, so the harness writes `POWERCONTEXT_CODEX_SERVER_URL` there before each ON session, as
`powercontext setup codex --server-url` does; other `POWERCONTEXT_CODEX_*` settings reach the ON arm unchanged. The
harness selects the model with `POWERCONTEXT_E2E_CODEX_MODEL` and the reasoning effort with
`POWERCONTEXT_E2E_CODEX_REASONING_EFFORT`, which defaults to `medium`. Harbor authenticates Codex with
`OPENAI_API_KEY`, the auth document named by `CODEX_AUTH_JSON_PATH`, or `~/.codex/auth.json` when
`CODEX_FORCE_AUTH_JSON=1`. A ChatGPT login can use the models that Codex 0.153.4 offers to ChatGPT accounts.

```bash
export POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:8000
export POWERCONTEXT_CLIENT_API_TOKEN=replace-me
export POWERCONTEXT_CLIENT_TIMEOUT=150
export POWERCONTEXT_CODEX_SERVER_URL=http://host-gateway:8000
export POWERCONTEXT_CODEX_ALLOW_INSECURE_HTTP=true
export POWERCONTEXT_E2E_CODEX_MODEL=gpt-5.6-sol
export CODEX_FORCE_AUTH_JSON=1
make harness-paired ARGS='--host codex --trials 2'
```

Claude Code 2.1.284 runs through Harbor's Claude Code agent. The ON container sees only the plugin's marketplace
manifest, `.claude-plugin/marketplace.json`, and `integrations/claude-code`. Before each ON session the harness installs
the plugin with its `server_url` option set to `POWERCONTEXT_CLAUDE_SERVER_URL`, because the plugin's MCP connection
reads only that option. The plugin's hook reads `POWERCONTEXT_CLAUDE_SERVER_URL` itself, and other
`POWERCONTEXT_CLAUDE_*` settings reach the ON arm unchanged. Without `POWERCONTEXT_CLAUDE_ALLOW_INSECURE_HTTP=true`,
the hook skips a plain-HTTP Server such as `host-gateway`, and the ON arm reports an integration failure. The harness
selects the model with `POWERCONTEXT_E2E_CLAUDE_CODE_MODEL` and the effort with
`POWERCONTEXT_E2E_CLAUDE_CODE_REASONING_EFFORT`, which defaults to `medium`. Harbor authenticates Claude Code with
`CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`), `ANTHROPIC_API_KEY`, or `ANTHROPIC_AUTH_TOKEN`. When an API key
and the OAuth token are both set, it uses the API key unless `CLAUDE_FORCE_OAUTH=1`. Harbor also forwards
`ANTHROPIC_BASE_URL` from the harness environment, so unset it unless the agent should use that endpoint.

```bash
export POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:8000
export POWERCONTEXT_CLIENT_API_TOKEN=replace-me
export POWERCONTEXT_CLIENT_TIMEOUT=150
export POWERCONTEXT_CLAUDE_SERVER_URL=http://host-gateway:8000
export POWERCONTEXT_CLAUDE_ALLOW_INSECURE_HTTP=true
export POWERCONTEXT_E2E_CLAUDE_CODE_MODEL=claude-sonnet-5-5
export CLAUDE_CODE_OAUTH_TOKEN=replace-me
export CLAUDE_FORCE_OAUTH=1
make harness-paired ARGS='--host claude-code --trials 2'
```

OpenCode 1.18.33 runs through Harbor's OpenCode agent. The ON arm copies the plugin's bundled `lib/index.js` and its
Skill to where `powercontext setup opencode` puts them. Setup also registers a TUI plugin, which `opencode run` does not
load. The agent container sees only the plugin's `lib` and `skills` directories. The plugin reads
`POWERCONTEXT_OPENCODE_SERVER_URL`, the Scope, and the plain-HTTP consent `POWERCONTEXT_OPENCODE_ALLOW_INSECURE_HTTP`
from its environment; without the consent it stays inactive and the ON arm reports an integration failure. The plugin
prefers `POWERCONTEXT_OPENCODE_BASE_URL` when that is also set, so leave it unset. OpenCode keeps its sessions,
oversized tool results, plans, snapshots, and logs in its data directory, which Harbor does not clear between steps.
Before each session in both arms the harness therefore empties that directory except for stored credentials, and removes
OpenCode's temporary directory, as sessions start empty on the other hosts. It stops the arm if OpenCode still lists a
session afterwards. The harness selects the model with `POWERCONTEXT_E2E_OPENCODE_MODEL` in OpenCode's `provider/model`
form and passes `POWERCONTEXT_E2E_OPENCODE_REASONING_EFFORT`, default `medium`, as the model variant. OpenCode silently
ignores a variant that the model does not define, so choose an effort the model offers; the report records the requested
value. Harbor passes the key of the providers it knows, such as `OPENROUTER_API_KEY`; a model from another provider gets
no key.

```bash
export POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:8000
export POWERCONTEXT_CLIENT_API_TOKEN=replace-me
export POWERCONTEXT_CLIENT_TIMEOUT=150
export POWERCONTEXT_OPENCODE_SERVER_URL=http://host-gateway:8000
export POWERCONTEXT_OPENCODE_ALLOW_INSECURE_HTTP=true
export POWERCONTEXT_E2E_OPENCODE_MODEL=openrouter/z-ai/glm-5.3
export OPENROUTER_API_KEY=replace-me
make harness-paired ARGS='--host opencode --trials 2'
```

Pi 0.82.1, the version the PowerContext Pi package tests against, runs through Harbor's Pi agent. Harbor installs Pi
from its former npm name, which ends before that version, so the harness installs `@earendil-works/pi-coding-agent`
with the same steps. The ON arm then runs `pi install` on the package, as `powercontext setup pi` does; the agent
container sees only its `package.json`, `extensions`, `src`, and `skills`. The package reads
`POWERCONTEXT_PI_SERVER_URL`, the Scope, and the plain-HTTP consent `POWERCONTEXT_PI_ALLOW_INSECURE_HTTP` from its
environment. Pi runs every session with `--no-session`, so it saves no session. Its bash tool keeps the full output of
a command over 2,000 lines or 50 KB as `pi-bash-*.log` in the temporary directory, so before each session in both arms
the harness removes those files. The harness selects the model with `POWERCONTEXT_E2E_PI_MODEL` in Pi's
`provider/model` form and passes `POWERCONTEXT_E2E_PI_REASONING_EFFORT`, default `medium`, as `--thinking`. Harbor
passes the provider's key, such as `OPENROUTER_API_KEY`.

```bash
export POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:8000
export POWERCONTEXT_CLIENT_API_TOKEN=replace-me
export POWERCONTEXT_CLIENT_TIMEOUT=150
export POWERCONTEXT_PI_SERVER_URL=http://host-gateway:8000
export POWERCONTEXT_PI_ALLOW_INSECURE_HTTP=true
export POWERCONTEXT_E2E_PI_MODEL=openrouter/z-ai/glm-5.3
export OPENROUTER_API_KEY=replace-me
make harness-paired ARGS='--host pi --trials 2'
```

Each arm writes `observation.json`, which includes the per-session Server snapshots for ON, and its Harbor jobs:

```text
<output>/
  paired-report.json
  report.md
  <workload-id>/trial-<n>/<off|on>/
    observation.json
    harbor-jobs/
```

The report states the host, its version, the model, and the reasoning settings. The command exits non-zero when any
arm could not be scored; a task that fails in either arm is a result, not a command failure.

For each workload and over all of them, the report gives:

- Each arm's passed and scored runs, its success rate with a 95% Wilson score interval, and the runs left out as
  errors or integration failures.
- The mean ON minus OFF score over the trials in which both arms were scored, with a 95% percentile bootstrap
  interval over those pairs (10,000 resamples from a fixed seed, so the same evidence gives the same interval), and
  how many pairs only ON passed, only OFF passed, or tied.
- Per step and arm, over the same scored runs: the agent's execution time as Harbor measured it, and the input
  tokens, the cached input tokens among them, output tokens, and cost that Harbor's agent for that host records.
  Claude Code, OpenCode, and Pi report their own cost; for Codex, Harbor estimates it from its price table and leaves
  it out for a model the table lacks. A timed-out run is scored, so its time counts. Bub reports no usage at all,
  because Harbor reads it from the ACP prompt response and `bub-acp-server` leaves it out (bubbuild/bub-contrib#78),
  so those cells show `n/a`.
- The Server's usage for the ON arm, as a mean over each scored run's final Scope snapshot: generation and embedding
  requests and tokens, and the Server's own estimate of the tokens of context it returned. The Server has no price
  list, so there is no Server cost. OFF runs have no Scope. A timed-out ON run keeps the snapshots it reached, so
  its usage is that of the sessions that ran; the report states how many runs the mean covers.

Each step figure and Server token count is a mean over the runs that reported it, so the harness never counts a
missing figure as zero. Runs can differ: Harbor reads a step's usage from the host's own output, so a step whose host
recorded none has no figures, and the Server leaves a Scope's tokens unknown when a model provider did not report
them. A timed-out step keeps the usage its host recorded before it was stopped, as it keeps its time. Harbor's agents
for Pi, Claude Code, Codex, and OpenCode do record 0 for a token count missing from a log the host wrote, and the
report cannot tell that from a real 0. The report shows `n/a` when no run reported a figure and adds its count, as in
`1,000 (1 of 2 runs)`, when only some did; `paired-report.json` keeps the count of every figure.

The report is marked preliminary. With two trials the intervals are wide, which is the point: they show how little
such a pilot can say. The command does not yet check the Default Scope for leaks or run in the fixed Compose harness.

### MemoryCode workloads

`e2e/bub/paired-tasks/memorycode/` holds continuation workloads built from MemoryCode (Rakotonirina et al., "From Tools
to Teammates: Evaluating LLMs in Multi-Session Coding Interactions", ACL 2025; Apache-2.0), a published dataset rather
than tasks written for this harness. In each dialogue a mentor gives a mentee coding guidelines across mentoring
sessions, such as a prefix for argument names or a decorator on every function, updates some of them, and talks about
unrelated topics. `e2e/bub/scripts/memorycode_tasks.py` turns a dialogue into one task: an agent session per mentoring
session, which gives the agent that session's transcript and asks only for an acknowledgement, then a recall session
that asks for code for the dialogue's history eval queries, one file each, following the mentor's latest guidelines
without restating them. The paper gives the model the whole history in one prompt; here the recall session sees no
transcript, so what it knows of the guidelines comes from the host's memory. Each mentoring session's verifier empties
the workspace, so notes the agent writes there cannot stand in for that memory.

The recall step grades the files with MemoryCode's own object extraction and checks, ported to
`e2e/bub/scripts/memorycode_grade.py` with upstream's quirks: a guideline about objects that a file does not define is
not scored for that file, and a name rule checks the first name of each object. Upstream grades the first fenced block
of a chat answer; a file that parses is graded whole, so a fenced example in a docstring does not replace it. An answer
without code scores 0 on every guideline, which here means a missing or empty file. The trial reward is 1 only when at
least one guideline applies and every output follows every guideline that applies to it. The verifier's reward file also
records the counts of applicable and passed checks and, when any check applies, MemoryCode's macro score over outputs;
each arm's observation keeps them under `harbor.rewards`. The task image does not install `pedantic`, the module the
decorator guidelines name, so an agent that runs its code sees an import error; this is the same in both arms.

The manifests pin 50 short dialogues, 10 for each count of one to five mentoring sessions, drawn with seed 1705. The
dataset is not copied into this repository. Generate the tasks from a MemoryCode checkout at the pinned revision: the
script writes them to `e2e/bub/memorycode-tasks/`, which git ignores, and fails when a task's checksum differs from its
manifest. Every task holds a copy of the grader, so after changing the grader or the task format, `--pin` writes the new
checksums for the same dialogues; `--sample N` draws another sample, with `--seed`, and replaces the manifests.

```bash
git clone https://github.com/Cohere-Labs-Community/MemoryCode /path/to/MemoryCode
git -C /path/to/MemoryCode checkout 1ab87e119b2f9a498de8075219e1c07f6041b394
uv run --project e2e/bub python e2e/bub/scripts/memorycode_tasks.py /path/to/MemoryCode
make harness-paired ARGS='--host pi --manifest e2e/bub/paired-tasks/memorycode --trials 1'
```

`--category memorycode-sessions-3` selects the dialogues with three mentoring sessions. Each session's transcript is 175
to 971 words in this sample, and the Server's generation model extracts Memory from it while the harness settles the
Scope between sessions. With `deepseek/deepseek-v4-pro` through OpenRouter the default 30-second
`POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS` often expired and the run became an integration failure; 120
seconds was enough. A dialogue with five mentoring sessions runs six agent sessions per arm, and its ON arm also waits
for each extraction, so plan a few minutes per dialogue.

### Task-completion workloads

The same command compares the arms on a task that one agent session completes and the task's own verifier grades, such
as a SWE-bench Pro instance. Its manifest declares `evaluation: {comparison: task-outcome}` and may name a Harbor
registry dataset, which Harbor downloads; the manifest pins the task's checksum, and a run whose Harbor task differs
from it is an error that also ends that workload's remaining trials. `e2e/bub/paired-tasks/swebench-pro/` holds one
manifest per repository of Harbor's `swebenchpro@1.0` (the 731 SWE-bench Pro public tasks, graded by the benchmark's
own `run_script.sh` and `parser.py` in its own images), chosen as the first task of each repository by name;
`e2e/bub/scripts/swebench_pro_manifests.py` writes manifests for another selection from a downloaded copy of the
dataset. The default manifest directory holds only the continuation workloads, so name this one with `--manifest`:

```bash
make harness-paired ARGS='--host pi --manifest e2e/bub/paired-tasks/swebench-pro --trials 1'
make harness-paired ARGS='--host pi --manifest e2e/bub/paired-tasks/swebench-pro --id swebench-pro-flipt-02e21636 --trials 3'
```

Each arm runs the task once in a fresh container, scored by the trial's reward. The ON arm binds a new Scope, so what
PowerContext adds in a single session is what the integration captures and recalls within it; an ON run counts only
when the Server shows Sources captured and a context request during that session. The report's step table shows that
session as the step `task`, with the time and usage figures Harbor records for a single-step trial. The hosts, the
OFF arm, and the evidence are as for continuation workloads. Each SWE-bench Pro task gives the agent 3,000 seconds and
declares 4 GB of memory, which the harness does not enforce, and its image is one to several GB (the ansible image is
1.6 GB), so plan disk space and time per run accordingly. The images keep a pip configuration that names the index
their build used, at a loopback address that no longer answers (confirmed in the ansible image), so Bub's runtime
install sets `PIP_INDEX_URL` to PyPI, or to `POWERCONTEXT_E2E_PIP_INDEX_URL` for a mirror the containers can reach;
the host's own `PIP_INDEX_URL` is not forwarded, and uv and apt keep their defaults. The manifests' `max_steps` and
`max_tokens` budgets apply to Bub only; the other hosts run with their own defaults. This OFF arm differs from the
published SWE-bench Pro run, whose OFF arm had the Codex plugin installed but disabled: here OFF is the host as a user
without PowerContext has it, the same on every host, so that the arms differ in nothing but the integration. The
benchmark's images keep the repository's git history, including the commit that holds the gold tests, in both arms
alike; the harness does not change the benchmark's own exposure.

Before each session on every host, the harness also empties `/tests`, where Harbor uploads each step's tests for its
verifier and leaves them, so that a later session cannot read an earlier step's verifier. It uses Harbor's own
directory reset, which also replaces a symlink or file at that path with an empty directory. Harbor uploads a step's
tests again before running that step's verifier, so nothing a verifier needs is lost; the harness assumes the image
itself ships nothing there.

## Long-horizon task

The Terminal-Bench manifest pins its task checksum, model requirement, step budget, capture cadence, recall probes,
and acceptance thresholds. Bub and ACP server versions belong to the adapter runtime and are recorded in replay
evidence. Harbor task and agent timeouts remain Harbor-owned; `BUB_MODEL_TIMEOUT_SECONDS` remains a native runtime
setting. Run the task with the same command:

```bash
make harness-compose-acceptance ARGS='--category long-horizon'
```

This task requires privileged Linux containers, enough time and disk for the task image, a runtime-provided Bub
model and credentials, and configured PowerContext generation and embedding inference. For example:

```bash
export OPENROUTER_API_KEY=replace-me
export BUB_MODEL=openrouter:openai/gpt-5.4
export BUB_API_KEY="$OPENROUTER_API_KEY"
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openrouter:deepseek/deepseek-v4-pro
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS=120
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=openrouter:qwen/qwen3-embedding-4b
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=openrouter-qwen3-embedding-4b-2560-unit
export POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=2560
make harness-compose-acceptance ARGS='--category long-horizon'
```

The runtime may authenticate Bub with its native `BUB_API_KEY`, provider-specific `BUB_<PROVIDER>_API_KEY`, or a
Codex OAuth document at
`${CODEX_HOME:-$HOME/.codex}/auth.json`. Authentication choice is not part of the workload manifest.
The fixed Compose harness exposes `BUB_MODEL`, `BUB_API_KEY`, and `BUB_API_BASE`; direct harness execution also
forwards other native `BUB_*` values without translating them.

If the agent task container requires an outbound proxy, set `POWERCONTEXT_E2E_AGENT_PROXY_URL` to a URL reachable
from that container. In the fixed nested-container harness, `host-gateway` addresses the harness container, so a
proxy exposed there can be passed as `http://host-gateway:<port>`. The URL can carry credentials, so the harness
treats it as a secret when evidence is written and gives Harbor a reference to it rather than the value.

Bub's runtime install, in every run, sets pip's index to PyPI, or to `POWERCONTEXT_E2E_PIP_INDEX_URL` when that names
a mirror the containers can reach; the host's own `PIP_INDEX_URL` is not forwarded. The value is passed to one
install command in the container and is not written to evidence, but it does appear in that command's environment,
so prefer a mirror that needs no credentials in its URL.

The agent container sees only the repository files that installation needs: the `powercontext` package and the host
integration, and none of them in a paired OFF arm. Workload files, answer keys, and benchmark data stay on the host,
because the agent can search its container. Agent setup uses Bub's supported installation path: `uv tool install`
installs Bub with the local PowerContext plugin when enabled, then `bub install` adds the ACP server to the same
environment. Both steps pin Bub to the harness version so installing ACP cannot upgrade the host. Harbor uploads and
runs its native ACP client. The Terminal-Bench task keeps its original image, setup, verifier, and isolation boundary.
The harness ignores dataset CPU and memory limits because it evaluates Memory behavior rather than benchmark resource
compliance. This also keeps the fixed harness usable in nested container runtimes that cannot create additional
cgroups.

Long-horizon acceptance requires observable Memory behavior:

- the manifest checksum matches Harbor's resolved task;
- native ACP evidence exists;
- completed Bub events were captured at the configured coverage;
- a checkpoint created Memory in an initially empty scope;
- new Memory cites sources captured during the run;
- recall probes return prepared context that satisfies their required and forbidden fragment contracts.

In-run context injections remain a reported metric, but they do not gate this single-session task because extraction
may complete only at the final checkpoint. Harbor rewards are diagnostic scores and do not gate Memory acceptance.

## Rescore evidence

Every workload uses the same offline command:

```bash
REPLAY=.powercontext-e2e/bub/sqlite/acceptance/terminal-bench-db-wal-recovery/replay.json \
make harness-rescore
```

For negative recall contracts, replay evidence stores the pre-redaction match verdict rather than the matched text.
Offline rescoring therefore preserves the live outcome without exposing a configured secret through the replay.

The harness does not mirror PowerContext Server, PowerContext Client, Bub, Harbor, or any-llm settings. Each component
loads its native parameters, and the adapter only forwards the native values needed across the nested-container
boundary. The Bub plugin uses Bub's Pydantic settings extension and accepts the same fields in the `powercontext`
section of `bub.yml`. Every `*_API_KEY`, `*_TOKEN`, `*_AUTHORIZATION`, and `*_SECRET_ACCESS_KEY` value in the harness
environment, including Bub's API keys and the PowerContext Client token, is redacted in every file the harness writes,
whatever its length. A name with `_TOKEN_` in the middle, such as `AWS_BEARER_TOKEN_BEDROCK`, counts too, and names
match in any case, but a name ending in `_FILE`, `_PATH`, or `_URL`, such as `AWS_WEB_IDENTITY_TOKEN_FILE`, says
where a token is and is left alone. Only common placeholders for local model servers, such as `1` or `ollama`, are
left in place, because they protect nothing and redacting them by substring would rewrite the evidence. CI scans
evidence with TruffleHog before publishing it. Harbor writes the files under `harbor-jobs/` itself, and the harness
does not redact them: the job configuration holds references to secrets rather than their values, but every host's
own output there can contain arbitrary command output, such as an agent printing its environment, and should be
reviewed before sharing.
