# PowerContext evaluation console

This directory contains a self-progressing SWE-bench Pro evaluation service. A batch can run the paired OFF/ON
experiment or only one Arm to reduce cost and latency. The web process owns the HTTP API and report UI; the worker
owns task execution, retries, resource cleanup, and durable recovery.

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
uv run --project evaluation pytest -c evaluation/pyproject.toml evaluation/tests -m "not live" -q
uv run --project evaluation ruff check evaluation
uv run --project evaluation ruff format --check evaluation
uv run --directory evaluation ty check src
```

Build and test the web UI:

```bash
cd evaluation/web
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

### 2. Install the pinned SWE-bench Pro harness

The runner accepts exactly harness commit `ca10a60a5fcae51e6948ffe1485d4153d421e6c5`. That commit contains the pinned
731-row dataset; the console verifies its schema, order, row count, and SHA-256 before admitting a batch.

```bash
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
cd "$REPOSITORY_ROOT/evaluation/web"
npm ci
npm test -- --run
npm run build
cd "$REPOSITORY_ROOT"
```

### 4. Create and validate the runtime environment

There is one platform runtime configuration file. Copy the example, edit every path that differs from the layout
above, and keep it private. In particular, set `POWERCONTEXT_EVAL_FRONTEND_DIST` to the absolute
`$REPOSITORY_ROOT/evaluation/web/dist` path. Uncomment `POWERCONTEXT_EVAL_CODEX_CONFIG` only if the file was installed
in the previous step.

```bash
install -m 0600 evaluation/deploy/powercontext-eval.env.example \
  "$EVALUATION_ROOT/config/evaluation-console.env"
${EDITOR:-vi} "$EVALUATION_ROOT/config/evaluation-console.env"

set -a
. "$EVALUATION_ROOT/config/evaluation-console.env"
set +a

uv run --project evaluation python -c \
  'import os; from powercontext_eval.web.config import WebConfig; WebConfig.from_environment(os.environ); print("configuration valid")'
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
uv run --project evaluation powercontext-eval web
```

```bash
# Terminal 2
set -a; . "$EVALUATION_ROOT/config/evaluation-console.env"; set +a
uv run --project evaluation powercontext-eval worker
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
uv run --project evaluation powercontext-eval swebench-pro create-batch \
  --console-url http://127.0.0.1:8787 \
  --task-set swebench-pro-stability-v1 \
  --treatment-mode on_only \
  --powercontext-ref latest \
  --idempotency-key "stability-$(date -u +%Y%m%dT%H%M%SZ)"
```

## Configuration files

Only the environment file configures the evaluation platform. The other files either belong to Codex or are
deployment templates:

| File | Required | Purpose |
| --- | --- | --- |
| `evaluation-console.env` | yes | Web, Worker, storage, benchmark, retries, parallelism, and optional integrations |
| `auth.json` | yes | Codex credentials; referenced by path and copied into isolated task homes |
| `config.toml` | no | Custom Codex provider/model configuration |
| `powercontext-eval.env.example` | no | Safe template; never loaded directly in production |
| `*.service.in` | no | Unrendered systemd templates; they are not additional application configuration |

## Configuration reference

Copy `evaluation/deploy/powercontext-eval.env.example` to a protected location, replace every applicable example
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

## Standalone runner

The systemd templates and local commands must use the repository root as their working directory. A packaging
workflow that starts the service outside a Git checkout must provide the exact 40-character source revision in
`POWERCONTEXT_EVAL_BUILD_REVISION`.

The standalone runner defaults to native networking with TokensFlow disabled. Other paths derive from the supplied
root and remain individually overridable:

```bash
uv run --project evaluation powercontext-eval swebench-pro run \
  --root /srv/powercontext-eval \
  --instance-id instance_owner__repository-revision
```

An internal deployment can explicitly add `--proxy-url http://127.0.0.1:8081 --tokensflow
--tokensflow-egress-network bridge`; TokensFlow binary and profile paths derive from the root unless overridden.

## systemd templates

`evaluation/deploy/*.service.in` are templates, not installable units. Render all placeholders into a staging
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
