# SWE-bench Pro coding evaluation

This suite measures coding-task completion with the pinned SWE-bench Pro public v2 dataset. It validates the gold
patch before running PowerContext OFF/ON treatments, grades each generated patch with the official harness, and
writes task reports and context traces. A batch can also select only the OFF or ON treatment.

The suite owns its dataset adapter and catalog, prediction format, gold validation, runner, scoring integration,
reports, CLI commands, and tests. Its execution tools, artifact storage, Web console, worker, and deployment
templates also live here. The suite provides the `swebench-pro` command; the shared Python environment lives in
[`evaluation/`](../../README.md).

```text
swebench_pro/
├── src/powercontext_eval_swebench_pro/ # Suite, execution, storage, Web API, and worker
├── tests/                            # Suite, execution, API, and worker behavior
├── web/                              # Console frontend and browser tests
├── deploy/                           # Environment and service templates
└── docs/console.md                    # Console setup and operations
```

See [console and worker setup](docs/console.md) for deployment, configuration, batch scheduling, and recovery.

## Pinned inputs

- Harness commit: `ca10a60a5fcae51e6948ffe1485d4153d421e6c5`
- Dataset revision: `7ab5114912baf22bb098818e604c02fe7ad2c11f`
- Public dataset: 731 tasks, SHA-256 `b5b2462bfbf5aeb2cb7ba7d215778a1768b85f9d7ad7f748546c7f80a0ad1510`
- Task sets: `swebench-pro-public-v2` (731 tasks) and `swebench-pro-stability-v1` (24 pinned regression tasks)

The catalog validates the dataset schema, row count, order, and hash before admitting a batch. Gold validation
overrides retain the original task identity and an audit record in the report.

## Prepare harness

Choose the same absolute writable runtime root used by the console configuration. The commands below use
`/srv/powercontext-eval`; change this value consistently if your deployment uses a different location.

```bash
export EVALUATION_ROOT=/srv/powercontext-eval
install -d -m 0700 "$EVALUATION_ROOT/cache" "$EVALUATION_ROOT/venvs"

git clone https://github.com/scaleapi/SWE-bench_Pro-os.git \
  "$EVALUATION_ROOT/cache/swebench-pro.git"
git -C "$EVALUATION_ROOT/cache/swebench-pro.git" checkout --detach \
  ca10a60a5fcae51e6948ffe1485d4153d421e6c5
test "$(git -C "$EVALUATION_ROOT/cache/swebench-pro.git" rev-parse HEAD)" = \
  ca10a60a5fcae51e6948ffe1485d4153d421e6c5
test "$(sha256sum "$EVALUATION_ROOT/cache/swebench-pro.git/helper_code/sweap_eval_full_v2.jsonl" | cut -d' ' -f1)" = \
  b5b2462bfbf5aeb2cb7ba7d215778a1768b85f9d7ad7f748546c7f80a0ad1510

uv venv --python 3.11 "$EVALUATION_ROOT/venvs/swebench-pro-ca10a60"
uv pip install \
  --python "$EVALUATION_ROOT/venvs/swebench-pro-ca10a60/bin/python" \
  -r "$EVALUATION_ROOT/cache/swebench-pro.git/requirements.txt"
```

Both revision and dataset-hash checks must succeed before running evaluations. The console's default harness
Python path uses this pinned virtual environment; the standalone command below selects the same interpreter
explicitly with `--harness-python`.

## Run

Run these commands from the repository root. The shared environment is configured by `evaluation/pyproject.toml`:

```bash
uv sync --project evaluation --frozen
uv run --project evaluation swebench-pro --help
```

A real run requires Linux, Docker, the pinned harness and task images, Codex credentials, and a PowerContext Git
source. Prepare the harness above, then follow the [console setup instructions](docs/console.md#quick-start)
to configure the source, tools, credentials, and Web and Worker services.

Run one task with the standalone runner:

```bash
uv run --project evaluation swebench-pro run \
  --root "$EVALUATION_ROOT" \
  --harness-python "$EVALUATION_ROOT/venvs/swebench-pro-ca10a60/bin/python" \
  --instance-id instance_owner__repository-revision
```

The instance ID must come from the pinned catalog. Paths derive from `--root` and can be overridden individually.
Native networking is the default; TokensFlow telemetry and proxy integration are optional.

To test against OceanBase, pass `--database-config /absolute/private/database.json`. This private JSON object must
contain exactly `off` and `on`, each using the `settings.database` shape (`kind`, `url`, and optional profile
settings). Create a separate evaluation database for each arm:

```json
{
  "off": {
    "kind": "oceanbase",
    "url": "mysql+aoceanbase://eval:REPLACE_WITH_PASSWORD@db.example:2881/swe_eval_off?charset=utf8mb4"
  },
  "on": {
    "kind": "oceanbase",
    "url": "mysql+aoceanbase://eval:REPLACE_WITH_PASSWORD@db.example:2881/swe_eval_on?charset=utf8mb4"
  }
}
```

OFF disables inference while ON enables its configured processing capabilities. PowerContext records that
processing configuration in a database-wide manifest, so separate Scopes in the same OceanBase database do not
isolate these configurations. The runner rejects OFF/ON configurations that point to the same OceanBase host,
port, and database, even when they use different credentials.

When deriving the configurations from `.env`, serialize each actual URL, not the masked `SecretStr`
representation, and replace its database name with the corresponding newly created evaluation database. Keep the
file private (mode `0600`) and delete it after the run. The runner does not create or drop external databases.
Never point this configuration at a business database.

Each arm uses its own explicit database configuration and registers its own Scope. Other `container_env`
settings continue to apply only to ON. Without an explicit database configuration, each arm uses its local SQLite default.
Treatment evidence queries the configured backend and records its kind and a target fingerprint without the
connection URL or password. Containers install the locked Server/CLI runtime dependencies with `--no-dev`.
For OceanBase, dependency preparation also omits the unused `sqlite-vec` package while keeping the dependency
lock unchanged. This requires a PowerContext source revision that imports the SQLite
vector extension only when SQLite vector search is enabled and includes `tzdata` in its builtin runtime dependencies
for images without system timezone data. OceanBase credentials appear only in each arm's
private `runtime/container.env`; remove these files with the disposable runtime when cleaning up a run.

After the Web and Worker services are healthy, create a bounded batch through the console API:

```bash
uv run --project evaluation swebench-pro create-batch \
  --console-url http://127.0.0.1:8787 \
  --task-set swebench-pro-stability-v1 \
  --treatment-mode on_only \
  --powercontext-ref latest \
  --idempotency-key "stability-$(date -u +%Y%m%dT%H%M%SZ)"
```

Choose `off_on`, `on_only`, or `off_only` for the treatment mode. Start with the stability task set before a full
public-v2 run. The console retains completed reports and supports compatible historical baseline comparisons.

## Validate

```bash
uv run --project evaluation pytest -c evaluation/pyproject.toml evaluation/coding/swebench_pro/tests -m "not live" -q
```

These local tests verify the pinned contracts, runner phases, report semantics, and command wiring using controlled
inputs and processes. They do not execute a real model or establish the outcome of a full SWE-bench Pro run.
