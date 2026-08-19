# Evaluation console operations

This console is for m0 only. It is an internal, unauthenticated service bound to m0's internal address
`100.88.99.11`; the host firewall and private network are the authentication boundary. Do not expose port 8787 to
the public Internet. The units do not manage or depend on the machine's existing application, database, cache, or
local proxy services.

## Fixed batch contract

One report is one immutable `swebench-pro-public-v2` batch:

- exactly 731 public SWE-bench Pro tasks;
- one PowerContext revision resolved to one full commit SHA for the complete batch;
- one `gpt-5.6-sol` / `medium` Codex configuration;
- one OFF and one ON execution for every task;
- between one and thirty physical OFF/ON task pairs running concurrently, with every other child retained in the
  durable queue.

The pinned dataset SHA-256 is
`b5b2462bfbf5aeb2cb7ba7d215778a1768b85f9d7ad7f748546c7f80a0ad1510`. The catalog refuses to start if the
file hash, row count, row schema, task IDs, or source order differs.

The production 731-task batch is long-running and consumes the account's subscription allowance. Do not start it
during deployment or smoke testing.
Before a real run, record and show the user:

```text
current subscription usage = latest sanitized Codex used percent and reset time
pause threshold = the selected used-percent boundary
remaining estimate = observed paired Token and duration samples, or unavailable
```

If representative measurements do not yet exist, state that the estimate is unavailable; do not invent one. Codex
is subscription-controlled here, so the console does not display currency or pretend that it has an account balance.
A real batch requires explicit final approval after the non-mutating preview shows these facts.

## Configurable task-pair parallelism

`POWERCONTEXT_EVAL_TASK_PARALLELISM` accepts an integer from `1` through `30` and defaults to `1`. Setting it to `30`
runs up to thirty concurrent task pairs. Parallelism is
across independent SWE-bench tasks only: each task remains one
ordered OFF-then-ON comparison pair, followed by official evaluation and reporting.

Each concurrent task has its own workspace, runtime, Codex home, PowerContext home, Docker network, and scope. Leases
are per immutable attempt, while one supervisor owns the configured slots. Codex subscription usage and rate limits
remain account-wide rather than per slot.

Pause, cancel, an account-usage threshold, usage unavailability, and an infrastructure failure stop new claims.
Active task pairs finish their current OFF/ON boundary; they are not killed midway. An infrastructure failure
atomically pauses a runnable batch and records a sanitized control event, while already-active peers finish. Resume
is always explicit after the failure is resolved, usage is freshly observed below the threshold, and service health
is verified.

`/api/health` reports `active_task_pairs`, `task_parallelism`, `resource_admission_open`, and allowlisted filesystem
byte/inode capacity in addition to queue counts. Validate a capacity increase at a clean task boundary:

1. keep the batch paused with zero running tasks, set `POWERCONTEXT_EVAL_TASK_PARALLELISM` to the intended capacity,
   and restart only the
   evaluation Worker;
2. verify Web/Worker and existing services are healthy, account usage is freshly below the threshold, and health
   reports `active_task_pairs: 0`, the selected `task_parallelism`, and `resource_admission_open: true`;
3. explicitly resume the batch and verify distinct running attempts never exceed the selected capacity;
4. if a bounded validation wave is required, request pause and let every active task pair finish naturally;
5. verify isolated workspaces, runtimes, Codex homes, PowerContext homes, Docker networks and scopes, official
   evaluation, cleanup, retained evidence, service health, and a fresh below-threshold usage observation;
6. only after every check passes, explicitly resume sustained processing at the selected capacity.

If the wave has an infrastructure failure, keep the batch paused, preserve every failed attempt and its evidence,
set parallelism back to `1`, restart only the Worker, diagnose and fix the failure, and retry only the infrastructure
failure items. Do not resume other queued work until those retries succeed and all safety checks pass.

## Subscription usage and batch controls

The launcher first creates a preview. Preview reads the current sanitized account usage and fixed 731-task contract,
but creates no batch, queue row, attempt, or model call. Only **Confirm and start** persists work.

The deployment defaults are:

