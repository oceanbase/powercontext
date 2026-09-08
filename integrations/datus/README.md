# PowerContext / Datus experimental bridge

This first implementation delivers approved, exact Skill packages to a native
Datus Skill directory. SQL remains Datus's responsibility. It also provides
evaluation primitives and opt-in raw native observation. It does **not** yet
provide a certified, isolated paired QA runner or establish a benchmark score.
It is not registered as a supported `powercontext setup` target.

## Reproduce the two environments

From the repository root:

```bash
uv sync --frozen --python 3.12.11
uv sync --project integrations/datus/runtime --frozen --python 3.12.11
uv run --project integrations/datus/runtime --frozen datus-agent --version
uv run --project integrations/datus/runtime --frozen datus-agent benchmark --help
```

The tested interpreter is CPython 3.12.11. `runtime/uv.lock` pins Datus 0.4.0 at
`Datus-ai/Datus-agent@b38e71c30cbb32566d2f659570b6f1229c7a1415`, MySQL adapter
0.1.8, and transitive dependencies. The archive SHA-256 is
`aa81a3feba26e72df6548c01bce1a023fe5f2178ee700f281c2ae0844c5e413c`;
the MySQL wheel SHA-256 is
`109f8d9a028c1f38f5ac5205a50a65f7c74a265e3cfcdc370d35cb3e10a78162`.
The source archive is the same commit as the agreed Git source; it avoids an
upstream recursive submodule checkout failure (Spider2 missing its URL).
The lock records the public Tsinghua PyPI mirror used in this environment.

Do not install PowerContext and Datus into one environment: the installed Datus
distribution requires httpx 0.27.2, whereas PowerContext's client requires
httpx >=0.28. The actual resolved OpenAI Agents dependency is 0.13.4; package
build metadata, not an older dependency declaration in upstream pyproject.toml,
determines this lock. No upstream files are patched.

The bridge CLI runs in the PowerContext environment, using this checkout's
source path. `native` and `observer` deliberately avoid importing the
PowerContext SDK so they can run in the separate Datus environment.

## Offline learning and exact delivery

`learning.generate_from_examples` captures independently reviewed example
question/SQL/lesson plus result, native receipt and source-manifest digests via
the existing Source API. It requests a **pending** Skill Candidate. It never
approves that candidate. The existing PowerContext review/approval operation
must produce an exact Skill Artifact reference before delivery.

Evidence digests provide lineage, not proof that an example is independent or
correct. The evaluator must verify its original native execution receipt and
provenance. Do not pass the formal QA file or derived gold examples to this API.

Provision `POWERCONTEXT_BASE_URL` and, where required, `POWERCONTEXT_TOKEN` through
the task's secret mechanism. Do not pass secrets as command-line arguments.
Use a pre-created, owned directory and an approved scope/artifact/revision:

```bash
PYTHONPATH=integrations/datus/src uv run --frozen python -m powercontext_datus.cli deliver \
  --scope SCOPE_ID --artifact ARTIFACT_ID --revision 1 --skill-root ./owned-skills
```

The bridge downloads the exact revision through the authenticated SDK, verifies
the canonical package/archive and full file inventory, and refuses existing
destinations. It does not resolve "latest", overwrite, merge, auto-approve,
register a remote receiver, or extend the existing receiver's target enum.
Only non-executable `.md/.txt/.sql/.json/.yaml/.yml` files are accepted.
An instruction can still request unsafe behavior: package validation is **not**
tool permission enforcement. The runtime must separately restrict effective tools.

`publish_completed_usage` gates SDK usage writes on an evaluator-owned paired
completion decision. The Agent must not own that decision or have access to the
feedback queue. It is a transport helper, not an isolated queue implementation.

## Native component smoke

For an installed, approved package named `SKILL_NAME`:

```bash
PYTHONPATH=integrations/datus/src uv run --project integrations/datus/runtime --frozen \
  python -m powercontext_datus.native --skill-root ./owned-skills --expected-skill SKILL_NAME
```

`skill_manager_for` creates the actual native SkillManager with explicit
directories, config mutation disabled and auto-sync off. It checks **all**
discovered and GenSQL-visible names against the supplied exact allowlist. It
does not call `SkillConfig.from_dict`, which appends builtin/adapter directories.
The caller must inject this manager into the real GenSQL node; invoking the
standard Datus CLI alone does not install this manager or the observer.

Optionally add `--db` to execute only `SELECT 1 AS adapter_smoke` using native
`datus_mysql.MySQLConnector.execute_query`. Provision `DATUS_DB_HOST`,
`DATUS_DB_PORT`, `DATUS_DB_USER`, `DATUS_DB_PASSWORD`, `DATUS_DB_NAME` through the
authorized task environment. Use a read-only account and a foreground process
timeout. The current native adapter does not enforce its connection timeout
field or configure TLS here; do not assume the connection is encrypted.

The JSON is labeled `native_component_smoke`, with `native_agent_qa_runs: 0`.
A successful SELECT 1 or manual Skill load is neither a learning example nor a
model-auth/Agent success. Errors expose their type, not connector URLs or passwords.

## Evaluation primitives and current limits

