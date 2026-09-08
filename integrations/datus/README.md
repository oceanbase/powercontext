# PowerContext / Datus experimental bridge

This experimental bridge delivers approved, exact Skill packages and runs a
frozen native Datus workflow in isolated development sessions. SQL remains
Datus's responsibility. The paired evaluator preserves raw evidence and refuses
unknown coverage. Component tests establish no live benchmark score.
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
physical entrypoints **before** registry name deduplication: each expected name
must have exactly `skill-root/name/SKILL.md`, with matching native-parsed metadata.
Root-level, nested, aliased and duplicate entries are rejected without deleting
files. It then checks discovered/GenSQL-visible names and their actual locations
against that exact allowlist; the smoke also compares loaded content with its
expected package entrypoint. The root must remain unchanged during validation. It
does not call `SkillConfig.from_dict`, which appends builtin/adapter directories.
The paired runner injects this manager into the real GenSQL node; invoking the
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

## Isolated native execution

The development entry point is `python -m powercontext_datus.paired`. It uses the
pinned native `GenSQLAgenticNode -> ExecuteSQLNode -> OutputNode` graph.
`workflow.build_graph` injects the exact SkillManager and the original native
`describe_table`, `list_tables`, `execute_sql` and `load_skill` tools. Both arms
use this same tool profile and native model adapter. SQL is executed during the
graph, including any SQL the model requests before the ExecuteSQL node. A repeated
execution is counted again. The evaluator never replays predicted SQL.

This profile uses a shared, independently approved schema/business/example text
file as native `external_knowledge`. Online vector retrieval and embedding are
explicitly disabled. It disables Datus's implicit Bash, filesystem, web, MCP,
sub-agent, memory, plugin and compaction tools. It verifies the effective tool
schemas and rendered system prompt again at the final model-dispatch boundary.
Unsupported tools/child actions or an unobserved driver prevent coverage
certification; this is not a claim that every Datus configuration is supported.

Each question runs in a new Linux bubblewrap namespace with a private tmpfs
home/workspace/session store. Only approved common text, that arm's Skill files,
the locked interpreter/runtime and bridge sources are mounted read-only.
The evaluator, other questions, gold/oracles, receipts and prior traces are never
mounted. Only CPU/memory hardware information is provided from proc/sys; process,
environment and descriptor paths remain absent. The worker performs real denied
read and read-only-write probes before execution. It writes evidence through a
write-only pipe to its parent, with no evidence file path exposed to tools.
Timeout kills/reaps the worker's namespace and preserves partial output.

Runtime/bridge roots must be regular directories. Directory symlinks are checked
before cache exclusions: external or excluded-cache targets fail closed. Internal
aliases such as a venv's `lib64 -> lib` include the link and resolved target in
identity, with target files independently inventoried under the same root.