| Setting | Default | Meaning |
| --- | ---: | --- |
| `POWERCONTEXT_EVAL_USAGE_PAUSE_PERCENT` | `80` | Stop claiming new tasks at or above this used percentage |
| `POWERCONTEXT_EVAL_USAGE_PROBE_SECONDS` | `60` | Normal interval between account-usage observations |
| `POWERCONTEXT_EVAL_USAGE_PROBE_TIMEOUT_SECONDS` | `15` | Maximum duration of one bounded usage probe |
| `POWERCONTEXT_EVAL_USAGE_SNAPSHOT_MAX_AGE_SECONDS` | `120` | Oldest observation accepted by preview, start, resume, and retry |
| `POWERCONTEXT_EVAL_FILESYSTEM_MIN_FREE_BYTES` | `10737418240` | Base byte reserve; claim admission also reserves 4 GiB per configured task slot |
| `POWERCONTEXT_EVAL_FILESYSTEM_MIN_FREE_INODES` | `1000000` | Base inode reserve; claim admission also reserves 250,000 inodes per configured task slot |
| `POWERCONTEXT_EVAL_WORKSPACE_RECLAIM_INTERVAL_SECONDS` | `10` | Successful scratch reclaim poll interval; retained `/runs` and non-success workspaces are never removed |
| `POWERCONTEXT_EVAL_TASK_PARALLELISM` | `1` | Concurrent independent OFF/ON task pairs; allowed range is 1 through 30 |
| `POWERCONTEXT_EVAL_TOKENSFLOW_FINALIZER_TIMEOUT_SECONDS` | `600` | Deadline after durable arm handoff before forced cleanup |
| `POWERCONTEXT_EVAL_TOKENSFLOW_FINALIZER_POLL_SECONDS` | `5` | Interruptible durable finalizer poll interval |
| `POWERCONTEXT_EVAL_CODEX_MODELS` | `gpt-5.6-sol` | Comma-separated models admitted for newly created batches and tasks; the default Sol model must remain present |

Changing the model allowlist affects only new submissions. Existing batches keep their immutable model and remain
readable, runnable, and retryable even when that model is no longer admitted for new work.
Before each claim, the Worker reserves both bytes and inodes for the configured parallelism. If either reserve is
unavailable or below its hard boundary, runnable batches pause with `resource_pressure`; recovery never resumes them
implicitly. A separate Worker maintenance loop removes only a succeeded attempt's reproducible `/work/<run-id>`
scratch after validating its durable report and terminal deferred cleanup. It never removes `/runs`, failed,
interrupted, queued, running, or cleanup-pending evidence. Scanning is cyclic, so completed tasks from older batches
cannot starve newer successful workspaces from reclamation.
TokensFlow finalization capacity is always twice `POWERCONTEXT_EVAL_TASK_PARALLELISM`; it is not independently
configurable. When the durable queue exceeds that bound, the oldest excess jobs are force-cleaned in the same poll.

Pause and cancel use a complete SWE-bench task as the boundary: the active OFF/ON pair finishes, then pause starts no
new child, while cancel marks every remaining unstarted child cancelled. They do not kill an arm midway. Resume is
always a manual resume and requires a fresh observation below the current threshold. Raising the threshold, reaching
the reset time, or recovering from usage unavailable never resumes a batch implicitly.

Changing a threshold updates only the protected batch and writes a control event. A transient usage-probe failure is
reported as usage unavailable and fails closed: preview, start, resume, and retry cannot proceed, and runnable batches
pause at the next safe boundary.

Only infrastructure failures are retryable. A retry creates a new immutable attempt for that logical task; prior
attempt evidence remains available, and no other completed task is rerun. Official `RESOLVED` and `UNRESOLVED`
outcomes are benchmark results and cannot be retried.
The durable attempt identity retains the form `<task-id>.attempt-0002`; filesystem and Docker resources use the
deterministic safe slug `<task-id>-attempt-0002`, because Docker evaluation run IDs do not accept a period.

## Transfer, build, and test on m0

Mac must not build or package deployable frontend artifacts. Transfer committed Git objects directly into the bare
source repository on m0:

```sh
git push m0:/data/powercontext-eval/source/powercontext.git \
  <candidate-sha>:refs/heads/evaluation
```

On m0, verify the received commit and check it out in the deployment working tree:

```sh
cd /data/powercontext-eval/deploy/powercontext
git fetch /data/powercontext-eval/source/powercontext.git evaluation
git checkout --detach <candidate-sha>
test "$(git rev-parse HEAD)" = "<candidate-sha>"
```

The evaluator reads `/data/powercontext-eval/source/powercontext.git`, not the deployment working tree. Frontend
dependencies and `dist` therefore cannot dirty or alter the source used by `latest`.

