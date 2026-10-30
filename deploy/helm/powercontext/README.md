# PowerContext Helm chart

Deploy the current `api` and `background` roles against an **external OceanBase** database.
This chart does not provision a database. SQLite and embedded seekdb are unsupported;
use the single-process Docker example for evaluation.

## Application compatibility

Chart 0.1.0 targets the current master role/configuration and managed-database contracts,
inspected at `1f0afd6b`. No public release image is assumed.
Build the repository Dockerfile from the application revision being tested and publish it
to your registry. Supply its immutable tag or SHA-256 digest explicitly; `latest` is rejected.
The chart is not compatible with the proposed API/Scheduler/Worker deployment until that
runtime is integrated and tested. Keep every Pod on the same application image.

```sh
# From the repository root; choose your registry and a unique immutable image tag.
POWERCONTEXT_VERSION=$(uvx --from hatchling --with hatch-vcs hatchling version)
docker build --file docker/Dockerfile \
  --build-arg "POWERCONTEXT_VERSION=${POWERCONTEXT_VERSION}" \
  --tag registry.example.com/powercontext:YOUR_REVISION .
docker push registry.example.com/powercontext:YOUR_REVISION
```

## Secrets and configuration

Provision a Secret named `powercontext` in your namespace through your secret manager:

| Key | Value |
| --- | --- |
| `database-url` | `mysql+aoceanbase://USER:PASSWORD@HOST:2881/DATABASE?charset=utf8mb4` |
| `bearer-token` | Nonempty static bearer credential |
| `cursor-signing-secret` | The same secret of at least 32 bytes for every replica |

URL-encode credentials in the database URL. The database must meet PowerContext's
OceanBase requirements. Both empty and existing databases require explicit Server
maintenance before any API or background Pod starts; follow the initialization procedure
below. Secret values are never stored in Helm values or a rendered Secret.

Optional `providerSecret` names a second existing Secret whose keys are provider environment
variables such as `OPENAI_API_KEY`. Reserve it for provider credentials: do not put
PowerContext role, storage, authentication, or runtime overrides there.
Non-secret model settings belong in `inference`:

```yaml
image:
  repository: registry.example.com/powercontext
  tag: YOUR_REVISION
existingSecret: powercontext
providerSecret: powercontext-provider
inference:
  POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL: openai:gpt-4.1-mini
```

Configure a complete embedding profile as described in the application configuration guide
when enabling vector search. The chart does not enable models or incur provider requests by default.
Use the same model configuration on both roles. Secrets referenced as environment variables
are read at Pod startup; coordinate a restart after rotation. Rotating the cursor key invalidates
outstanding cursors.

## Initialize the database and install

OceanBase Server startup validates managed storage; it does not initialize an empty
database or repair an old schema. Run the target application's `server db-migrate`
commands before the first Helm install. Stop every writer before maintaining an existing
database. There is no automatic Helm migration hook or per-Pod initialization container.

Use one fixed maintenance host with Docker and a persistent evidence directory dedicated
to this database. All inspections, migration attempts and retries must use that same host,
container hostname and mounted directory. A shared volume or a repeated hostname on a
different node does not provide cross-host coordination. If you instead run a Kubernetes
maintenance Job, pin it to that one node, retain its evidence volume and hostname across
replacement Jobs, and follow the same commands and stopped-writer requirements.

On that host, prepare a protected `deployment.env` through your secret manager. It must
select `POWERCONTEXT_SERVER_DATABASE_KIND=oceanbase`, the same database URL and credentials,
authentication and cursor secrets, global supervisor mode, and the same inference and
Processing configuration as the target Pods. In particular, the acceptance profile's
`POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=test` must also be present when preparing
its database. The migration plan binds the target Processing declaration and embedding
profile; changing those settings requires a new plan. Do not commit this file.