The launcher supports the installed bubblewrap 0.4.0 and needs unprivileged user
namespaces. It supplies a minimal environment from the parent, including no
ambient product tokens. There is no unsandboxed fallback. The exact mount policy,
rather than the presence of bubblewrap, defines the boundary; see the
[upstream security model](https://github.com/containers/bubblewrap/blob/main/README.md#sandbox-security).

Online runs share host networking for the approved model and database connection.
The pinned httpx clients enforce the configured model origin and retain each HTTP
attempt without headers or credentials. This is not a general network firewall.
The supported tool profile exposes no arbitrary HTTP/shell/code execution.
The database must be an independently controlled immutable read-only snapshot;
local hashes cannot establish remote database immutability. Native MySQL transport
and grants still require deployment-side verification.

## Learning, freeze and paired development

The runner is evaluator-owned. Its input files are not Agent inputs. Paths in the
plan are explicit absolute paths selected by that operator. A template is provided
in `development-plan.example.json`; its placeholders cannot pass admission.

1. Independently author/review the schema/business material and learning samples
   without the formal questions, gold or this development context. Preserve the
   existing exposure ledger. Provide actual source, service authorization and
   data-version receipt files, each referenced by path and SHA-256.
2. Run `sample` with `evidence_kind=independent_learning`, question-only
   `tasks`, the native arm, and an admission document binding the roster/common
   digests and independent author/reviewer. Required receipt references are
   `source_receipt`, `exposure_ledger`, `service_authorization_receipt` and
   `data_version_receipt`. This produces native execution receipts, not approved
   lessons. The evaluator verifies their correctness and provenance.
3. Use the existing `learning.generate_from_examples` SDK bridge to submit only
   independently validated lessons to PowerContext. Obtain independent Skill
   approval and use `deliver_skill` to install exact revisions. The learning
   runner neither generates nor approves a Skill automatically.
4. Prepare the development roster and full-row oracle in the evaluator area.
   Both arms share the same public model configuration, data snapshot, common
   material, current date, timeout and turn budget. Native Skills, when prepared
   from the shared examples, may be supplied to the native arm. The enhanced arm
   additionally requires exact PowerContext delivery receipts.
5. `freeze` constructs both real native graphs without sending a question or
   making a model request. It freezes actual prompt/tool/Skill/common identities,
   the plan/oracle/admission, and every non-bytecode runtime/interpreter/bridge
   file. Subsequent workers redirect bytecode lookups to their fresh private
   layer. `run` refuses changed inputs and checks drift after the pair.
6. `run` executes each question in both isolated arms and writes raw per-case
   evidence. Scoring is performed only after both arms finish. There is no
   usage-feedback write or learning backflow in the runner.

From the repository root (replace paths with evaluator-owned files):

```bash
PYTHONPATH=integrations/datus/src uv run --frozen python -m powercontext_datus.paired sample \
  --runtime-python integrations/datus/runtime/.venv/bin/python \
  --input /evaluation/independent-samples.json --output /evaluation/sample-evidence

PYTHONPATH=integrations/datus/src uv run --frozen python -m powercontext_datus.paired freeze \
  --runtime-python integrations/datus/runtime/.venv/bin/python \
  --input /evaluation/development-plan.json --output /evaluation/frozen-manifest.json

PYTHONPATH=integrations/datus/src uv run --frozen python -m powercontext_datus.paired run \
  --runtime-python integrations/datus/runtime/.venv/bin/python \
  --input /evaluation/frozen-manifest.json --output /evaluation/new-pair
```

The live plan uses `evidence_kind=independent_development`. Live admission requires
`independent_author`, a distinct `independent_reviewer`, exact roster/common/oracle
hashes, `data_mode=immutable_read_only_snapshot`, and these receipt references:

- `source_receipt`, `exposure_ledger`, `native_learning_receipts`
- `powercontext_generation_receipt`, `skill_approval_receipt`
- `data_version_receipt`, `oracle_consistency_receipt`, `service_authorization_receipt`

Each receipt is `{"file": "/evaluation/receipt.json", "sha256": "..."}` and must
exist with matching bytes. `skill_deliveries` maps each arm to its installed
receipts: name/files, and for PowerContext Skills scope/artifact/revision and
archive/tree digest. Enhanced receipts must satisfy the strict SDK artifact address
schema: nonempty scope and printable artifact ID, family `skill`, positive integer
revision (not a string or boolean), and no extra artifact fields.
These bind the independent evaluator's attestations; they do
not authenticate an arbitrary self-authored assertion or prove its truth. The
evaluator must check sample provenance, remote snapshot control and gold/data
consistency before issuing admission. The service authorization receipt covers
Datus inference, PowerContext generation and the generation service's actual
embedding configuration, quotas and timeout.

`secret_refs` contains only `model_api_key` and `db_password` file references.
The launcher requires current-user-owned regular files with no group/other access
and passes their values over stdin to the worker, never argv, a saved manifest,
or the model prompt. Each freeze, paired-run or sample invocation reads its
references exactly once and reuses those values in memory across all tasks and
arms. Each worker receives its own copy. Secret values and their hashes never
enter a manifest or evidence; rotation requires a separate authorized invocation.
It does not read ambient OpenAI/product/login tokens.
Datus's `max_retry=1` means one total model-stream attempt at this pin; the shared
`max_turns` controls the native tool loop, with the process timeout as the hard
wall-clock bound. SDK/HTTP internal attempts are separately retained.

## Evidence, counting and verdicts

`capture` extends the raw NativeTrace lifecycle observer with native tool-call
IDs, parent operation IDs, SQLAlchemy result-fetch evidence and raw PyMySQL
cursor observation. PyMySQL initialization/metadata SQL that bypasses SQLAlchemy
is retained too. An engine marker associates the same actual cursor execution
with its wrapper without counting it twice. Nested SQL and repeated attempts
remain distinct. Native actions must match completed dispatch IDs/names.
Missing, duplicate, conflicting, unfinished or unlinked evidence produces
`trace_missing` and unknown steps. Partial results do not become empty tables.

The PyMySQL command boundary also records native ROLLBACK/COMMIT/BEGIN, SET,
session selection and PING paths that bypass `Connection.query`. Submission and
native reply completion are separate events; failed sends and acknowledgements
are retained. Query/cursor/engine records share one SQL operation ID. Each online
control SQL and connection probe is counted as a distinct attempt, including
pool/recovery activity; they are not hidden initialization or exempt traffic.
Unscoped, bypassed, unsupported or unfinished commands invalidate coverage.
Non-SQL protocol payload bytes are never recorded.

Each native tool call must have exactly one processing action and one later
terminal action, with matching tool names and a terminal status consistent with
the dispatch result. Duplicate, conflicting, missing or reversed lifecycle
evidence invalidates certification; no set-based folding can hide it.

Full result rows are captured from native fetches before DataFrame/CSV conversion
can coerce integers/NULLs or lose empty-result columns. Chunked fetches accumulate
until exhaustion. Decimal values have a tagged lossless JSON representation.
Unsupported cell types fail closed and need a separately frozen conversion policy.

The frozen answer protocol is `json_table_v1`: the entire native GenSQL
`output` is a JSON string containing `columns` and `rows`. The evaluator checks
this whole answer against the actual online result independently from the gold
comparison. Extra prose, unsupported claims, malformed answers and mismatched
rows do not pass. General natural-language answer judging is not implemented.
Grounding obeys the frozen `ordered` row policy, but remains numerically exact:
the gold comparison's tolerance never permits an inaccurate final answer.
Standard JSON decimal and exponent number literals are parsed directly as
`Decimal`, without intermediate binary-float rounding. Tagged database decimals
remain lossless; models need not emit the internal tagged representation.

The evaluator oracle maps each task ID to `expected` and `declared_answer`
tables, with optional `ordered`, `absolute_tolerance` and `relative_tolerance`.
It first checks oracle consistency and then compares complete predicted rows,
preserving multiplicity, NULL and the predeclared column order. It never sorts
columns independently or requires the prediction to equal gold SQL text.

Per-case files retain native actions, every SQL/bind and complete result, raw
GenSQL/final output, model and HTTP attempts, reported usage, failure records and
monotonic timing. Scores retain the fixed roster, including timeouts and missing
runs. Online and total capture latency are distinct. Missing token/cache usage
or pricing stays null. Optional `rates` has input/output/cached prices per million
tokens; the resulting cost is computed from supplied frozen rates, not a billing
receipt. Default-zero SDK cache usage stays unknown.

After a process starts, context/Skill drift invalidates its result without
discarding its raw stdout, parsed records, timeout state or wall time. These
evaluator-only files can contain sensitive outputs and must not be published
without review. The launched case has `process_started=true/not_started=false`;
only subsequent unlaunched cases are marked `not_started`. Pair and sample
runners stop dispatch after a reported control failure. Partial or malformed
stdout is retained even when no complete answer can be scored.

`component_fixture` is an explicit local SQLite/synthetic-HTTP mode. Its reports
always have zero real learning/development runs and `formal_state=not_started`.
Fixture arms always set `accepted=false`; `component_pass` retains their diagnostic
gate result. Fixture arm statistics are component diagnostics only. The native SQLAlchemy
connector is used for fixture execution, with a fixture-only database-name method;
live runs use the pinned MySQL adapter. No component count establishes accuracy.

## Acceptance boundary and exposure ledger

This code does not admit formal runs. Real learning/development validation still
requires authorized model/PowerContext services, independently reviewed samples
and an established common data version/oracle. No such live runs are claimed by
the component tests. The bridge is not registered as a supported
`powercontext setup` target.

The fixed formal input SHA-256 remains
`c4507c1cd1d167a4c2b05d4cfad7f48226b45e216f2826127070b562e89bda84`.
No formal question, answer, gold SQL, database credential or learned domain Skill
is committed. Earlier diagnostics processed reference SQL for EXPLAIN and exposed
aggregate structure to the designer. Retain that exposure ledger; do not claim
absolute non-exposure or replace the benchmark. Formal admission remains a
separate coordinator decision after implementation, real development evidence,
independent review and final freeze. The agreed formal gate remains at least
42/46 correct with 1–2 online logical steps, plus the full-roster average; unknown
steps cannot be excluded or filled with zero.

## Tests

```bash
uv run --frozen python -m pytest tests/datus_adapter -q
DATUS_RUNTIME_PYTHON=integrations/datus/runtime/.venv/bin/python \
  uv run --frozen python -m pytest tests/datus_adapter -q
```

The optional suite drives the real native GenSQL/SDK/tool/SQL/output chain through
a local synthetic HTTP gateway, runs OS denial probes, checks complete rows and
final answers, and freezes/runs two independent case sessions per arm. It covers
SQL retries, identical repeated SQL, multi-operation wrappers, chunked results,
unlinked PyMySQL cursor operations, timeouts, unknown usage and input drift.
The synthetic driver probe does not connect to a live MySQL server. Existing
Server/SDK tests separately exercise proposal/approval/download/install. None are
real model learning or benchmark runs.
