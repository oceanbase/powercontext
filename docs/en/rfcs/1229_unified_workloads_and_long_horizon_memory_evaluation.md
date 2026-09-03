- Proposal Name: `unified_workloads_and_long_horizon_memory_evaluation`
- Start Date: 2026-08-13
- RFC PR: [oceanbase/powercontext#1229](https://github.com/oceanbase/powercontext/pull/1229)
- Related RFC: [RFC 0081: End-to-End Evaluation Architecture](0081_end_to_end_evaluation_architecture.md)

# Summary

PowerContext represents built-in end-to-end samples and long-horizon tasks as workloads. Each workload selects a
pinned task, chooses an execution adapter, sets a budget, and declares how to evaluate the Memory produced during the
run.

Workloads share one catalog, replay envelope, Memory evaluator, report format, and `acceptance` command. The current
implementation uses Bub as its execution adapter because Bub exposes model calls, tools, context
injection, capture, and checkpoints. The architecture can accept another adapter later without changing those common
contracts.

A source-native task reward remains diagnostic. It does not decide whether PowerContext collected grounded,
recallable Memory.

# Motivation

RFC 0081 defines the broader end-to-end evaluation architecture, but it leaves local deterministic samples,
model-backed agent runs, and long-horizon tasks on separate command and artifact paths. That separation duplicates
selection, execution setup, evidence handling, and reporting.

A unified workload contract provides stable answers to these questions:

- Which pinned task and revision ran?
- Which execution adapter and runtime configuration drove it?
- Did execution start from an isolated, empty PowerContext scope?
- What evidence was captured during execution?
- Did the run create grounded Memory and recall it afterward?
- Can the same evidence be rescored without rerunning the task?

Bub is a useful initial adapter because both deterministic tool flows and model-backed agent flows can traverse its
real ACP, command, tool, hook, and plugin boundaries. Deterministic execution does not need to pretend to be a model
run, while long-horizon execution can expose the model loop and capture policy.

## Scope

This RFC covers:

- one Pydantic workload manifest and catalog;
- selection by workload ID or category;
- an isolated PowerContext scope for every workload;
- Harbor-backed Bub execution for repository and registry tasks;
- normalized replay evidence, Memory evaluation, and report rendering;
- deterministic acceptance across SQLite and OceanBase;
- opt-in model-backed and long-horizon workloads; and
- offline rescoring from committed evidence contracts.

The complete LoCoMo benchmark and the separate SWE-Pro evaluation remain outside this catalog. This RFC does not
migrate them or change their native inputs, scoring, results, or operational commands.

# Guide-level explanation

## Workload manifest

The workload manifest is both a catalog entry and an execution contract. Pydantic validates the manifest and runtime
settings before execution.

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
      query: Which project decision selected multi-node persistent storage?
      expected_context:
        - OceanBase
      forbidden_context:
        - SQLite
```

`dataset` may identify a repository-maintained Harbor task or a versioned registry task. Repository tasks and
registry tasks use the same execution and evidence path.

Recall probe matching is case-insensitive and Unicode-normalized. Probes with `expected_context` contribute to
`probe_coverage` and require every expected fragment. Probes with only `forbidden_context` express abstention and do
not contribute to that coverage. If there are no positive probes, `probe_coverage` is `1`. Every probe rejects
prepared context containing a forbidden fragment, and any forbidden match fails acceptance independently of the
`probe_coverage` threshold.

`execution.type` selects the adapter. The current contract implements `bub`. `execution.model` only declares whether
the workload needs a model:

- `false` keeps the Bub execution deterministic and does not pass a model into the agent environment;
- `true` requires the runtime to resolve a model before the Harbor Job starts.

Model identity, provider, endpoint, and authentication are runtime concerns. They do not belong in a portable
workload manifest. Replay evidence records the resolved model identity when a model is used, but never credentials.
The harness does not duplicate those settings: its Client consumes `POWERCONTEXT_CLIENT_*`, the Bub adapter forwards
native `BUB_*` values when a model is required, and the integration consumes `POWERCONTEXT_BUB_*`. Harbor receives an
`AgentConfig` directly and does not require model provider keys.

Adapter package versions and timeouts follow the same ownership rule. The adapter runtime pins Bub and the ACP
server and records their resolved versions in replay evidence. Harbor task definitions own agent and environment timeouts;
Bub owns `BUB_MODEL_TIMEOUT_SECONDS`. The workload manifest does not override either timeout domain.

## Selection and command surface

Workloads have stable IDs. Categories are selection metadata. The `acceptance` command selects one or more IDs,
categories, or the default `acceptance` category:

```bash
powercontext-e2e acceptance --output e2e/bub/results

powercontext-e2e acceptance \
  --id locomo-support-group \
  --id project-database-decision \
  --output e2e/bub/results

powercontext-e2e acceptance \
  --category long-horizon \
  --output e2e/bub/results
```

Both selectors use repeatable command options. Environment aliases and comma-separated selector syntax are not part
of the contract.

Long-horizon and live workloads remain acceptance evaluations. Their categories control selection; they do not
introduce separate execution modes or commands.

SQLite and OceanBase are runtime database variants, not execution adapters or workload categories. Required CI runs
the same deterministic `acceptance` workloads against both databases.

## Current Bub adapter

The current adapter enters every workload through a Harbor Job and Harbor's ACP runner. Harbor owns the task
environment and agent lifecycle. Bub runs through its supported installation path with the PowerContext integration.

Deterministic workloads use `model: false` and execute Bub commands such as `powercontext.remember` and
`powercontext.context`. They verify the Harbor-to-ACP-to-Bub tool path without invoking a model. Model-backed
workloads use `model: true` and additionally exercise Bub's model, context-injection, trajectory-capture, and
checkpoint hooks.

Both forms produce the same replay envelope and pass through the same Memory evaluator. A deterministic workload is
still a Bub workload because it traverses the Bub adapter; determinism is a property of model use, not adapter
identity.

## Shared execution flow

The harness performs common work before and after adapter execution:

```text
manifest and task provenance
  -> validated workload and isolated PowerContext scope
  -> execution adapter
  -> normalized replay evidence
  -> Memory evaluation
  -> report rendering
```

The harness records the pre-execution Memory baseline, invokes the adapter, records final Memory, and runs the
declared recall probes. A failed workload still writes the evidence collected before the failure.

## Input and instruction boundary

The pinned task owns execution input. Harbor tasks own agent-visible instructions. The workload manifest references
those inputs without copying them. Replay evidence records the resolved instruction identity and, where safe, its
content.

Evaluation probes remain separate from execution input. They run after the task and cannot provide hints to the
agent or task verifier.

## Memory acceptance

Memory acceptance uses observable evidence:

- the resolved task checksum and execution adapter match the manifest;
- required native execution evidence was recorded;
- eligible events were captured when capture is required;
- the run created Memory and completed required checkpoints or flushes;
- new Memory cites Sources captured during execution; and
- declared recall probes receive prepared context that satisfies their required and forbidden fragment contracts.

A deterministic workload may require fixed Memory fragments. A long-horizon task normally measures capture
coverage, grounding, and recall instead of requiring a fixed task answer.

Native task rewards, verifier results, duration, and model usage remain labels, scores, or metrics. A task may fail
its native grader and still pass Memory acceptance.

# Reference-level explanation

## Workload and adapter contracts

The manifest is the harness-level workload abstraction. The adapter owns execution-specific settings and translates
the pinned task into normalized evidence. Dataset adapters only produce standard task layouts and pin upstream
provenance; they do not run workloads, evaluate Memory, or render reports.

Dependencies flow in one direction:

```text
manifest and task provenance
  -> execution adapter
  -> replay evidence
  -> Memory evaluation
  -> report rendering
```

The evaluator reads replay evidence and cannot control the adapter. Report rendering reads the evaluation result and
does not recalculate acceptance.

## Evidence contract

Every workload writes one artifact directory:

| Artifact | Purpose |
| --- | --- |
| `replay.json` | Workload identity, adapter, task provenance, runtime observations, Memory snapshots, probes, and native evidence references. |
| `eval-report.json` | Assertions, scores, labels, metrics, and reasons. |
| `report.md` | A human-readable projection of the evaluation result. |

The replay records the dataset checksum, the workload's single `execution.type`, resolved model identity when present,
database identity, resolved instructions, and PowerContext scope state. The common envelope supports offline rescoring. Adapter-native
evidence remains typed within that envelope; the current Bub adapter records ACP summaries, captured events,
checkpoints, tool observations, and trajectory artifacts.

For negative recall contracts, the replay stores the pre-redaction match verdict rather than the matched text. This
keeps offline rescoring consistent with the live result without exposing a configured secret through the replay.

Final artifact sinks remove configured secrets. Native task artifacts may contain task content and require review
before publication.

## Adapter extension

If a future evaluation cannot be represented faithfully by Bub, `execution` can become a discriminated union with an
additional adapter. For example:

```yaml
execution:
  type: basic
```

```yaml
execution:
  type: codex
```

These examples reserve no implementation and assign no benchmark to either adapter. A new adapter must define its
typed execution settings and native evidence while reusing workload identity, selection, the replay envelope,
Memory evaluation, artifact layout, and reporting. Migrating an existing benchmark requires separate scope and
validation against its native semantics.

## Compatibility

The public command remains `acceptance`. Existing SQLite and OceanBase CI jobs continue to invoke
`make harness-compose-acceptance` and evaluate the same default acceptance category. ID and category selectors extend
that command without introducing a generic `run` command.

The current LoCoMo benchmark and SWE-Pro evaluation keep their existing commands, artifacts, and result contracts.
The LoCoMo-derived built-in workload remains a pinned sample and does not claim a complete benchmark result.

## Non-goals

This RFC does not replace RFC 0081, define a leaderboard, require registry publication, or introduce a new agent
protocol. It does not standardize private adapter internals or replace source-native graders. It does not migrate,
rewrite, or retire the current LoCoMo benchmark or SWE-Pro evaluation. It does not implement another execution
adapter.

## Acceptance criteria

The proposal is complete when:

- one Pydantic manifest represents deterministic, model-backed, and long-horizon workloads;
- `execution.type: bub` selects the current adapter and `execution.model` declares only whether a model is required;
- component-native runtime settings select model identity, provider, endpoint, and credentials without a harness
  mapping layer;
- one `acceptance` command selects one or more workload IDs and categories;
- repository and registry Harbor tasks use the same execution and provenance contracts;
- SQLite and OceanBase run the same deterministic acceptance category in required CI;
- model-backed Bub workloads record native evidence through Harbor and ACP;
- long-horizon Memory acceptance remains independent of native task reward;
- every replay identifies its adapter and supports offline rescoring; and
- the current LoCoMo benchmark and SWE-Pro evaluation remain unchanged.

# Drawbacks

The unified replay envelope must preserve adapter-native evidence without reducing it to untyped dictionaries.
Long-horizon runs can consume paid model capacity, require privileged containers, and produce large artifacts. The
required database matrix therefore covers deterministic workloads, while model-backed categories remain explicit
opt-in evaluations.

# Rationale and alternatives

Separate harnesses for deterministic samples, live agent runs, and long-horizon tasks would duplicate selection,
evidence, evaluation, and reporting. The shared contracts keep those responsibilities in one place while the
adapter isolates execution semantics.

Putting model names or authentication methods in manifests would make workloads depend on one operator environment.
A boolean requirement preserves the deterministic boundary while runtime settings select the available model and
credentials.

Using a source-native reward as Memory acceptance would answer whether the task was solved, not whether PowerContext
collected useful Memory. The native result remains available without replacing the Memory evaluator.

# Prior art

RFC 0081 separates runtime integration, workload execution, evidence collection, evaluation, and reporting. This
proposal keeps those boundaries and gives the built-in acceptance scenarios a shared workload and artifact contract.

Harbor provides pinned task environments, agent lifecycle management, and native task verification. Bub provides the
first observable execution adapter. The replay envelope keeps Harbor and Bub evidence typed while Memory acceptance
remains independent of the native task score.

# Unresolved questions

None. Adding another execution adapter, migrating an existing benchmark, and publishing model-backed artifacts each
require separate review.

# Future possibilities

The workload contract can add a `basic` or `codex` execution variant when a workload cannot be represented faithfully
through Bub. Such an adapter would reuse selection, replay, evaluation, and reporting rather than create another
harness.

The complete LoCoMo benchmark or SWE-Pro evaluation may move to the catalog after its native scoring and artifact
contracts have been validated against this workload model. Registry publication and shared artifact retention can be
considered separately once their privacy and operational requirements are defined.
