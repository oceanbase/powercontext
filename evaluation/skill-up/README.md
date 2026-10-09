# Claude Code Skill guidance regression suite

This project evaluates the packaged `powercontext-project-context` Skill with
[skill-up v0.12.0](https://github.com/alibaba/skill-up/releases/tag/v0.12.0), pinned upstream source
`dd2ff3250074505204b969405bef2cdce4bae428`. It measures **Claude Code + MCP instruction/tool-selection behavior**
with deterministic `rule_based` assertions and controlled replies. Model behavior can still vary between runs.

It complements the [integration guidance qualification record](../../docs/en/development/integration-guidance-evaluation.md).
It neither replaces those multi-surface observations nor contributes to the SWE-bench Pro or LoCoMo scores.
Offline validation and synthetic tests do not establish real model coverage; retain authenticated runs' full evidence.

## Prerequisites and installation

Use Python 3.11+, Git, Node.js, Bash, skill-up **0.12.0**, and an installed/authenticated Claude Code CLI with access to
`claude-sonnet-4-6`. `python3`, `node`, and `claude` must be on PATH. The shell examples below target Linux/macOS
or WSL; native Windows also supports model runs with the Bash/PATH setup below. The `none`
runtime invokes host commands and is not an OS sandbox. Use a disposable evaluation account/workspace: the runner
disables hooks and bypasses Claude Code permission prompts. A paid Claude model run executes both arms (12 user turns).

From this directory on Linux x86-64 (Python setup also works on macOS/WSL):

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-dev.txt
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

The two Bash paths serve skill-up and Claude Code respectively.

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

`run.py` is the acceptance entry point. It verifies the pin and runner version, requires a new output directory, sets
`CLAUDE_PLUGIN_ROOT` to `harness/plugin-root`, and invokes
`skill-up run evals/eval.yaml --baseline --iteration 1 --output-dir <directory>`. It records tool versions, input hashes
and exit status, copies exact inputs into `inputs/`, then checks archived sessions even when the runner fails.
To recheck a retained run without changing its original evidence:

```bash
python3 report.py --iteration results/first-live-run/iteration-1 \
  --skill-lock results/first-live-run/skill-lock.json --output-dir results/rechecked --engine-exit-code 0
```

Replace `0` with the original runner exit code. Use the pin tested at execution time, never today's pin for an older run.
Retain the whole output directory: provenance, pin, input snapshot, native reports, raw JSONL and qualification reports.

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

Both arms receive the same `evals/fixtures/repos/powercontext-tool-contract/CLAUDE.md`: required/optional top-level
argument names, types and defaults derived from OpenAPI. This fills the generic mock schemas' parameter information gap
without providing routing rules, fixture values or answers. Refresh it with `python3 validate_suite.py --write-tool-contract`
after reviewing an API change. Validation rejects reference drift or overridden case context.

`with_skill` means the pinned Skill was installed and available; `without_skill` omits that installation. These labels
do not prove the agent read the Skill's full body. Inspect recorded Skill invocations or file reads before making a
claim about body consumption; ordinary coding does not require a Skill invocation.

## Pinning a Skill revision

`vendor/powercontext-project-context` contains the packaged Skill files and its managed-refresh metadata. `skill-lock.json` identifies
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
  the MCP server key. The supplement reads actual `tool_use` records from the final cumulative Claude JSONL, retaining
  IDs, arguments, line references and hashes; `result.json` supplies grades, not calls. Review the first real inventory.
  Uncalled catalog names are not observed names. Wrong namespaces or missing required calls fail qualification.
- **C3 — Disabled hooks.** skill-up sets `disableAllHooks: true`, and `/v1/context/prepare` is not an MCP tool. This suite
  cannot observe bounded recall, automatic Capture or Flush, nor does it measure memory quality.
- **C4 — Permission model.** The runner uses `bypassPermissions`. Scores measure instruction/tool selection, not real
  host confirmation dialogs, approval enforcement or permission UX.
- **C5 — State.** Stateless mocks isolate PowerContext responses, not the host profile. Claude inherits user settings,
  native memory and global Skills/MCP; use a dedicated evaluation profile. Do not use `CLAUDE_CONFIG_DIR` to isolate it:
  v0.12.0 looks for transcripts under `HOME/.claude/projects` regardless. Real mode would require isolated Scopes/reset.
- **C6 — Lifecycle.** `environment.type: none` starts no PowerContext Server. The current runner starts its mock itself.
  The unused HTTP reference is not a ready-to-run real suite: it requires a separately started Server, an authenticated
  readiness/tool-catalog check, dedicated Scopes and explicit cleanup before any real case is admitted.
- **C7 — Plugin boundary.** The packaged Skill resolves `scripts/workspace_scope.py` from the host-provided plugin root
  when available, with an installed-Skill fallback. The wrapper deliberately sets `CLAUDE_PLUGIN_ROOT` to
  `harness/plugin-root`, whose fixture returns only the fictional Scope and rejects bind/clear operations. The pinned
  Skill is unchanged by the harness. This evaluates guidance after controlled Scope resolution; it does not qualify
  the actual plugin helper, the fallback path, or standalone packaging.
- **C8 — Host boundary.** Only Claude Code + MCP is covered. Results do not qualify Codex, Hermes, WorkBuddy, OpenCode,
  Pi, OpenClaw, or the portable Agent Plugin.

The built-in mock publishes generic descriptions and permissive argument schemas. The shared workspace reference
supplies source-derived parameter signatures equally to both arms, but does not make the mock validate them. Only
explicitly asserted arguments are graded; this is not a real API contract test. Failed writes are controlled
application-error JSON inside a successful MCP text envelope, not protocol errors or network timeouts.
Output patterns cover known false-success phrases, not all possible claims; review final answers too.

Every logical turn must finish with an assistant `end_turn` response after all recorded tool calls have matching results.
The supplement checks every relevant call's Scope so a valid call cannot hide an additional wrong-Scope call.

Native artifacts live in `iteration-1/`: `result.json`, `report.xml` (JUnit), HTML and `benchmark.json`, plus per-case
raw sessions under `<case>/<with_skill|without_skill>/outputs/agent/run/`. `--baseline` produces both arms in
one invocation. Native benchmark pass rate averages assertion pass rates; the qualification supplement reports whole-case
pass rates with its additional transcript checks and the loaded-minus-unloaded delta. A baseline behavioral
failure is comparison data, while a missing/error baseline is incomplete evidence. Report all five cases and both arms,
even when the Skill has no improvement or performs worse. Do not aggregate these scores with task-outcome benchmarks.

The `Skill guidance validation` workflow runs the credential-free checks on relevant pull requests. The authenticated
model command is a separate gate for a maintainer-provided Claude environment; a green offline job is not a model pass.
