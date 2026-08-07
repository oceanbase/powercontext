- Proposal Name: `end_to_end_evaluation_architecture`
- Start Date: 2026-08-07
- RFC PR: [oceanbase/powercontext#81](https://github.com/oceanbase/powercontext/pull/81)
- Related RFCs: [RFC 0014](0014_memory_layer_design.md),
  [RFC 0016](0016_pydantic_ai_inference_integration.md),
  [RFC 0046](0046_observability_foundations.md), and
  [RFC 0080](0080_memory_search_reranking.md)

# Summary

PowerContext needs an E2E suite that answers whether the product works through its public boundaries. It covers three
kinds of evidence:

| Scope | What it proves | Oracle |
| --- | --- | --- |
| General E2E | PowerContext components compose into a complete business flow | Deterministic public behavior |
| Scenario replay | A real agent can capture and recall context across independent sessions | Behavior, regression, and replay expectations |
| Sampled scenarios | The same harness works on fixed conversation and repository cases | Materialized expectations or a source-native result |

The first replay harness uses Bub. It runs with SQLite and OceanBase, calls a real model, and records enough evidence to
understand a failure or rescore the run offline. Bub is an implementation choice, not the subject of this RFC.

# Motivation

Focused tests remain useful, but they do not prove that the CLI, Client, Server, database, agent integration, and model
work together. A benchmark score has the opposite problem: it can show a quality change without showing which part of
the path failed. The E2E suite sits between them. It runs a complete path and keeps the input, intermediate state, and
result together.

Tests must pay for their maintenance. A short script whose correctness is clear from reading it may need no test. A
test that checks a private buffer size, call order, module layout, or every normalized internal error adds work without
protecting a user.

Only two kinds of cases belong in this suite:

- A behavior test protects an interface or experience that a user can observe. It should survive an internal rewrite.
- A regression test records a defect that is likely to recur. It reproduces the externally wrong result, not the
  implementation mistake that caused it.

# Guide-level explanation

## Harness-driven development

Harness-driven development treats an executable scenario as the acceptance boundary for a change. The developer or
development agent produces the commit. A separate evaluation agent exercises that commit through the same interfaces
available to a user:

```text
development agent -> commit under test

scenario -> harness -> evaluation agent -> system under evaluation
                |                              |
                +<------ recorded evidence <---+
                             |
                             v
                       oracle and report
```

The evaluation agent uses a separate session, workspace, and PowerContext scope. It receives the scenario and allowed
repository state, but not the development conversation or unstated implementation rationale. It may use the same model
as the development agent. Independence comes from context and state isolation, not from requiring another model vendor.

This separation keeps evaluation out of the development agent's history and working state. It can run locally or in CI
without disturbing the development loop. It also gives the result an independent perspective: the evaluation agent
must discover and use the feature from committed behavior instead of relying on knowledge acquired while building it.
This catches missing instructions, hidden setup, and accidental dependence on a dirty workspace. It is not a substitute
for human review or an unbiased external audit.

The working loop is short: state the observable behavior, implement the change, replay the scenario with the evaluation
agent, inspect the evidence, and use that result for the next change. The scenario remains stable while implementations
can be replaced.

Harness-driven development does not require a new case for every change:

| Change | Evaluation action |
| --- | --- |
| Existing behavior changes internally | Run the relevant existing cases |
| A user-visible contract is added | Add or extend a behavior case |
| A recurring defect is fixed | Add a regression case |
| Only private implementation changes | Add no E2E case unless the risk crosses a public boundary |

## Evaluation target and evidence mode

The evaluation target states what the run claims to verify: a PowerContext flow, an agent journey, or a sampled
workload. The evidence mode states how the claim is checked:

| Evidence mode | Execution | Use |
| --- | --- | --- |
| Deterministic acceptance | Public interfaces with deterministic capabilities | Per-commit behavior and regression checks |
| Live replay | Independent evaluation agent with a real provider | Complete agent and model path |
| Offline rescore | A recorded replay without rerunning the system | Evaluator changes and result comparison |

Every result names both. A deterministic run cannot support a provider claim. An offline rescore cannot prove that the
current commit still executes. A live replay is evidence for its recorded model, budget, database, and scenario, not a
universal correctness claim.

# Reference-level explanation

## Architecture boundaries

The scenario defines the goal, ordered inputs, observable expectations, and budget. The harness owns environment setup,
agent lifecycle, isolation, and evidence capture. The evaluation agent operates the product. The system under
evaluation is the tested PowerContext commit together with its integration, model configuration, workspace, and
database. The oracle interprets recorded evidence and produces the report.

The development agent is not part of the evaluated execution path. The harness does not infer intent from development
logs, and the evaluator does not inspect private implementation details to decide whether the scenario passed.

## General E2E

General E2E lives under `tests/e2e/` and runs through `make e2e-test`. It enters through a supported CLI, Client, HTTP,
MCP, or Runtime boundary and observes the result through another supported boundary. Cases cover complete flows such
as Source capture, Memory processing and recall, Handoff, reviewed Artifacts, authentication, statistics, restart, and
observability.

These tests are deterministic. They may inject a deterministic generation or embedding capability when the provider
is not the subject of the case. That proves product composition, not provider compatibility.

## Scenario replay

Live replay runs a real provider through an agent harness:

```text
scenario input
  -> agent harness
  -> PowerContext integration
  -> PowerContext Server
  -> Memory and prepared context
  -> agent output
  -> evaluation report
```

One replay uses one PowerContext scope. Each step uses a new agent session and cannot read earlier agent history. State
crosses the session boundary only through PowerContext.

The harness records prepared context before each agent run, then records the final output, agent spans, and public
Memory state. A failed step stops the sequence and still produces a partial report.

## Sample integration

Sampling is an authoring step. It turns selected source cases into committed fixtures or manifests. CI never chooses a
new random sample while evaluating a commit.

For each sample set, the repository records:

- the source revision or fingerprint;
- stable case IDs and a versioned selection policy;
- the agent-visible input and evaluator-only reference data; and
- the model, harness, PowerContext commit, execution budget, and attempt policy used by a result.

LoCoMo conversation samples and SWE-bench Pro repository samples use the same rules. Conversation samples include the
declared chronological input. Selecting only sessions named by gold evidence is forbidden. Repository cases use a
fresh workspace and PowerContext scope for every attempt. Gold answers, evidence annotations, hidden tests, reference
patches, and grading results never enter agent or PowerContext input.

A source-native result remains authoritative when one exists. Memory, prepared context, spans, latency, and usage help
explain the run but cannot replace that result. A small committed set provides fast feedback. It does not support a
claim about the complete source distribution.

## Scenario fixture

The initial replay contract is strict YAML:

```yaml
schema: powercontext.session-replay/v1
id: project-database-decision
sessions:
  - id: capture
    input: >-
      Store this durable project decision in PowerContext: the project selected OceanBase because it needs
      MySQL-compatible, multi-node persistent storage for shared agent context.
    expected_memory:
      - OceanBase
      - MySQL-compatible
      - multi-node persistent storage
  - id: recall
    input: What database did this project select, and why?
    expected_context:
      - OceanBase
      - MySQL-compatible
      - multi-node persistent storage
    expected_answer: >-
      The project selected OceanBase because it needs MySQL-compatible, multi-node persistent storage for
      shared agent context.
```

Expectations describe meaning visible through a public boundary. They do not snapshot prompts, SQL, database IDs,
complete model text, private trace shape, or tool order.

A sample-derived fixture may also contain source identity, source revision, selection policy, and stable case IDs.
These fields form one provenance block. Loading fails when the source revision or an ID does not match.

## Evidence

Each live run produces three artifacts:

| Artifact | Contents |
| --- | --- |
| `replay.json` | Scenario, non-secret run identity, outputs, prepared context, Memory snapshots, and agent span tree |
| `eval-report.json` | Assertions, labels, scores, metrics, and reasons |
| `report.md` | A short report for a reviewer or CI summary |

`replay.json` is self-contained so offline scoring does not need to join separate input, trace, Memory, and output
files. It distinguishes setup or execution failure from a completed result with low quality.

The bundle contains user-visible text and receives stricter handling than normal telemetry. Credentials, authorization
headers, database URLs, and provider secrets are removed. Live runs require trusted events and bounded artifact
retention.

## Trace and model configuration

The evaluator owns the OpenTelemetry tracer provider. The harness emits agent and model spans through that provider,
and Pydantic Evals evaluates its native span tree. The harness does not add an OTLP receiver, protobuf decoder, generic
attribute converter, or second span model.

A run identifies these model roles separately:

- the agent model;
- PowerContext generation;
- PowerContext embedding; and
- the evaluation judge.

The initial Bub harness reads its agent model from `BUB_MODEL`, `BUB_API_KEY`, optional `BUB_API_BASE`, and bounded
`BUB_CLIENT_ARGS`. It may pass that configuration to PowerContext generation and the judge only when the mapping is
explicit and lossless. Explicit PowerContext settings take precedence. Embedding always requires its own profile.

## Evaluation

Pydantic Evals receives the complete replay observation. These failures block acceptance:

- setup or agent execution did not complete;
- a declared session did not run;
- expected Memory is absent after a step; or
- expected prepared context is absent before the dependent step.

Answer quality is diagnostic by default because live model output can vary. A trusted configuration may make it
blocking after the case has shown enough stability. Duration, token use, span count, and Memory additions are metrics,
not assertions, unless a scenario declares an external budget.

## Database matrix

Behavior and replay scenarios run with SQLite and OceanBase. The input and expectations stay the same. The matrix
checks public behavior across both deployments, not identical latency, SQL, or physical plans.

# CI

Pull requests and main branch commits run general E2E plus deterministic scenario acceptance for both databases. Live
replay runs only on trusted events that have provider credentials. The committed sampled set runs on its declared
trusted or scheduled cadence.

The matrix does not fail fast. Each database uploads its own evidence even when the other one fails. Long term reports
compare only runs with the same sample set, model configuration, execution budget, and attempt policy.

# Non-goals

This RFC does not define a general evaluation platform, dataset registry, agent protocol, or harness plugin system. It
does not replace source-native grading. It does not test private implementation details for coverage.

The first implementation stays close to Bub and PowerContext. Shared abstractions require a second working harness and
a separate design review.

# Acceptance criteria

The design is complete when:

- general E2E covers complete public PowerContext flows;
- independent agent sessions share durable state only through PowerContext;
- the same behavior and replay scenarios run with SQLite and OceanBase;
- live replay uses a real provider and records model identity;
- one replay bundle contains input, output, Memory, prepared context, and agent spans;
- Pydantic Evals can score that bundle online or offline without a custom OTLP receiver;
- sampled inputs are fixed, reviewable, isolated, and free of reference leakage;
- CI separates infrastructure failure, blocking acceptance, and diagnostic quality; and
- every test protects observable behavior or a concrete regression.

# Open questions

- Which model and embedding profiles are stable and affordable enough for trusted CI?
- Which cases and budgets define the first committed sample set?
- Which non-sensitive fields should be retained for long term trend reports?
