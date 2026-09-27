# Claude Code Skill guidance regression suite

This project evaluates the packaged `powercontext-project-context` Skill with
[skill-up v0.12.0](https://github.com/alibaba/skill-up/releases/tag/v0.12.0), pinned upstream source
`dd2ff3250074505204b969405bef2cdce4bae428`. It measures **Claude Code + MCP instruction/tool-selection behavior**
with deterministic `rule_based` assertions and controlled replies. Model behavior can still vary between runs.

It complements the [integration guidance qualification record](../../docs/en/development/integration-guidance-evaluation.md).
It neither replaces those multi-surface observations nor contributes to the SWE-bench Pro or LoCoMo scores.
There is no checked-in live model qualification result. Offline validation and synthetic reporter tests do not satisfy
the first real transcript requirement; retain the first authenticated run's complete evidence directory before claiming
measured guidance coverage or marking that acceptance item complete.

## Prerequisites and installation

Use Python 3.11+, Git, Node.js, Bash, skill-up **0.12.0**, and an installed/authenticated Claude Code CLI with access to
`claude-sonnet-4-6`. `python3`, `node`, and `claude` must be on PATH. The shell examples below target Linux/macOS
or WSL; native Windows also supports model runs with the Bash/PATH setup below. The `none`
runtime invokes host commands and is not an OS sandbox. Use a disposable evaluation account/workspace: the runner
disables hooks and bypasses Claude Code permission prompts. A paid Claude model run executes both arms (12 user turns).

From this directory, install Python validation dependencies into a virtual environment:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
```

For Linux x86-64, install the exact runner release into a local directory:

```bash
mkdir -p .venv/skill-up
curl -fsSL https://github.com/alibaba/skill-up/releases/download/v0.12.0/skill-up_0.12.0_linux_amd64.tar.gz \
  -o .venv/skill-up/skill-up.tar.gz
printf '%s  %s\n' ec2631e45702d460b69cd700e1847f5562ef2e971f09c23be2efcec4e0f583c6 \
  .venv/skill-up/skill-up.tar.gz | sha256sum --check -
tar -xzf .venv/skill-up/skill-up.tar.gz -C .venv/skill-up
export PATH="$PWD/.venv/skill-up:$PATH"
skill-up --version
```

Other platforms must use their matching v0.12.0 release asset and verify its published checksum. Authenticate Claude
Code using its supported login/API-key setup, then check `claude --version` and `claude auth status`. Do not store tokens
in this project or pass them as command-line arguments.

On native Windows, create the virtual environment with `python -m venv .venv` and install the same requirements using
`.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`. The Scope helper needs `python3` to resolve to that
environment too; copying its interpreter within the same `Scripts` directory supplies that name. In PowerShell,
adjust the installation paths, retaining Node.js on PATH:

```powershell
$evalVenv = (Resolve-Path .venv).Path
Copy-Item -LiteralPath "$evalVenv\Scripts\python.exe" -Destination "$evalVenv\Scripts\python3.exe"
$evalGit = 'C:\tools\PortableGit'
$env:SKILL_UP_BASH = "$evalGit\usr\bin\bash.exe"
$env:CLAUDE_CODE_GIT_BASH_PATH = "$evalGit\bin\bash.exe"
$env:PATH = "$evalVenv\Scripts;C:\tools\skill-up;C:\tools\claude;$evalGit\bin;$env:PATH"
```

The two Bash paths serve skill-up and Claude Code respectively. This native configuration has been exercised with
real model calls; `validate` and `--dry-run` alone do not verify it.

## Validate and run

No PowerContext Server, database, MCP token, or readiness request is needed for this **mocked** suite. skill-up owns the
Node stdio MCP process lifecycle, starting a fresh mock in each case/arm workspace. Responses are stateless, so a save
cannot contaminate another case or run. The fixture Scope `skill-up-fixture-scope` is fictional. Keep `parallelism: 1`.

```bash
python3 sync_skill.py --check
python3 validate_suite.py
python3 -m unittest discover -s tests -v
skill-up validate evals/eval.yaml
python3 run.py --dry-run
python3 run.py --output-dir results/first-live-run
```

`run.py` rejects an existing output directory, verifies the Skill pin and runner version, and records tool versions,
input hashes and both arms. It sets `CLAUDE_PLUGIN_ROOT` to the controlled Scope helper, then executes exactly:

```bash
export CLAUDE_PLUGIN_ROOT="$PWD/harness/plugin-root"
skill-up run evals/eval.yaml --baseline --iteration 1 --output-dir results/manual-run
```

The wrapper is the acceptance entry point because the raw command alone does not bind results to the pin or validate
transcript completeness. To supplement an existing raw run, preserve the pin used at execution time and run:

```bash
python3 report.py --iteration results/manual-run/iteration-1 \
  --skill-lock skill-lock.json --output-dir results/manual-run --engine-exit-code 0
```

Replace `0` with the original skill-up process exit code; never substitute success for a failed raw run. The wrapper
passes that recorded exit code automatically.

Do not attach today's lock file to a historical run unless it is the actual tested pin. Keep `provenance.json`, the copied
`skill-lock.json`, all native reports, raw JSONL sessions and the qualification supplement together. A nonzero model run
still gets a report; missing/error evidence must remain visible. The wrapper needs a new directory for every run.
The `inputs/` directory retains the actual suite, fixtures, harness and vendored Skill content copied before execution;
its hashes are recorded in provenance. Retain this snapshot when regenerating or reviewing a historical report.

## Cases and controls

| Case | Required behavior |
| --- | --- |
| `ordinary-coding` | Answer the self-contained coding task and call no PowerContext tool. |
| `explicit-save` | Call `mcp__powercontext__remember_memory` in the resolved fixture Scope; avoid unrelated tools. |
| `empty-search` | Call search; report the controlled empty result; do not inventory or write. The following coding turn uses current context only. |
| `inspect-candidates` | List and inspect the candidate; never approve, reject, revise or publish it. |
| `failed-save` | Attempt the explicitly requested save, report `FIXTURE_WRITE_DENIED`, and do not claim persistence. |

The ordinary-coding negative and explicit-save positive controls are inseparable. Full mock catalogs are retained in
the failed-save override because v0.12.0 replaces the entire server fixture; dropping a forbidden tool could otherwise
make its negative assertion pass trivially. Case files are listed explicitly: v0.12.0 does not expand `cases.files`
globs. Even single-turn cases use `input.turns`; turn-scoped rules do not work with its single-shot `input.prompt` path.
All relative config paths start at this project root, not at `evals/`.

Each case uses the same `context.repo_fixture: evals/fixtures/repos/powercontext-tool-contract`. The runner copies its
`CLAUDE.md` to the workspace root before Claude starts, in both arms. This neutral API reference lists the required and
optional top-level argument names, types and defaults for all 34 tools, derived from `openapi/powercontext.yaml`.
It supplies parameter information omitted by skill-up's generic mock schemas, such as `scope_id`, `kind` and `text`;
it contains no intent-routing rules, authorization rules, fixture Scope values or expected results. Scope argument
assertions remain enabled. The validator checks this reference against the source contract and rejects missing or
overridden case context. To refresh it after reviewing an API change, run:

```bash
python3 validate_suite.py --write-tool-contract
```

The follow-up coding answer in `empty-search` accepts the exact list `[1, 2]` with whitespace or paired inline-code
backticks. Different numbers, additional elements and explanatory prose still fail; all tool-call boundaries remain
independent of answer formatting.

`with_skill` means the pinned Skill was installed and available; `without_skill` omits that installation. These labels
do not prove the agent read the Skill's full body. Inspect recorded Skill invocations or file reads before making a
claim about body consumption; ordinary coding does not require a Skill invocation.

## Pinning a Skill revision

`vendor/powercontext-project-context` contains the four unchanged packaged Skill files. `skill-lock.json` identifies
their source commit, individual SHA-256 hashes and an aggregate hash of sorted `<sha256>  <relative-path>\n` lines.
Vendoring reads immutable Git blobs, and `.gitattributes` keeps the vendor bytes in LF form across platforms.

To evaluate a new packaged Skill, first commit that change, then explicitly update and review the pin:

```bash
python3 sync_skill.py --revision HEAD
python3 sync_skill.py --check
```

`python3 sync_skill.py` replays the existing locked revision, which must be available in local Git history. `--check`
needs no historical fetch: it validates vendor hashes and compares the current packaged Skill, normalizing checkout
CRLF to Git LF. CI fails if the packaged Skill drifts without updating the pin. A changed pin requires a new model run.

## Interpreting evidence and limits

- **C1 — Authentication.** The enabled fixture uses `mode: mocked`; v0.12.0 ignores mock headers entirely. Authentication
  is not tested. `evals/fixtures/mcp/powercontext-http.example.yaml` is an unused real-HTTP reference: only `config_ref`
  can supply its `headers`. `POWERCONTEXT_CLAUDE_AUTHORIZATION` is the complete `Bearer <token>` value, whereas Python
  SDK configuration may take a bare token. Never put `headers` directly under `mcp.servers[]`: they are silently ignored.
- **C2 — Exact names.** Rule matching is exact. Assertions expect `mcp__powercontext__<tool>`, with `powercontext` from
  the MCP server key. The qualification supplement extracts actual `tool_use` names from Claude's saved session JSONL,
  It reads the final cumulative session once, retaining tool-call IDs. `result.json` has no calls in v0.12.0. Missing sessions,
  missing positive calls or an unexpected PowerContext namespace make qualification incomplete. Uncalled forbidden
  names remain declared catalog expectations; they must not be represented as observed names. Review the first real
  inventory before accepting the namespace; do not shorten names just to obtain passing negatives.
- **C3 — Disabled hooks.** skill-up sets `disableAllHooks: true`, and `/v1/context/prepare` is not an MCP tool. This suite
  cannot observe bounded recall, automatic Capture or Flush, nor does it measure memory quality.
- **C4 — Permission model.** The runner uses `bypassPermissions`. Scores measure instruction/tool selection, not real
  host confirmation dialogs, approval enforcement or permission UX.
- **C5 — State.** Fresh, stateless MCP fixtures isolate PowerContext replies across cases and arms; mocked PowerContext
  writes are not persisted. Each case gets a new workspace, but Claude still inherits its active user profile, including
  user settings, native auto-memory, global Skills and MCP configuration. Native memory writes can persist outside the
  workspace. Use a dedicated evaluation user profile; this suite does not establish complete host-state isolation,
  real Server persistence or transactional behavior. Do not use `CLAUDE_CONFIG_DIR` as an isolation workaround:
  v0.12.0 locates Claude transcripts under `HOME/.claude/projects` regardless of that setting. A future real suite needs
  isolated Scopes/reset per run.
- **C6 — Lifecycle.** `environment.type: none` starts no PowerContext Server. The current runner starts its mock itself.
  The unused HTTP reference is not a ready-to-run real suite: it requires a separately started Server, an authenticated
  readiness/tool-catalog check, dedicated Scopes and explicit cleanup before any real case is admitted.
- **C7 — Plugin boundary.** The packaged Skill references `${CLAUDE_PLUGIN_ROOT}/scripts/workspace_scope.py`, outside its
  Skill directory. The wrapper deliberately provides `harness/plugin-root/scripts/workspace_scope.py`, a fixture that
  returns only the fictional Scope and rejects bind/clear operations. The Skill text is unchanged. This evaluates the
  guidance after controlled Scope resolution; it does not qualify the actual plugin helper or standalone packaging.
- **C8 — Host boundary.** Only Claude Code + MCP is covered. Results do not qualify Codex, Hermes, WorkBuddy, OpenCode,
  Pi, OpenClaw, or the portable Agent Plugin.

The built-in mock publishes generic descriptions and permissive argument schemas. The shared workspace reference
supplies source-derived parameter signatures equally to both arms, but does not make the mock validate them. Only
explicitly asserted arguments are graded; this is not a real API contract test. Failed writes are controlled
application-error JSON inside a successful MCP text envelope, not protocol errors or network timeouts.
Output patterns catch known false-success formulations;
they are not a general truthfulness classifier. Review final answers alongside the raw transcript.

Native artifacts live in `iteration-1/`: `result.json`, `report.xml` (JUnit), HTML and `benchmark.json`, plus per-case
raw sessions under `<case>/<with_skill|without_skill>/outputs/agent/run/`. `--baseline` produces both arms in
one invocation. Native benchmark pass rate averages assertion pass rates; the qualification supplement additionally
reports whole-case pass rates and the loaded-minus-unloaded delta. Do not conflate the two. A baseline behavioral
failure is comparison data, while a missing/error baseline is incomplete evidence. Report all five cases and both arms,
even when the Skill has no improvement or performs worse. Do not aggregate these scores with task-outcome benchmarks.

The `Skill guidance validation` workflow runs the credential-free checks on relevant pull requests. The authenticated
model command is a separate gate for a maintainer-provided Claude environment; a green offline job is not a model pass.
