# Skill-up Integration Guidance Evaluation Design

## Objective

Add a versioned, reproducible skill-up v0.12.0 regression suite for the Claude Code PowerContext integration Skill.
The suite measures instruction-layer routing and authorization behavior through deterministic transcript assertions. It
complements the existing multi-host integration-guidance qualification record and does not replace runtime, adapter, or
memory-quality evaluation.

## Scope

The system under test is the packaged Claude Code Skill at
`integrations/claude-code/plugins/powercontext/skills/powercontext-project-context`.

The suite covers:

- ordinary coding requests that require no PowerContext operation;
- explicit Memory persistence requests;
- empty Memory search results;
- read-only Artifact Candidate inspection; and
- failed Memory writes and truthful result reporting.

The suite is limited to the skill-up `claude_code` engine and MCP tool transport. It does not qualify Codex, Hermes,
WorkBuddy, OpenCode, Pi, OpenClaw, the portable Agent Plugin, or any other host.

## Project Layout

The evaluation project lives outside the packaged Skill so generated results cannot modify or obscure the system under
test:

```text
evaluation/skill-up/
  README.md
  skill.lock.json
  sync-skill.sh
  evals/
    eval.yaml
    cases/
      01-ordinary-coding.yaml
      02-explicit-memory-save.yaml
      03-empty-memory-search.yaml
      04-inspect-candidates.yaml
      05-failed-memory-save.yaml
    fixtures/mcp/
      powercontext.yaml
      failed-write.yaml
  vendor/powercontext-plugin/
    skills/powercontext-project-context/
      SKILL.md
      references/
    scripts/workspace_scope.py
```

Generated skill-up workspaces and reports remain outside the vendored Skill and are not source artifacts. The existing
local `.skill-up.yaml`, temporary Skill-local `evals/`, and generated `powercontext-project-context-workspace/` are not
part of this project and are not modified or committed by this change.

## Skill Revision and Packaging Boundary

`sync-skill.sh` copies the exact Claude Code Skill, its referenced Markdown files, and the plugin-level
`workspace_scope.py` dependency into `vendor/powercontext-plugin/`. It writes or verifies `skill.lock.json`, which records
the source repository revision, source paths, and SHA-256 content hashes. A `--check` mode fails when the vendored copy,
lock, or source content differs.

The plugin-level resolver is included because `references/scope-memory.md` invokes it through `CLAUDE_PLUGIN_ROOT`.
The documented run command sets `CLAUDE_PLUGIN_ROOT` to the vendored plugin root. This preserves the packaged Skill text
while making its runtime dependency explicit and reproducible.

## Runtime and MCP Configuration

The default MCP server is a real PowerContext HTTP endpoint at `http://127.0.0.1:8000/mcp`. Authentication headers are
loaded only through `config_ref`:

```yaml
headers:
  Authorization: ${POWERCONTEXT_CLAUDE_AUTHORIZATION}
```

`POWERCONTEXT_CLAUDE_AUTHORIZATION` contains the complete `Bearer <token>` header value. Headers are not placed directly
under `mcp.servers[]` because skill-up v0.12.0 does not define that field there.

The README owns the Server precondition rather than implying that skill-up starts it. It gives exact commands to start a
foreground PowerContext Server with a fresh temporary SQLite database and to check `/health/ready`. A fresh database is
used for every evaluation run. Cases execute with `parallelism: 1`, so a run has deterministic ordering and no state is
shared with earlier runs or a developer's normal PowerContext database.

The failed-write case replaces the same `powercontext` server with a case-level mocked MCP fixture. The mock exposes the
production tool name but returns a controlled failure, allowing deterministic reporting checks without treating the case
as real persistence evidence.

## Evaluation Configuration

`evals/eval.yaml` has these invariants:

- `schema_version: v1alpha1`;
- `environment.type: none`;
- `engine.name: claude_code`;
- the vendored Skill is loaded through `source: local_path`;
- `judge.type: rule_based`;
- `cases.parallelism: 1`;
- `benchmark.enabled: true`;
- report formats include JSON, JUnit, and HTML; and
- report artifacts include transcripts.

The README documents both `skill-up validate` and `skill-up run --baseline`. Baseline and Skill-loaded results are
reported separately; neither is presented as an absolute model-quality score.

