# SWE-bench Pro console and worker

The [SWE-bench Pro suite](../README.md) owns this Web console, worker, process execution, and artifact storage.
Its Python package, `powercontext_eval_swebench_pro`, is built with the other suite packages by
[`evaluation/pyproject.toml`](../../../pyproject.toml). The Web process owns the HTTP API and report UI; the worker
owns execution, retries, resource cleanup, and durable recovery. The console schedules SWE-bench Pro batches with
paired OFF/ON or single-arm runs.

Each arm records completed logical MCP requests from the Server's Prometheus metrics, including initialization,
tool discovery, tool calls, and failed requests. An ON arm requires both captured prompt Sources and a positive MCP
request count; an OFF arm requires both counts to be zero. Metrics read failures remain retryable; malformed metrics
are a terminal treatment validation failure. Neither records zero as a substitute for missing evidence. User-supplied
`POWERCONTEXT_SERVER_METRICS*` settings are reserved so evaluation containers retain metrics collection. The count
proves MCP contact, not necessarily a tool call. A restart resets the Server's counter; historical reports with
incorrect zero counts must be rerun to obtain valid evidence.

The service is intentionally deployment-neutral. Host names, operators, filesystem roots, optional proxy endpoints, Docker
network ranges, credentials, and service locations are supplied by the operator. The repository does not contain a
production environment file or a ready-to-install host-specific systemd unit.

## Runtime requirements