The database must already exist in a MySQL-mode user tenant. The maintenance credential
needs the target-identity permissions documented in
[database migrations](../../../docs/en/docs/operate/database-migrations.md#target-configuration-and-external-evidence).
Create a durable directory writable by the image's UID/GID `10001`, then run:

```sh
export POWERCONTEXT_IMAGE=registry.example.com/powercontext:YOUR_REVISION
sudo install -d -m 0700 -o 10001 -g 10001 /srv/powercontext/YOUR_DATABASE/migration-evidence

pc_migrate() {
  docker run --rm --hostname powercontext-maintenance \
    --env-file "$PWD/deployment.env" \
    --mount type=bind,src=/srv/powercontext/YOUR_DATABASE/migration-evidence,dst=/migration-evidence \
    "$POWERCONTEXT_IMAGE" server db-migrate "$@" --bundle server \
    --evidence-dir /migration-evidence --lock-coordination single-host
}

pc_migrate status
pc_migrate plan --backup manual --shared-database
# Inspect the plan, stop every writer and make a manual database backup first.
# Replace PLAN_ID with the returned plan_id; confirmation flags declare those actions complete.
pc_migrate apply --backup manual --shared-database --plan-id PLAN_ID \
  --backup-confirmed --maintenance-confirmed --yes
pc_migrate verify
```

Require `state=ready`, `readiness_scope=server` and `server_ready=true` before starting
the Deployments. Keep the evidence directory and backup after the container exits.
Unsupported historical layouts require a registered migration path; do not bypass
admission or reuse the historical patched image below to initialize current storage.
Then install the chart using that same image and configuration. Start one API Pod
first so its static bearer Access bindings are committed before other processes
bootstrap the same preset. Schema maintenance does not seed those credential-specific
bindings. After that API becomes Ready, restore the desired replica counts from your
saved values file; do not retain the temporary overrides with `--reuse-values`:

```sh
helm lint deploy/helm/powercontext -f values.local.yaml
helm upgrade --install pc deploy/helm/powercontext \
  --namespace powercontext --create-namespace -f values.local.yaml \
  --set api.replicas=1 --set background.replicas=0 --wait --timeout 10m
helm upgrade pc deploy/helm/powercontext \
  --namespace powercontext -f values.local.yaml --wait --timeout 10m
kubectl -n powercontext port-forward service/pc-powercontext 8000:8000
```

The Service only selects API Pods. Ingress is optional; configure its host, TLS Secret and
controller-specific annotations when enabled. Bearer authentication and enforced Access are
always enabled. Use HTTPS or a trusted private network for clients. The Pod service account
token is not mounted; no Kubernetes API permissions are required.

## Cluster policy overrides

`commonLabels` adds string-valued labels to resources and Pods without changing selectors.
The chart's identity labels (`app.kubernetes.io/name`, `instance`, `component`, `managed-by`
and `helm.sh/chart`) are reserved. `podAnnotations` adds string-valued Pod annotations.
`podSecurityContext` and container `securityContext` expose the fields listed in
`values.schema.json`; their defaults retain non-root execution, RuntimeDefault seccomp,
dropped capabilities and a read-only root filesystem.

For clusters that assign a UID/GID through admission, remove the fixed identity fields
with Helm's `null` overrides. For example, an OpenShift SCC may assign the identity:

```yaml
commonLabels:
  cost-center: platform
podAnnotations:
  example.com/owner: platform
podSecurityContext:
  runAsUser: null
  runAsGroup: null
  fsGroup: null
```

Validate the rendered Pod against your cluster's admission policy and volume permissions.
The chart does not create PDBs or topology-spread constraints; manage any required PDB
separately and use the existing `affinity`, `nodeSelector` and `tolerations` for placement.
Ingress annotations must be strings; `ingress.tls` is an array of entries with a nonempty
`secretName` and optional string-array `hosts`.

## Replicas and health

`api.replicas` and `background.replicas` default to one. More background replicas are standby
candidates for the **global** database lease, not extra active supervisors. Each active supervisor
starts local workers with per-family quotas. `background.maxWorkers` sets the legacy
`artifact_processing_max_workers` alias and the `topic_memory_max_workers` quota; it is
not a global process limit or an additional worker pool. Other quotas retain their defaults:
Memory, Experience, Skill, Handoff and Prompt each allow one worker, and Profile allows four.
With `maxWorkers=2` and all seven built-in families enabled, their quotas sum to 11 workers.
The acceptance profile's `test` generation model enables four families with quotas totaling eight;
without a generation model, no families are enabled by default.
This is a quota estimate, not measured concurrency: enabled capabilities, available work and
scheduling determine actual occupancy. Size memory for the supervisor and its child processes;
the default 2 GiB limit is a starting configuration, not a capacity guarantee.
The chart deliberately fixes global mode; switching supervisor modes requires a separate migration.