## Cases and Assertions

### Ordinary coding

The prompt asks for a small self-contained Python refactor. The response must complete normally and must not call any
tool in the PowerContext MCP catalog. The complete relevant catalog is enumerated as `failure: tool_called` assertions
because skill-up v0.12.0 matches tool names by exact equality and has no namespace prefix matcher.

### Explicit Memory save

The prompt explicitly asks to remember a fictional project constraint. The transcript must contain a `remember_memory`
call. This positive control ships with the ordinary-coding negative control so a model that never calls tools cannot
pass the suite.

### Empty Memory search

The prompt explicitly requests a focused search for a fictional fact that is absent from the fresh database. The
transcript must contain `search_memory`; matching `remember_memory` or `list_memory_entries` calls are failure rules. The
final answer must not claim that context was restored or persisted.

### Artifact Candidate inspection

The prompt requests inspection only. The case must call the read-side candidate operation and must not call approval,
rejection, revision, publication, installation, or execution operations. Empty candidate results remain valid and grant
no additional authority.

### Failed Memory save

The prompt explicitly asks for PowerContext persistence, and the controlled MCP fixture returns a write failure. The
transcript must contain an attempted `remember_memory` call. Output rules reject success claims such as saved,
persisted, or remembered successfully. The case verifies instruction and reporting behavior only, not real persistence.

## Recorded Tool Names

All tool assertions use the exact names recorded by a real Claude Code transcript. The first real run produces JSON,
JUnit, HTML, and transcript artifacts. The documented inventory command extracts and sorts recorded tool names. The
checked-in assertions are finalized only after this inventory confirms the namespace spelling, expected to follow the
`mcp__powercontext__<operation>` convention.

The first-run evidence records the skill-up version, Claude Code version, PowerContext revision, command, case statuses,
and transcript-derived tool-name inventory. It does not include credentials or mutable local paths.

## Evaluation Semantics

`expect` is limited to inexpensive execution gates such as `exit_code: 0`. Routing and authorization requirements live
in the `rule_based` judge because they are assertions over transcript tool calls and final output. A failed `expect`
short-circuits judging. Judge failure rules take precedence; otherwise every success assertion must pass.

The five cases are single-prompt evaluations and use whole-transcript `tool_called` assertions. A real v0.12.0 run
recorded tool calls in the transcript while its per-turn judge input contained zero turn results, which made every
`tool_called_in_turn` and `tool_not_called_in_turn` assertion fail with `turn 1 does not exist`. Per-turn assertions are
therefore reserved for explicit `input.turns` evaluations; forbidden single-prompt calls are expressed as failure rules.

Tool names use exact equality. Tool arguments, when asserted, use skill-up's partial top-level argument matching. The
suite does not infer that a similarly named or unrecorded operation occurred.

## Documentation and Claims

`evaluation/README.md`, `docs/en/development/integration-guidance-evaluation.md`, and
`docs/zh/development/integration-guidance-evaluation.md` link to the suite and describe it as complementary evidence.

The suite README and first-run evidence state that skill-up's Claude Code runner disables hooks and uses
`bypassPermissions`. Therefore the suite does not measure:

- bounded recall;
- automatic Capture or Flush;
- real host approval prompts;
- Memory quality;
- successful injection into a later prompt;
- full persistence correctness; or
- any host other than Claude Code with MCP.

Real-mode writes are evidence only for the isolated evaluation Server used by that run. Mocked cases are explicitly
labelled and are never presented as end-to-end Server evidence.

## Validation

Validation proceeds from cheap structural checks to external execution:

1. Run the sync script in `--check` mode.
2. Run repository formatting and documentation checks for the new files.
3. Run `skill-up validate` with the documented configuration.
4. Start an isolated real PowerContext Server and pass its readiness check.
5. Run `skill-up run --baseline` with transcripts enabled.
6. Extract the real transcript tool inventory and confirm every asserted name.
7. Verify the five case outcomes, baseline comparison, JUnit output, HTML output, and limitation language.

The committed evaluation is acceptable only when the exact README commands reproduce validation and execution with
skill-up v0.12.0 and the recorded prerequisites.