| Module | Implemented boundary | Still owned by the paired runner/evaluator |
| --- | --- | --- |
| `freeze` | Exact regular-file content/mode snapshots; additions, removals, links and drift rejected | OS/process isolation, database version, complete effective context inventory |
| `observer` | Raw native action snapshots before/after mutation, before rollback/clear; SQLAlchemy cursor lifecycle with distinct IDs; exclusive mode-0600 sidecar | Real graph installation, complete result/final-answer capture, action/driver correlation, model costs, child-process coverage |
| `trace` | Count unique logical operation attempts; retain failures/retries, deduplicate repeated lifecycle records; reject mixed/conflicting/incomplete traces | Prove event coverage and derive honest operation IDs from native raw evidence |
| `oracle` | Full-row multiset or ordered comparison, duplicate multiplicity, NULL/type/finite numeric precision handling | Frozen column/unit policy, gold/answer/data consistency and final-answer grounding |
| `report` | Fixed roster denominator, joint correctness + 1–2 steps, unknown steps remain unknown, conflict/drift/absence invalidate results | Trusted case verdict production; authenticity of native execution and stable data |

`with NativeTrace(path, run_id=..., task_id=..., attempt_id=...):` must wrap the
entire online interval, from question injection through final answer submission.
It temporarily observes native ActionHistoryManager methods and SQLAlchemy Engine
events in **one dedicated process**. It restores those hooks on exit. Do not use
it in a multi-tenant server. Raw sidecars can contain sensitive SQL/parameters and
tool results; store them outside Agent-readable paths and do not publish them
without a privacy review. A successful capture-finished event is not proof of a
correct final answer. Cursor success does not prove complete result fetching.

Neither the generic counter nor the observer currently performs automatic,
coverage-certified action/DB-span reconciliation. Never label raw span counts,
Datus's success-only counter, workflow node count, `max_steps`, or one outer RPC
as the agreed online step metric. Multiple SQL calls inside a wrapper are multiple
steps; the same operation's wrapper and DB span are one step. Failed attempts,
retries, retrieval/schema/Skill calls and child operations count. Ambiguity means
`trace_missing`, not zero. Report model calls/tokens, latency and cost separately.

The table oracle requires a predeclared column mapping/order; it does not compare
SQL text or independent sorted columns. Unsupported cell types fail closed and
require a frozen evaluator conversion policy. Numeric tolerance defaults to zero.
Final-answer grounding and oracle consistency are independent mandatory checks.

## Acceptance sequence (not executed by the component CLI)

1. Authorize Datus inference, PowerContext generation and any enabled embedding
   services separately: exact provider/model IDs, protected secret references,
   budgets and timeouts. Verify a real model + native SQL smoke first.
2. Import schema/approved shared business context without formal QA/gold. Prepare
   learning and development examples in a fresh authoring context without the
   formal questions or this design conversation. Independently verify provenance,
   real successful executions and absence of near-copy gold examples.
3. Give both native/enhanced arms the same independent material and budget. Learn
   only from those examples; approve and install exact enhanced Skill revisions.
4. Freeze model parameters, prompt/workflow/tools, effective Skill inventory and
   bytes, schema/KB/indices, permissions, code/locks and data-version evidence.
   Clone a fresh session/writable layer per task. Disable automatic learning,
   cross-task memory and usage feedback. File hashes alone are not a sandbox.
5. Run the **independent development** paired arms and record every failure.
   Only after choosing the final version, run the fixed 46-question formal pair
   from question-only input. Gold/answers/other-task traces must be inaccessible
   to the Agent. Do not return formal diagnostics to authors before both arms finish.
6. Independently validate complete online SQL results and grounded final answers
   against the frozen oracle/data. Retain all 46 tasks, including oracle conflicts,
   cancellations, missing traces and failures. At least **42/46 must both be
   correct and use 1–2 online logical steps**. Also report full accuracy and full
   mean steps (null if any unknown), coverage, model cost and failure categories.

The fixed input SHA-256 recorded by the design is
`c4507c1cd1d167a4c2b05d4cfad7f48226b45e216f2826127070b562e89bda84`.
No question, answer, gold SQL, DB password or learned domain Skill is committed.
Previous diagnostics processed reference SQL for EXPLAIN and exposed aggregate
table/column information to the designer; preserve that exposure ledger. Do not
claim absolute non-exposure or replace the dataset to erase the ledger. Tuning
against later formal diagnostics makes subsequent runs known-set regression.

Outstanding engineering before formal admission: wire the selected real GenSQL
graph (`gen_sql -> execute_sql -> output`) with the manager/observer, establish
per-case OS isolation and effective tool checks, reconcile raw operations,
capture full results/answers/model costs, and implement the paired oracle runner.
These are not claimed complete or hidden behind missing model authorization.

## Tests

```bash
uv run --frozen python -m pytest tests/datus_adapter -q
DATUS_RUNTIME_PYTHON=integrations/datus/runtime/.venv/bin/python \
  uv run --frozen python -m pytest tests/datus_adapter -q
```

The first command skips the optional native-library subprocess probes. The
second runs them in the pinned environment. Local Server/SDK tests exercise real
SQLite-backed package proposal/approval/download/install. Learning tests use
transport mocks; native observer tests use synthetic actions and real SQLite SQL.
None count as Datus learning/development/formal QA executions. The review packet
must report actual native two-arm counts, even when both are zero.