API probes use `/health/live` and `/health/ready`. Startup has a five-minute budget by default;
adjust probes for database startup times. Readiness can return HTTP 200 with degraded inference:
inspect its body and application errors rather than treating readiness as proof that model calls work.
`/metrics` is available through the API Service with the application's authentication rules.
It does not report the remote background supervisor's processing metrics.

The background runner has **no HTTP listener**. There is no HTTP probe or background Service.
Kubernetes restarts an exited process, but Pod Ready only means its container started; it does
not prove database access, lease ownership, or progress. Monitor restart counts, background logs,
lease renewal and durable processing progress separately. Standby candidates are healthy without
owning the lease. Do not add a probe that kills every non-leader, or use `kill -0` as a claim of
processing health. A dedicated background health endpoint remains a separate runtime improvement.

All API Pods share a cursor key so paginated requests can cross replicas. The application
runtime mounts MCP as stateless Streamable HTTP with `stateless_http=True`; this is a runtime
property, not a Chart environment setting. This transport does not offer persistent MCP sessions;
clients must support stateless Streamable HTTP. Local `/data` and `/tmp` are ephemeral writable
directories; durable application state lives in OceanBase. Local skill publication is disabled.

## Upgrade and migration

`Recreate` prevents rolling replacements within each Deployment. **It does not coordinate the two
Deployments**. Do not assume an ordinary `helm upgrade` is a safe mixed-version upgrade.

1. Pause client writes and explicit processing triggers. Back up OceanBase and record the deployed
   image, configuration and Secret versions.
2. If uninterrupted completion is required, wait for accepted processing to finish and verify durable
   progress before stopping candidates. SIGTERM revokes leadership and stops child processes; it does
   not promise every model call will drain. A longer grace period alone does not guarantee draining.
3. Scale both Deployments to zero and wait until all their Pods have terminated. Disable any external
   reconciler that would restore replicas during maintenance.
4. Use the target image and configuration to run the full Server maintenance procedure
   above on the fixed maintenance host with its retained evidence directory. Review the
   plan and backup policy, apply the exact `plan_id` with the required confirmations, and
   require full Server readiness from `verify`. Complete any required Processing ownership
   transition separately through `--bundle processing` as described in
   [database migrations](../../../docs/en/docs/operate/database-migrations.md).
   [Atomic Memory migration](../../../docs/en/docs/operate/atomic-memory-migration.md) and
   [processing migration](../../../docs/en/docs/operate/artifact-processing-migration.md)
   describe compatibility commands and their limits; partial domain readiness does not
   replace full Server readiness. There is no automatic Helm migration hook.
5. Only after migration verification succeeds, start one API Pod with zero background replicas
   using the target image and your saved values as shown above. After it is Ready, restore the
   desired replica counts from those values. Check readiness, authentication and background
   progress before resuming traffic.

A Helm rollback does not roll back the database. Assess schema compatibility and restore the database
backup when required before returning to an earlier application version. Uninstall removes the
workloads and ephemeral files; externally managed Secrets and OceanBase data remain.

## Verification

Render/configuration tests (requires Helm, pytest and the repository server dependencies):

```sh
uv run --locked --extra server pytest tests/test_helm_chart.py tests/test_helm_acceptance.py -q
```

The Main workflow installs Helm 3.19.0, runs strict lint on the default and acceptance
profiles, and runs these rendering/configuration and acceptance CLI tests. The CLI tests
simulate kubectl responses to check preflight rejection and per-Pod verification.

Cluster acceptance is intentionally opt-in. Confirm the target kubeconfig context before running
these commands. Use an isolated namespace, a disposable external OceanBase database, and an image
built from the application revision under test. Provision an existing Secret in that namespace
with the three keys described above; do not use a production database or production credentials.

Create `values.acceptance.local.yaml` containing the image and Secret name, without Secret values:

```yaml
image:
  repository: registry.example.com/powercontext
  tag: YOUR_REVISION
existingSecret: powercontext-acceptance
```

