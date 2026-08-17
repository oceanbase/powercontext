# End-to-end workload harness

This directory contains PowerContext's end-to-end workload catalog. LoCoMo is used as a pinned input sample, not as a
benchmark suite. The Terminal-Bench case retains its native task and verifier, while PowerContext acceptance is based
on Memory collection, grounding, and recall rather than the native task reward.

The common architecture separates workload selection, execution, evidence, Memory evaluation, and reporting. Bub is
the current execution adapter because its model, tools, context injection, capture, and checkpoints are observable.
Another adapter can be added later without changing the common workload or evaluation contracts.

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
```

The dataset can be a local Harbor dataset path or a registry dataset name and version. `execution` selects the
adapter and its budget. `model` declares only whether the workload requires a model. The runtime selects the model,
provider, endpoint, and credentials. `evaluation` declares only externally observable Memory behavior.

Runtime configuration keeps the native ownership of each component. The harness Client reads
`POWERCONTEXT_CLIENT_*`, the Bub adapter forwards native `BUB_*` settings for model-backed workloads, and the
PowerContext integration reads `POWERCONTEXT_BUB_*`. Harness-owned settings are limited to workload selection,
evidence paths, database identity, repository mounting, and nested-container orchestration. Harbor does not require
model provider credentials.

The built-in manifests are:

| ID | Dataset | Categories | Purpose |
| --- | --- | --- | --- |
| `locomo-support-group` | local Harbor multi-step task | `acceptance`, `sample` | Pinned LoCoMo-derived sample |
| `project-database-decision` | local Harbor multi-step task | `acceptance`, `sample`, `smoke` | Durable project decision |
| `terminal-bench-db-wal-recovery` | `terminal-bench@2.0` | `long-horizon`, `terminal-bench` | Long-running capture and recall |

## Run acceptance workloads

Against an existing PowerContext Server, run the default `acceptance` category:

```bash
export POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:8000
export POWERCONTEXT_BUB_BASE_URL=http://host-gateway:8000
make harness-acceptance
```

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

`replay.json` is a self-contained Pydantic observation. Its workload's `execution.type` identifies the `bub` adapter,
and the remaining fields record the pre-execution Memory baseline and the instructions resolved by Harbor's ACP
runner.
`eval-report.json` uses
`powercontext.e2e-evaluation/v1`. `report.md` is rendered from the report model with Marko. Native Harbor and ACP
evidence remains under `harbor-jobs/`.

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

If the task container requires an outbound proxy, set `POWERCONTEXT_E2E_AGENT_PROXY_URL` to a URL reachable from that
container. The harness forwards it to both the agent and native verifier phases. In the fixed nested-container harness,
`host-gateway` addresses the harness container, so a proxy exposed there can be passed as
`http://host-gateway:<port>`. The typed setting is also treated as a secret when evidence is written.

Agent setup uses Bub's supported installation path: `uv tool install` installs Bub with the local PowerContext plugin,
then `bub install bub-acp-server` adds the ACP server to the same environment. Harbor uploads and runs its native ACP
client. The Terminal-Bench task keeps its original image, setup, verifier, and isolation boundary. The harness ignores
dataset CPU and memory limits because it evaluates Memory behavior rather than benchmark resource compliance. This
also keeps the fixed harness usable in nested container runtimes that cannot create additional cgroups.

Long-horizon acceptance requires observable Memory behavior:

- the manifest checksum matches Harbor's resolved task;
- native ACP evidence exists;
- completed Bub events were captured at the configured coverage;
- a checkpoint created Memory in an initially empty scope;
- new Memory cites sources captured during the run;
- recall probes return prepared context.

In-run context injections remain a reported metric, but they do not gate this single-session task because extraction
may complete only at the final checkpoint. Harbor rewards are diagnostic scores and do not gate Memory acceptance.

## Handoff and completed-task scenarios

The `scenarios` command runs a single uninterrupted baseline before the selected scenario legs. It keeps the workload,
model budget, native verifier, Codex OAuth mount, and proxy environment unchanged across legs.

```bash
uv run --frozen --project e2e/bub powercontext-e2e scenarios \
  --id terminal-bench-db-wal-recovery \
  --handoff-after-step 5 \
  --handoff-after-step 12 \
  --output .powercontext-e2e/bub/scenarios
```

The switch points are completed Bub loop boundaries. A session handoff starts a new Bub/ACP process in the same task
container and sends only `continue`. A container handoff runs the source segment without a verifier, collects a full
workspace archive through Harbor's agent-log channel, starts a fresh task container, restores the archive, and sends
only `continue`; the native verifier runs after the resumed segment. Source and resumed segments use the same isolated
PowerContext scope.

The completed-reuse scenario runs a cold task in an empty scope and a warm task in a fresh container using the
uninterrupted baseline's completed Memory scope. It accepts the warm leg only when native rewards do not degrade and
both Bub token usage and LLM-call steps decrease.

Each leg retains the normal `replay.json`, `eval-report.json`, native Harbor evidence, and report. The suite adds
`scenario.json` and `scenario-report.md`. Token totals are computed by summing every Bub `LlmCallResult.usage`; raw
per-call usage remains in `powercontext-capture.jsonl`. Input, output, cached-input, and reasoning tokens remain separate,
while ACP context-window usage is not counted as token spend.
Container-handoff outputs also retain the workspace archive and record its digest in scenario evidence. Treat that
archive as task data and protect it with the same policy as native terminal and ACP artifacts.

## Rescore evidence

Every workload uses the same offline command:

```bash
REPLAY=.powercontext-e2e/bub/sqlite/acceptance/terminal-bench-db-wal-recovery/replay.json \
make harness-rescore
```

The harness does not mirror PowerContext Server, PowerContext Client, Bub, Harbor, or any-llm settings. Each component
loads its native parameters, and the adapter only forwards the native values needed across the nested-container
boundary. The Bub plugin uses Bub's Pydantic settings extension and accepts the same fields in the `powercontext`
section of `bub.yml`. Native Bub API keys and the PowerContext Client token are redacted at every final evidence sink.
CI scans evidence with TruffleHog before publishing it. Native ACP artifacts can contain arbitrary command output and
should be reviewed before sharing.