On m0, from `/data/powercontext-eval/deploy/powercontext`, run the Python verification:

```sh
/data/powercontext-eval/bin/uv sync --project evaluation --frozen
/data/powercontext-eval/bin/uv run --project evaluation pytest -c evaluation/pyproject.toml evaluation/tests -m "not live" -q
/data/powercontext-eval/bin/uv run --project evaluation ruff check evaluation
/data/powercontext-eval/bin/uv run --project evaluation ruff format --check evaluation
/data/powercontext-eval/bin/uv run --directory evaluation ty check src tests
```

m0 uses the pinned `node-v22.23.2-linux-x64-glibc-217` toolchain because its host glibc is 2.17. The committed npm
override selects Rollup's portable WASM runtime, so the frontend build does not load a newer-glibc native parser.
Build and test directly on m0:

```sh
export PATH=/data/powercontext-eval/toolchains/node-v22.23.2-linux-x64-glibc-217/bin:$PATH
export HTTP_PROXY=http://127.0.0.1:7890
export HTTPS_PROXY=http://127.0.0.1:7890
cd evaluation/web
npm ci --cache /data/powercontext-eval/cache/npm --no-audit --no-fund
npm test -- --run
npm run build
find ../.. -name '._*' -print -quit | grep -q . && exit 1 || true
```

Use the committed `evaluation/web/package-lock.json`. The deployed frontend remains
`/data/powercontext-eval/deploy/powercontext/evaluation/web/dist`; no frontend archive crosses from Mac to Linux.

Install the reviewed Linux `regctl` binary at `/data/powercontext-eval/bin/regctl` and configure
`POWERCONTEXT_EVAL_REGISTRY_BINARY` to that absolute path. When a task image is not already local, the worker uses
this user-space client through `POWERCONTEXT_EVAL_PROXY_URL` to export the `linux/amd64` image, imports the exact
temporary archive with `docker load`, verifies the immutable local image ID, and removes the archive. This path does
not reconfigure or restart the Docker daemon, so existing containers remain untouched. Verify the downloaded
binary against the checksum published for its pinned upstream release before installing it.

The official SWE-bench Pro evaluator keeps the harness default Docker network because some pinned task scripts
install test dependencies at evaluation time. Do not pass `--block_network` for this task set: doing so can leave
package-manager setup waiting until timeout and turns valid benchmark tasks into infrastructure failures. Official
evaluation containers receive neither the Codex credential nor the host Docker socket.

Each run retains the exact pinned public row as `instance.jsonl`. The selected harness commit predates the public
file's uppercase `FAIL_TO_PASS` and `PASS_TO_PASS` columns, so the runner separately writes
`evaluator-instance.jsonl` with the two evaluator-required lowercase fields encoded as JSON strings. Gold, OFF, and
ON use this derived compatibility input while reports and provenance continue to reference the unmodified public
row.

The pinned Open Library instance ending in `v29f82c9cf21d57b242f8d8b0e541525d259e2d63` contains two
`PASS_TO_PASS` node IDs parameterized from `datetime.now().year`. Its derived evaluator input advances only those
two year values to the evaluation year and following year so they still identify the tests collected in the task
container. The retained `instance.jsonl` remains value-equivalent to the verified catalog row.

## Install configuration and units

Create the configuration without exposing its eventual contents in logs:

```sh
install -d -m 0700 /data/powercontext-eval/config
install -m 0600 evaluation/deploy/powercontext-eval.env.example \
  /data/powercontext-eval/config/evaluation-console.env
chmod 0600 /data/powercontext-eval/config/evaluation-console.env
${EDITOR:?set EDITOR} /data/powercontext-eval/config/evaluation-console.env
test "$(stat -c %a /data/powercontext-eval/config/evaluation-console.env)" = 600
```

The example has no credential values. The Mac credential source is operator-supplied and must never enter Git or
the environment file. From the operator's Mac, copy it only to the explicit staging path inside the protected
configuration directory:

```sh
scp /operator/supplied/path/auth.json m0:/data/powercontext-eval/config/auth.json.staged
```

Then, on m0, install it without printing its contents and remove the staged file:

```sh
chmod 0600 /data/powercontext-eval/config/auth.json.staged
install -d -o rongfeng.frf -g users -m 0700 /data/powercontext-eval/codex-home
sudo install -o rongfeng.frf -g users -m 0600 \
  /data/powercontext-eval/config/auth.json.staged /data/powercontext-eval/codex-home/auth.json
unlink /data/powercontext-eval/config/auth.json.staged
sudo -u rongfeng.frf test -r /data/powercontext-eval/codex-home/auth.json
stat -c '%U:%G %a' /data/powercontext-eval/codex-home/auth.json | grep -qx 'rongfeng.frf:users 600'
```