- Linux, Git, Python 3.11 or newer, `uv`, and Node.js/npm
- Docker with permission to pull images, create isolated bridge networks, and run evaluation containers
- Codex CLI and a valid Codex `auth.json`
- [`regctl`](https://github.com/regclient/regclient) for importing task images that are not already present
- enough disk space and inodes for the selected parallelism

TokensFlow telemetry and the credential-free loopback proxy are optional integrations. Both are disabled by default;
the normal open-source path uses native container egress and starts neither a proxy relay nor a TokensFlow
daemon/finalizer.

Install and validate from the repository root:

```bash
uv sync --project evaluation --frozen
uv run --project evaluation pytest -c evaluation/pyproject.toml evaluation/coding/swebench_pro/tests -m "not live" -q
uv run --project evaluation ruff check evaluation/coding/swebench_pro evaluation/memory/longmemeval_v2
uv run --project evaluation ruff format --check evaluation/coding/swebench_pro evaluation/memory/longmemeval_v2
uv run --directory evaluation ty check
```

Build and test the web UI:

```bash
cd evaluation/coding/swebench_pro/web
npm ci
npm test -- --run
npm run build
```

## Quick start

The commands below prepare a single-host development deployment. They intentionally bind the unauthenticated console
to `127.0.0.1`. Put an authenticating reverse proxy in front of the console before allowing access from another
machine; do not expose the HTTP service directly to an untrusted network.

### 1. Prepare the evaluation root

Choose an absolute writable directory and create the layout expected by the default configuration. The Web and
Worker processes must be able to read this repository and the protected configuration; the Worker also needs Docker
access and write access to the evaluation root.

```bash
export REPOSITORY_ROOT="$(pwd -P)"
export EVALUATION_ROOT=/srv/powercontext-eval

install -d -m 0700 \
  "$EVALUATION_ROOT/bin" \
  "$EVALUATION_ROOT/cache" \
  "$EVALUATION_ROOT/codex-home" \
  "$EVALUATION_ROOT/config" \
  "$EVALUATION_ROOT/source" \
  "$EVALUATION_ROOT/venvs"
```

### 2. Prepare the task harness

Follow the [SWE-bench Pro suite setup](../README.md#prepare-harness) to install its pinned harness, dataset, and
Python environment under `$EVALUATION_ROOT`. The Web worker validates that task input before admitting a batch.

### 3. Prepare source, tools, credentials, and frontend

The source is a bare mirror so every submitted branch, tag, or commit resolves to immutable Git data rather than a
mutable developer working tree. Refresh or replace this mirror deliberately when evaluating newer PowerContext
commits.

```bash
git clone --mirror "$REPOSITORY_ROOT" "$EVALUATION_ROOT/source/powercontext.git"

install -m 0755 "$(command -v codex)" "$EVALUATION_ROOT/bin/codex"
install -m 0755 "$(command -v uv)" "$EVALUATION_ROOT/bin/uv"
install -m 0755 "$(command -v regctl)" "$EVALUATION_ROOT/bin/regctl"

install -m 0600 "${CODEX_HOME:-$HOME/.codex}/auth.json" \
  "$EVALUATION_ROOT/codex-home/auth.json"
```

If the Codex account uses a custom provider, also copy its configuration without printing it:

```bash
install -m 0600 "${CODEX_HOME:-$HOME/.codex}/config.toml" \
  "$EVALUATION_ROOT/codex-home/config.toml"
```

Build the frontend from the repository checkout that will run the services:

```bash
cd "$REPOSITORY_ROOT/evaluation/coding/swebench_pro/web"
npm ci
npm test -- --run
npm run build
cd "$REPOSITORY_ROOT"
```

### 4. Create and validate the runtime environment

There is one SWE-bench Pro console runtime configuration file. Copy the example, edit every path that differs from the layout
above, and keep it private. In particular, set `POWERCONTEXT_EVAL_FRONTEND_DIST` to the absolute
`$REPOSITORY_ROOT/evaluation/coding/swebench_pro/web/dist` path. Uncomment `POWERCONTEXT_EVAL_CODEX_CONFIG` only if the file was installed
in the previous step.

```bash
install -m 0600 evaluation/coding/swebench_pro/deploy/powercontext-eval.env.example \
  "$EVALUATION_ROOT/config/evaluation-console.env"
${EDITOR:-vi} "$EVALUATION_ROOT/config/evaluation-console.env"

set -a
. "$EVALUATION_ROOT/config/evaluation-console.env"
set +a

uv run --project evaluation python -c \
  'import os; from powercontext_eval_swebench_pro.web.config import WebConfig; WebConfig.from_environment(os.environ); print("configuration valid")'
docker info >/dev/null
```

Use `POWERCONTEXT_EVAL_USAGE_MODE=api_key` for API-key credentials. That mode skips subscription-usage probing and
treats quota admission as sufficient; filesystem and Docker admission remain active. Keep `subscription` for a
Codex subscription whose CLI exposes the supported usage probe.

### 5. Start Web and Worker

From the repository root, load the same protected environment in two terminals:

```bash
# Terminal 1
set -a; . "$EVALUATION_ROOT/config/evaluation-console.env"; set +a
uv run --project evaluation swebench-pro web
```

```bash
# Terminal 2
set -a; . "$EVALUATION_ROOT/config/evaluation-console.env"; set +a
uv run --project evaluation swebench-pro worker
```

Verify the control plane before submitting work:

```bash
curl --fail --silent http://127.0.0.1:8787/api/health
```

Open `http://127.0.0.1:8787/`, choose `swebench-pro-stability-v1` and an `OFF + ON`, `ON only`, or `OFF only` run.
This 24-task set is the deployment regression suite; use it before the 731-task `swebench-pro-public-v2` set. A
completed aggregate report can freeze any executed Arm as an immutable baseline. Reports may select multiple
compatible baselines for historical comparison without rerunning evaluation, while the baseline library lists the
newest saved baselines first. The same bounded batch can be created from the CLI after Web and Worker are healthy:

```bash
uv run --project evaluation swebench-pro create-batch \
  --console-url http://127.0.0.1:8787 \
  --task-set swebench-pro-stability-v1 \
  --treatment-mode on_only \
  --powercontext-ref latest \
  --idempotency-key "stability-$(date -u +%Y%m%dT%H%M%SZ)"
```

## Configuration files

Only the environment file configures the SWE-bench Pro console and worker. The other files either belong to Codex or are
deployment templates:

| File | Required | Purpose |
| --- | --- | --- |
| `evaluation-console.env` | yes | Web, Worker, storage, benchmark, retries, parallelism, and optional integrations |
| `auth.json` | yes | Codex credentials; referenced by path and copied into isolated task homes |
| `config.toml` | no | Custom Codex provider/model configuration |
| `powercontext-eval.env.example` | no | Safe template; never loaded directly in production |
| `*.service.in` | no | Unrendered systemd templates; they are not additional application configuration |

## Configuration reference

Copy `evaluation/coding/swebench_pro/deploy/powercontext-eval.env.example` to a protected location, replace every applicable example
value, and set mode `0600`. Only `POWERCONTEXT_EVAL_ROOT` is required by the configuration loader; the runtime tools,
dataset, credentials, and source checkout must exist at their derived or explicitly configured paths before work is
claimed.

Derived paths are rooted below `POWERCONTEXT_EVAL_ROOT` unless explicitly overridden. The settings fall into these
groups:

| Group | Variables |
| --- | --- |
| Service and storage | `ROOT`, `HOST`, `PORT`, `DATABASE_PATH`, `RUN_ROOT`, `FRONTEND_DIST` |
| Benchmark inputs | `POWERCONTEXT_SOURCE`, `HARNESS_ROOT`, `HARNESS_PYTHON`, `DATASET_PATH` |
| Executables and credentials | `CODEX_BINARY`, `CODEX_MODELS`, `CODEX_CONFIG`, `UV_BINARY`, `REGISTRY_BINARY`, `AUTH_JSON` |
| Scheduling and recovery | `TASK_PARALLELISM`, `MAX_ATTEMPTS`, `LEASE_SECONDS`, `POLL_SECONDS`, `WORKSPACE_RECLAIM_INTERVAL_SECONDS` |
| Resource admission | `FILESYSTEM_MIN_FREE_BYTES`, `FILESYSTEM_MIN_FREE_INODES`, `DOCKER_NETWORK_POOL` |
| Usage admission | `USAGE_MODE`, `USAGE_PAUSE_PERCENT`, `USAGE_PROBE_SECONDS`, `USAGE_PROBE_TIMEOUT_SECONDS`, `USAGE_SNAPSHOT_MAX_AGE_SECONDS` |
| Optional integrations | `TOKENSFLOW_ENABLED`, TokenFlow settings, `PROXY_URL`, `EXTRA_NO_PROXY_HOSTS` |

Every name in the table has the `POWERCONTEXT_EVAL_` prefix. Important behavior-changing settings are:

- `POWERCONTEXT_EVAL_TASK_PARALLELISM` controls concurrent OFF/ON task pairs and defaults to `1`.
- `POWERCONTEXT_EVAL_MAX_ATTEMPTS` bounds durable per-task retries and defaults to `5`.
- `POWERCONTEXT_EVAL_USAGE_MODE=api_key` disables subscription quota probing; API-key mode is treated as having
  sufficient quota while still enforcing resource admission.
- `POWERCONTEXT_EVAL_TOKENSFLOW_ENABLED=true` explicitly enables internal TokensFlow telemetry and then requires its
  binary, user home, and egress network settings. When false or absent, no TokensFlow profile, daemon, finalizer,
  mount, or retained audit artifact is created.
- `POWERCONTEXT_EVAL_PROXY_URL` explicitly enables the loopback proxy relay. When absent, task networks use native
  egress and proxy variables are cleared from managed task processes.
- `POWERCONTEXT_EVAL_EXTRA_NO_PROXY_HOSTS` is a comma-separated, validated list appended to loopback-only
  `NO_PROXY`. It is valid only with the evaluation proxy and is recorded in each retained report.
- `POWERCONTEXT_EVAL_DOCKER_NETWORK_POOL` selects the private IPv4 pool used for isolated /28 task networks. It must
  provide at least 32 subnets and is recorded in each retained report.

Credential files are referenced by path and copied into isolated task homes. Never place credential values in the
environment example, command line, logs, reports, or repository.

## systemd templates

`evaluation/coding/swebench_pro/deploy/*.service.in` are templates, not installable units. Render all placeholders into a staging
directory, inspect the result, and run `systemd-analyze verify` before installation. Required placeholders are:

- `@EVALUATION_ROOT@`: absolute writable evaluation root
- `@REPOSITORY_ROOT@`: absolute deployment checkout
- `@UV_BINARY@`: absolute `uv` executable
- `@EVALUATION_USER@` and `@EVALUATION_GROUP@`: unprivileged web identity
- `@EVALUATION_WORKER_USER@` and `@EVALUATION_WORKER_GROUP@`: worker identity with the required Docker access

The web template is sandboxed to the evaluation root. The worker template intentionally does not claim a generic
sandbox because Docker access and host cleanup policy vary by deployment. Do not grant the web role Docker access.

## Control and recovery model

Only operator pause or cancel changes durable control intent. Task failures do not pause healthy peers. Retriable
task failures enter durable backoff and another free worker slot may claim the next eligible task. The default five
attempt budget uses 30, 120, 300, and 600 second delays. An exhausted or non-retriable task becomes a retained
failure while the rest of the batch continues.

The worker performs startup-only orphan recovery under a process lock. A normal claim never steals another slot's
lease. Loss of attempt ownership cancels child processes, after which the lifecycle cleaner removes exact
attempt-owned containers, networks, and scratch workspaces. Retained `/runs` reports and private incident evidence
remain available for audit. When TokensFlow is explicitly enabled, finalizer-owned containers are excluded until
their durable finalization job is terminal.

Admission pressure is transient: disk, inode, Docker, or usage probe pressure prevents new claims without changing
batch intent. Workspace reclamation runs continuously and removes only terminal attempts after report publication;
it never deletes running, queued, retryable, or finalizer-owned state.

## Operational acceptance

Before resuming a real batch:

1. verify the exact source revision and a clean checkout;
2. validate the protected environment file without displaying it;
3. run backend, lint, format, type, frontend test, and frontend build gates;
4. verify rendered systemd units and confirm web/worker health;
5. run a small OFF/ON batch and inspect Gold, OFF, ON, official evaluator, retry, cleanup, and report evidence;
6. confirm `active_task_pairs` never exceeds configured parallelism;
7. confirm unrelated host services are neither dependencies nor restart targets;
8. perform a secret scan over retained artifacts and logs;
9. document the exact rollback revision before increasing parallelism.

Rollback is a source checkout and service restart operation. Do not delete the database, `/runs`, incident evidence,
or task workspaces needed by queued and retryable attempts. Do not use global Docker prune as an evaluation cleanup
mechanism.
