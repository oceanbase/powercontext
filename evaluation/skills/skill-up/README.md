# Claude Code Skill evaluation

This suite compares Claude Code behavior with and without the packaged `powercontext-project-context` Skill. It uses [skill-up v0.12.0](https://github.com/alibaba/skill-up/releases/tag/v0.12.0), upstream source `dd2ff3250074505204b969405bef2cdce4bae428`, with controlled MCP replies and deterministic `rule_based` assertions.

## Measurement scope

Both arms receive the same tool catalog and source-derived argument reference. The Skill is installed only in `with_skill`. The cases measure tool selection, specified arguments, and reported results. They do not measure Memory quality or contribute to SWE-bench Pro or LoCoMo scores. Other host measurements are recorded in the [guidance evaluation record](../../../docs/en/development/integration-guidance-evaluation.md).

| Case | Required behavior |
| --- | --- |
| `ordinary-coding` | Answer the self-contained coding task and call no PowerContext tool. |
| `explicit-save` | Call `mcp__powercontext__remember_memory` in the resolved fixture Scope; avoid unrelated tools. |
| `empty-search` | Call search; report the controlled empty result; do not inventory or write. The following coding turn uses current context only. |
| `inspect-candidates` | List and inspect the candidate; never approve, reject, revise or publish it. |
| `failed-save` | Attempt the explicitly requested save, report `FIXTURE_WRITE_DENIED`, and do not claim persistence. |

## Prerequisites and installation

Use Python 3.11+, Git, Node.js, Bash, skill-up 0.12.0, and an installed/authenticated Claude Code CLI with access to `claude-sonnet-4-6`. `python3`, `node`, and `claude` must be on PATH. The shell examples below target Linux/macOS or WSL; native Windows also supports model runs with the Bash/PATH setup below. The `none` runtime invokes host commands and is not an OS sandbox. Use a disposable evaluation account/workspace: the runner disables hooks and bypasses Claude Code permission prompts. A paired live run sends 12 user turns and may incur model charges.

From the repository root on Linux x86-64 (Python setup also works on macOS/WSL):

```bash
cd evaluation/skills/skill-up
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

Other platforms must use their matching v0.12.0 release asset and verify its published checksum. Authenticate Claude Code using its supported login/API-key setup, then check `claude --version` and `claude auth status`. Do not store tokens in this project or pass them as command-line arguments.

<details>
<summary>Native Windows setup</summary>

On native Windows, create the virtual environment with `python -m venv .venv` and install the same requirements using `.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`. The Scope helper needs `python3` to resolve to that environment too; copying its interpreter within the same `Scripts` directory supplies that name. In PowerShell, adjust the installation paths, retaining Node.js on PATH:

```powershell
$evalVenv = (Resolve-Path .venv).Path
Copy-Item -LiteralPath "$evalVenv\Scripts\python.exe" -Destination "$evalVenv\Scripts\python3.exe"
$evalGit = 'C:\tools\PortableGit'
$env:SKILL_UP_BASH = "$evalGit\usr\bin\bash.exe"
$env:CLAUDE_CODE_GIT_BASH_PATH = "$evalGit\bin\bash.exe"
$env:PATH = "$evalVenv\Scripts;C:\tools\skill-up;C:\tools\claude;$evalGit\bin;$env:PATH"
```

The two Bash paths serve skill-up and Claude Code respectively.

</details>

## Pin a Skill revision

`vendor/powercontext-project-context` contains the shared Skill from `integrations/agent-plugin/powercontext/skills/powercontext-project-context`. `skill-lock.json` records its committed source revision and file hashes.

To evaluate a new packaged Skill, first commit that change, then explicitly update and review the pin:

```bash
python3 sync_skill.py --revision HEAD
python3 sync_skill.py --check
```

`python3 sync_skill.py` restores the locked revision, which must be available in local Git history. `--check` verifies the vendor and current packaged Skill against the pin without fetching history. CI rejects drift. A changed Skill requires a new model run before claiming qualification.

## Validate offline

Run these commands from this suite's directory after installing its dependencies and selecting the Skill pin:

```bash
python3 sync_skill.py --check
python3 validate_suite.py
python3 -m unittest discover -s tests -v
skill-up validate evals/eval.yaml
python3 run.py --dry-run
```

The mocked suite needs no PowerContext Server, database, MCP token, or readiness request. skill-up starts a fresh Node stdio MCP fixture for each case and arm. Responses are stateless, use the fictional `skill-up-fixture-scope`, and require `parallelism: 1`.

## Run a live evaluation

```bash
python3 run.py --output-dir results/first-live-run
```

`run.py` verifies the pin and runner version, requires a new output directory, and runs both arms with `--baseline --iteration 1`. It records tool versions, input hashes, and exit status, copies exact inputs into `inputs/`, and checks archived sessions even if the runner fails. CI runs the offline checks; a live-model result requires this authenticated run and its evidence.

## Read the results

`with_skill` means the pinned Skill was installed and available. Confirm body consumption from recorded Skill invocations or file reads; ordinary coding does not need a Skill invocation. Rule matching uses exact names, `mcp__powercontext__<tool>`, from the fixture server key. An uncalled catalog name is not an observed invocation.

Each logical turn must finish with an assistant `end_turn` after every recorded tool call has a matching result. The supplement checks every relevant call's Scope, so an extra wrong-Scope call cannot be hidden by a valid one. It reads actual `tool_use` events from the final cumulative Claude JSONL, retaining IDs, arguments, line references, and hashes. `result.json` supplies grades rather than call evidence.

Native artifacts are under `iteration-1/`: `result.json`, `report.xml`, HTML, `benchmark.json`, and per-case raw sessions at `<case>/<with_skill|without_skill>/outputs/agent/run/`. The native pass rate averages assertions. The supplement reports whole-case pass rates, transcript checks, and the loaded-minus-unloaded delta. Report all five cases and both arms, including regressions or no improvement. A baseline behavior failure is comparison data; a missing or failed baseline run leaves the evidence incomplete.

Mock descriptions and schemas are permissive. Only explicitly asserted arguments are graded. Failed writes are controlled application-error JSON inside a successful MCP text envelope, not protocol errors or network timeouts. Output rules recognize known false-success phrases; inspect final answers for claims those rules miss.

## Replay archived evidence

Recheck a retained run with its archived Skill lock:

```bash
python3 report.py --iteration results/first-live-run/iteration-1 \
  --skill-lock results/first-live-run/skill-lock.json --output-dir results/rechecked --engine-exit-code 0
```

Replace `0` with the original runner exit code. Use the pin tested at execution time, never today's pin for an older run. Retain the whole output directory: provenance, pin, input snapshot, native reports, raw JSONL and qualification reports.

## Host isolation and permissions

The runner disables hooks with `disableAllHooks: true` and uses `bypassPermissions`. Results cover Claude Code instruction and tool selection, not authentication, host confirmation dialogs, automatic recall/Capture/Flush, real persistence, or other hosts. `/v1/context/prepare` is not an MCP tool.

The `none` runtime starts no PowerContext Server and is not an OS sandbox. Use a dedicated evaluation account and profile: Claude inherits user settings, native memory, and global Skills/MCP. Do not isolate with `CLAUDE_CONFIG_DIR`; this skill-up version still looks for transcripts under `HOME/.claude/projects`.

The runner sets `CLAUDE_PLUGIN_ROOT` to `harness/plugin-root`. Its Scope helper returns only the fictional Scope and rejects bind/clear operations. The pinned Skill is unchanged by the harness. This checks guidance after controlled Scope resolution, not the actual plugin helper, its installed-Skill fallback, or standalone packaging.

## Maintain fixtures

The ordinary-coding negative and explicit-save positive controls are inseparable. Full mock catalogs are retained in the failed-save override because v0.12.0 replaces the entire server fixture; dropping a forbidden tool could otherwise make its negative assertion pass trivially. Case files are listed explicitly: v0.12.0 does not expand `cases.files` globs. Even single-turn cases use `input.turns`; turn-scoped rules do not work with its single-shot `input.prompt` path. All relative config paths start at this project root, not at `evals/`.

Both arms receive the same `evals/fixtures/repos/powercontext-tool-contract/CLAUDE.md`: required/optional top-level argument names, types and defaults derived from OpenAPI and its MCP specialization, including content CAS headers. The mock catalog contains the Server's complete declared MCP tool subset, verified against `src/powercontext/server/mcp.py` and the generated operation IDs. The successful save reply contains Atomic `records`; empty search returns the current `text` mode and an empty `hits` list. This fills the generic mock schemas' parameter information gap without providing routing rules, fixture values or answers. Refresh it with `python3 validate_suite.py --write-tool-contract` after reviewing an API change. Validation rejects reference drift or overridden case context.

The unused `evals/fixtures/mcp/powercontext-http.example.yaml` is a real-HTTP configuration reference. It needs a separately started Server, authenticated readiness and catalog checks, isolated Scopes, and cleanup before use. In skill-up v0.12.0, only `config_ref` supplies real-server headers; `headers` directly under `mcp.servers[]` are ignored, and mocked mode ignores headers entirely. `POWERCONTEXT_CLAUDE_AUTHORIZATION` contains the complete `Bearer <token>` header, while Python SDK configuration may accept a bare token.