Never paste authentication contents into terminal output, tickets, or logs.

For API-key Codex providers, add `POWERCONTEXT_EVAL_CODEX_AUTH_MODE`, `POWERCONTEXT_EVAL_CODEX_API_KEY`,
and `POWERCONTEXT_EVAL_CODEX_OPENAI_BASE_URL` only to the installed mode-0600 environment file. Set the auth mode
to `api`. These values are used by the Codex connectivity probe and invocation; ordinary `OPENAI_API_KEY` and
`OPENAI_BASE_URL` remain the separate provider credentials supplied only to PowerContext. The Codex key in the
environment must match the key in the protected `auth.json` copied into each disposable task container.

## Operate TokensFlow telemetry without loss

Set `POWERCONTEXT_EVAL_TOKENSFLOW_BINARY` to the selected absolute TokensFlow executable and
`POWERCONTEXT_EVAL_TOKENSFLOW_USER_HOME` to the absolute home whose `.tokensflow` directory contains the operator's
current configuration. The checked-in example uses a generic operator path and contains no credential. Replace it in
the protected mode-0600 environment file; do not copy credentials into Git or serialize the selected user-home path
through an API.

Set `POWERCONTEXT_EVAL_TOKENSFLOW_EGRESS_NETWORK` to an existing Docker network that provides the selected TokensFlow
endpoint with working egress. This value is mandatory and has no code default. The Worker validates it as one literal
Docker network name, attaches each fresh OFF/ON task container immediately before the TokensFlow identity gate,
verifies that attachment, keeps the task's original isolated PowerContext network attached, and disconnects only the
configured egress network after the daemon drain. The Worker never creates, modifies, or removes that external network
and never changes Docker daemon configuration. A missing, unsafe, unreachable, or unverifiable network fails closed
before container `tokensflow whoami` and Codex inference. TokensFlow lifecycle commands explicitly clear inherited
proxy variables and use this configured network directly; PowerContext and Codex continue using the isolated relay.

There are two different change procedures. A **configuration content replacement** atomically updates content at the
already configured source path; the next OFF or ON arm takes a fresh private snapshot, while an active arm continues
with its existing snapshot. A **configured path switch** changes either named environment value. Keep the batch paused,
wait for the active task boundary, edit the protected environment file, and restart only the Worker. A path switch does
not authorize work or perform a manual resume.

TokensFlow's existing host user service belongs to the evaluation operator's user manager. Enable linger once, then
enable the TokensFlow-managed unit as that user; do not create a second root service or an unmanaged background daemon:

```sh
sudo loginctl enable-linger <evaluation-user>
sudo -iu <evaluation-user> systemctl --user enable --now tokensflow.service
sudo -iu <evaluation-user> systemctl --user status tokensflow.service
sudo -iu <evaluation-user> tokensflow status
```

Run the final status command interactively because it can display account identity and local paths; do not paste or
retain its raw output. The evaluation identity gate runs `tokensflow whoami` with the selected host snapshot and inside
the arm container, compares normalized content, and retains only SHA-256 values and byte length. A mismatch is an
infrastructure failure before Codex. Each accepted arm starts one detached daemon in the existing task container and
proves the PID is the mounted TokensFlow executable before inference.

After Codex returns, the arm uses one shared 60-second deadline for all of the following: normal TERM and confirmed
daemon exit, `tokensflow upload --all`, and queue inspection proving the exact caught-up marker with no explicit
pending, rejected, failed, blocked, or circuit-open state. Successful non-secret provenance is written only after that sequence, and only then may the
container cleanup run. A replay that reports only duplicate records is acceptable because server-side deduplication is
part of the upload contract; a missing record is never accepted.

If TERM, upload, or queue verification fails, the task records an infrastructure failure and atomically requests a
batch pause. The runner does not force-remove that arm container or delete the only recoverable data. It writes the
fixed, non-sensitive marker
`work/<run-id>/<arm>/runtime/tokensflow-recovery.json`; the private TokensFlow home and Codex JSONL remain as the
**preserved private spool** for recovery and are not public report artifacts. Do not delete, rename, publish, or treat a
marked runtime as a clean attempt. Repair the service, endpoint, or configured path first; replay the preserved spool
with `upload --all`, confirm the queue is caught up, retain the recovery evidence privately, and only then retry that
single task. Duplicate replay is safer than loss. Verify the retry created exactly one new immutable attempt and that
no other child was claimed before an explicit manual resume.