Initialize and verify the disposable database with the target image and acceptance
configuration using the maintenance procedure above before installing any Pods.
The supplied `values-acceptance.yaml` selects two API replicas, one initial background replica,
and the local `test` generation model so the background capability acquires a lease without an
external model provider. This profile is for deployment tests only; leave embedding providers
unconfigured. It does not validate real model quality or interrupted inference recovery.

```sh
helm upgrade --install pc-acceptance deploy/helm/powercontext \
  --namespace powercontext-acceptance --create-namespace \
  -f values.acceptance.local.yaml \
  -f deploy/helm/powercontext/values-acceptance.yaml \
  --set api.replicas=1 --set background.replicas=0 --wait --timeout 10m
helm upgrade pc-acceptance deploy/helm/powercontext \
  --namespace powercontext-acceptance -f values.acceptance.local.yaml \
  -f deploy/helm/powercontext/values-acceptance.yaml --wait --timeout 10m
uv run python tests/e2e/helm_acceptance.py \
  --namespace powercontext-acceptance --release pc-acceptance --confirm-disposable
```

The explicit `--confirm-disposable` flag is required before any cluster access.
This test writes a parent Scope and two child Scopes, replaces API Pods, scales background candidates from one to two,
and removes the original leader Pod. It checks HTTP/MCP authentication through the Service, the
parent Scope through each API replica after replacement, cross-Pod cursor continuation,
and database lease holder/generation change. It lists the children with `limit=1` on one API Pod,
then resumes that cursor on every other API Pod and checks for skipped or repeated Scopes. It rejects
deployments with fewer than two configured API replicas before mutating the cluster, and requires at least
two Ready, non-terminating API Pods before creating its Scope and after API Pod replacement. Exactly one
initial background replica and a configured processing capability that acquires a lease are also required.
It returns background replicas to one. It does not delete its test Scopes or database.
Run only against a test deployment. A lease transition is not proof that all accepted inference work
completed: validate application processing and provider-specific recovery separately with representative
workloads. Multi-active-worker recovery and mixed-version rolling upgrades are outside this chart's scope.

### Historical cluster evidence

On 2026-10-05, the isolated cluster check passed on three Linux amd64 RKE2 nodes
(Kubernetes `v1.35.7+rke2r1`, Helm 3.19.0) with OceanBase CE 4.4.2.1. A fresh database
had zero tables before concurrent startup of two API Pods and one background Pod.
All initial and replacement application containers had zero restarts.

The check verified authenticated HTTP/MCP and unauthenticated 401 responses through
the Service, persisted Scope readback from each replacement API Pod, and a new
background lease holder with generation increasing from 1 to 3. Background replicas
returned to one; the disposable namespaces and database were removed.

The application image included separate local concurrent-startup fixes. These make
relational and FULLTEXT DDL idempotent, probe all four FULLTEXT projections with `MATCH`,
and retry narrowly identified authorization bootstrap conflicts after rollback.
This evidence predates the Atomic Memory and managed Server schemas. The old Memory-entry
FULLTEXT patch and this image do not establish acceptance for the current runtime. Current
deployments initialize through explicit maintenance, seed Access with one API Pod, then
start the desired replicas against verified storage. Validate that sequence and Pod replacement with the target image;
the historical empty-database startup result does not certify it.

| Component | Tested revision |
| --- | --- |
| Chart | `0.1.0`; application image supplied explicitly |
| Application base | `86537e4fbe2f3bec2d0a93c73caea4ec8ebaec24`, package `1.2.1.dev0` |
| Runtime patch SHA-256 | `3b38b98cd3df3cbe806d14bb8bb927ac61a0d07943f820a25ed14e8b1fcadda1` |
| Local image tag | `powercontext-fts-fix:86537e4f-20261005-v3` |
| Local image ID | `sha256:c95925cebba951d9ded320cc887eef9cc8ed0d35a768b7859d1f2b211336d6dd` |

The image ID is a local container identity, not a published registry manifest digest.
Validation used the local `test` generation model and a native UTC tenant. It covers
the historical deployment configuration only, including neither the current startup sequence
nor cross-Pod cursor continuation; model/provider and schema-migration acceptance belong
to their respective runtime changes.
