# PowerContext Skill-up guidance regression suite

This suite is a versioned, reproducible regression check for the packaged Claude Code
`powercontext-project-context` Skill. It measures deterministic Skill-text-to-tool-selection
and result-reporting behavior through skill-up transcripts. It targets only the skill-up
`claude_code` engine with the PowerContext MCP transport: it is not evidence for Codex,
Hermes, WorkBuddy, OpenCode, Pi, OpenClaw, the portable Agent Plugin, or any other host.

The suite contains an ordinary-coding negative control, an explicit Memory-save positive
control, empty Memory search, read-only Artifact Candidate inspection, and a controlled
failed-write reporting case. The checked-in configuration keeps `cases.parallelism: 1`,
loads the vendored Skill, runs the loaded/unloaded benchmark, and requests JSON, JUnit,
HTML, and transcript artifacts.

## Prerequisites and isolated Server

Run all commands from the repository root with a Claude Code installation and credentials
that skill-up can use. The suite/operator preflight requires skill-up v0.12.0; `skill.lock.json`
pins the vendored Skill contents and source revision, not the skill-up CLI:

```bash
export PATH="$(pwd -P)/.venv/bin:$PATH"
test "$(python3 -c 'import sys; print(sys.version_info >= (3, 11))')" = True
test "$(skill-up --version)" = "skill-up version 0.12.0"
evaluation/skill-up/sync-skill.sh --check
export POWERCONTEXT_SKILL_UP_ROOT="$(mktemp -d)"
export POWERCONTEXT_SERVER_DATABASE_URL="sqlite+aiosqlite:///$POWERCONTEXT_SKILL_UP_ROOT/powercontext.db"
export POWERCONTEXT_SKILL_UP_TOKEN=skill-up-fixture-token
export POWERCONTEXT_SERVER_ACCESS_MODE=enforced
export POWERCONTEXT_SERVER_AUTH_TOKEN="$POWERCONTEXT_SKILL_UP_TOKEN"
uv run powercontext server run --no-env-file
```

Run this block in the Server terminal and keep that Server running. The authentication
variables are exported before Server startup, so its fixture bearer token is active when the
MCP endpoint begins serving requests. Make a new
`POWERCONTEXT_SKILL_UP_ROOT` and database for every real run; do not point the suite at a
developer database or reuse a previous run's database. Serial case execution makes the
fresh database's state and case ordering deterministic.

The default real MCP endpoint is `http://127.0.0.1:8000/mcp`. Its Authorization header is
read from `evals/fixtures/mcp/powercontext.yaml` through `config_ref`, not from
`mcp.servers[]`; do not move the header into the server declaration.

An unauthenticated local Server may use an empty complete header only if skill-up and
Claude Code accept it. Otherwise, start the isolated Server with the fixture bearer token
above so the `config_ref` path is exercised consistently. Never commit a real credential.

## Validate and run

In a separate evaluation terminal, repeat the same fixture-token assignment, export the
vendored plugin root, wait for readiness, then derive the complete Authorization header from
that variable. Validate the configuration and run the positional evaluation path. `--config`
is global skill-up user configuration; it is not the evaluation path.

```bash
export PATH="$(pwd -P)/.venv/bin:$PATH"
test "$(python3 -c 'import sys; print(sys.version_info >= (3, 11))')" = True
export POWERCONTEXT_SKILL_UP_TOKEN=skill-up-fixture-token
export CLAUDE_PLUGIN_ROOT="$(pwd -P)/evaluation/skill-up/vendor/powercontext-plugin"
curl --fail --silent --show-error http://127.0.0.1:8000/health/ready
export POWERCONTEXT_CLAUDE_AUTHORIZATION="Bearer $POWERCONTEXT_SKILL_UP_TOKEN"
skill-up validate evaluation/skill-up/evals/eval.yaml
skill-up run evaluation/skill-up/evals/eval.yaml --baseline \
  --output-dir /tmp/powercontext-skill-up-first-run
```

The final command records Skill-loaded and unloaded benchmark results separately. Treat a
baseline comparison as a routing-regression comparison for this invocation, not as an
absolute model-quality score. Preserve the JSON, JUnit, HTML, and transcript artifacts from
the output directory selected by skill-up. Do not replace those artifacts with a summary
that omits a failed case.

## Transcript inventory and case interpretation

After a real run, inventory the recorded names before accepting or changing assertions. The
assertions use exact string equality in skill-up v0.12.0, so a similarly named operation or
an assumed namespace does not count.

These single-prompt cases intentionally use whole-transcript `tool_called` checks. In skill-up
v0.12.0, a Claude Code `input.prompt` run can record tools in its transcript while exposing no
per-turn judge results; `tool_called_in_turn` and `tool_not_called_in_turn` then fail with
`turn 1 does not exist`. Forbidden tools are therefore declared as `failure: tool_called` rules.

```bash
rg -o '"name":"[^"]+"' /tmp/powercontext-skill-up-first-run -g '*.json' -g '*.jsonl' \
  | sort -u
```

The normal cases connect to the isolated real Server. A real-mode write is evidence only
for that fresh evaluation Server and must remain readable through its supported path before
it is described as persistence evidence. The failed-Memory-save case alone replaces the
`powercontext` server with a case-level mocked MCP fixture. That mock exposes the production
tool name and returns a controlled failure; it tests attempted routing and truthful reporting,
not real persistence or an end-to-end Server failure.

## Lifecycle and limitations

The runnable lifecycle is: verify the pinned skill and vendor lock; create a fresh SQLite
database; start the isolated Server; wait for readiness; validate; run the benchmark; inspect
the five case outcomes and baseline comparison; inventory transcript tool names; retain all
four report artifact kinds; then stop the Server and discard the temporary database. Generated
workspaces and reports stay outside the vendored Skill. Do not modify the local `.skill-up.yaml`,
temporary Skill-local `evals/`, or generated Skill workspace as part of this suite.

skill-up's Claude Code runner uses `disableAllHooks` and `bypassPermissions`. Consequently,
this suite does not measure bounded recall, automatic Capture/Flush, real host approval
prompts, Memory quality, successful injection into a later prompt, full persistence
correctness, or any host other than Claude Code + MCP. It does not prove automatic Skill
discovery, publication, installation, commitment, execution, or end-to-end approval behavior.
Mocked failures are never presented as real persistence evidence.