For a controlled rollout, keep every real batch paused, prove zero active task pairs, run one approved single task,
and verify both arms show identity match, daemon start/stop, upload success, caught-up queue, no recovery marker, and
normal cleanup. On any failure, leave the batch paused and preserve the spool. Rollback means restoring the previous
two configured paths and accepted checkout at a task boundary, then restarting only the evaluation services and
rechecking health. It must not prune Docker, delete the queue or spool, or restart/reconfigure new-api, MySQL, Redis,
or the proxy.

Verify units before installation:

```sh
systemd-analyze verify evaluation/deploy/powercontext-eval-web.service \
  evaluation/deploy/powercontext-eval-worker.service
sudo install -m 0644 evaluation/deploy/powercontext-eval-web.service /etc/systemd/system/
sudo install -m 0644 evaluation/deploy/powercontext-eval-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now powercontext-eval-web.service powercontext-eval-worker.service
```

The Web unit keeps its systemd filesystem restrictions and runs as `rongfeng.frf`. The Worker runs as root because it
owns the disposable benchmark runtime and Docker lifecycle; it does not receive a synthetic UID/GID or a restricted
filesystem namespace. Task containers likewise keep the image's default root user and default `/root` home instead of
forcing `HOME`, `CODEX_HOME`, a read-only root, dropped capabilities, or `no-new-privileges`. Isolation still comes
from task-scoped mounts and networks, the absence of a Docker-socket mount, and CPU, memory, and PID limits.

Successful task containers and scoped networks are cleaned normally after report handoff. If evaluation
infrastructure fails after a task starts, the Worker retains the exact container, scoped network, workspace, runtime,
and logs for diagnosis; an operator removes those resources explicitly only after collecting evidence.

## Verify and operate

```sh
systemctl status powercontext-eval-web.service powercontext-eval-worker.service
journalctl -u powercontext-eval-web.service -u powercontext-eval-worker.service --since today
curl --fail --show-error http://100.88.99.11:8787/api/health
```

Submitting work adds it to the SQLite-backed queue. The Worker supervisor runs the configured number of task-pair
slots; every slot atomically leases at most one queued attempt for `POWERCONTEXT_EVAL_LEASE_SECONDS`. Polling is
controlled by `POWERCONTEXT_EVAL_POLL_SECONDS`. A service crash causes systemd to restart it after five seconds. An
interrupted lease is recovered independently after expiry; a batch infrastructure recovery fails closed by pausing
new claims and preserving the failed attempt for diagnosis. The Web and Worker share only the SQLite database and can
restart independently.

Persistent state and artifacts live under `/data/powercontext-eval`:

- Queue database: `/data/powercontext-eval/web/tasks.sqlite3`
- Per-run artifacts: `/data/powercontext-eval/runs/`
- Cached harness and dataset: `/data/powercontext-eval/cache/`
- Checkout and frontend snapshot: `/data/powercontext-eval/deploy/powercontext/`

Batch membership, source order, the resolved PowerContext commit, control intent, usage observations, control events,
and each immutable attempt are stored durably. Completed children are never rerun automatically. After restart,
queued children remain queued; an expired running lease becomes an interrupted child, and the worker continues with
later queued children. Aggregate reports are rebuilt from the immutable retained child artifacts.

## Report semantics and retained context

The report publishes measurements, not an authored acceptance conclusion:

- OFF and ON resolution rates use all 731 selected tasks as the denominator;
- a missing or failed evaluator result is not mislabeled as ordinary unresolved work and is counted separately as
  an execution/evaluation failure;
- the four paired outcome categories include only children with official results for both arms;
- input, output, and total Token comparisons are each calculated only from paired children for which both OFF and ON
  contain that metric; the displayed measured-task denominator is therefore identical for both arms;
- unavailable elapsed time and patch byte counts are omitted from the product report.

Each successful child retains the complete observable, sanitized OFF and ON timeline. The timeline contains the
benchmark prompt, Codex JSONL events, official evaluation, and exact PowerContext injections in timestamp order.
Injection records retain query, scope/session/turn identifiers when present, returned hit fields, and the exact
injected text. Codex authentication material, proxy credentials, environment secrets, and secret-shaped fields are
rejected before API delivery.

## Schema migration and release backup

The batch release migrates the existing SQLite database in place and preserves legacy task rows. Before deploying
the new SHA, stop only the evaluation worker and create a SQLite-consistent backup:

```sh
sudo systemctl stop powercontext-eval-worker.service
install -d -m 0700 /data/powercontext-eval/backups
sqlite3 /data/powercontext-eval/web/tasks.sqlite3 \
  ".backup '/data/powercontext-eval/backups/tasks-before-batch-<timestamp>.sqlite3'"
test -s /data/powercontext-eval/backups/tasks-before-batch-<timestamp>.sqlite3
```

Record the exact prior checkout SHA, unit files, environment-file checksum, Web/Worker status, and restart counts.
Deploy only the reviewed detached SHA, then initialize the schema by starting the Web process before the worker.
Do not delete the backup after successful startup.

## Preflight and acceptance

Run acceptance on m0 only. Before and after starting the console, record existing-service health with the site's
normal read-only health checks and compare results; the console units must not restart or reconfigure them. Also
check that port 8787 is free before first start:

```sh
ss -ltn 'sport = :8787'
curl --fail --show-error http://100.88.99.11:8787/api/health
```

For a secret scan that does not print matching values, inspect only filenames and exit status:

```sh
git grep -IlE '(api[_-]?key|password|token|secret)[[:space:]]*=' -- evaluation/deploy evaluation/README.md
test ! -f evaluation/deploy/evaluation-console.env
```

Docker cleanup audit: compare `docker ps -a --format '{{.ID}} {{.Names}} {{.Status}}'` and
`docker network ls --format '{{.ID}} {{.Name}}'` before and after an evaluation. Do not use broad prune commands;
investigate and remove only resources proven to belong to a completed run.

Verify the production catalog without starting work:

```sh
/data/powercontext-eval/bin/uv run --project evaluation python -c \
  "from pathlib import Path; from powercontext_eval.benchmarks.swebench_pro.catalog import SweBenchProCatalog; x=SweBenchProCatalog.load(Path('/data/powercontext-eval/cache/swebench-pro.git/helper_code/sweap_eval_full_v2.jsonl')); print(len(x.instance_ids), x.dataset_sha256)"
```

The output must be exactly the count `731` followed by the pinned SHA-256 above.

To validate preview and the task-boundary control contract without accidentally launching Codex:

1. verify the database and `/api/health` both report zero queued and zero running real tasks;
2. start Web and Worker so Worker can persist a fresh account-usage snapshot; with an empty queue this starts no
   benchmark work;
3. verify `POST /api/batches/preview` reports exactly 731 tasks and creates no batch or queue rows;
4. run pause, resume, cancel, and retry checks only against the deterministic fixture or an already-cancelled
   validation batch;
5. verify the control-event order, attempt retention, and zero running real children.

This preview and deterministic control check is a deployment check, not authorization to run the real benchmark.

## Rollback

To roll back only concurrency, keep the batch paused, set `POWERCONTEXT_EVAL_TASK_PARALLELISM=1`, and restart only the
evaluation Worker. Verify `/api/health` reports `task_parallelism: 1` before an explicit resume; changing capacity
never resumes a batch automatically.

Stop the two console units, check out the previously accepted commit received in the m0 bare repository, rebuild the
frontend on m0, resync the frozen evaluation environment, then start the units and repeat the health and queue checks:

```sh
sudo systemctl stop powercontext-eval-worker.service powercontext-eval-web.service
git fetch /data/powercontext-eval/source/powercontext.git evaluation
git checkout --detach <prior-accepted-commit>
/data/powercontext-eval/bin/uv sync --project evaluation --frozen
(export PATH=/data/powercontext-eval/toolchains/node-v22.23.2-linux-x64-glibc-217/bin:$PATH; \
  cd evaluation/web && npm ci --cache /data/powercontext-eval/cache/npm --no-audit --no-fund && npm run build)
sudo systemctl start powercontext-eval-web.service powercontext-eval-worker.service
curl --fail --show-error http://100.88.99.11:8787/api/health
```

Rollback does not delete the queue or run artifacts. Back up the SQLite database before any schema-changing release.
If the batch migration itself must be rolled back, keep both services stopped, preserve the failed database for
forensics, restore the explicit pre-release SQLite backup, then start the prior Web SHA before the prior worker SHA.
