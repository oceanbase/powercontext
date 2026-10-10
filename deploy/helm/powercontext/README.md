# PowerContext Helm chart

Deploy the current `api` and `background` roles against an **external OceanBase** database.
This chart does not provision a database. SQLite and embedded seekdb are unsupported;
use the single-process Docker example for evaluation.

## Application compatibility

Chart 0.1.0 targets the current master role/configuration contract, inspected at
`86537e4f`. No public release image is assumed.
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
OceanBase requirements. For an existing database, complete the processing migration
before startup. Secret values are never stored in Helm values or a rendered Secret.

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

```sh
helm lint deploy/helm/powercontext -f values.local.yaml
helm upgrade --install pc deploy/helm/powercontext \
  --namespace powercontext --create-namespace -f values.local.yaml --wait --timeout 10m
kubectl -n powercontext port-forward service/pc-powercontext 8000:8000
```

The Service only selects API Pods. Ingress is optional; configure its host, TLS Secret and
controller-specific annotations when enabled. Bearer authentication and enforced Access are
always enabled. Use HTTPS or a trusted private network for clients. The Pod service account
token is not mounted; no Kubernetes API permissions are required.

## Replicas and health

`api.replicas` and `background.replicas` default to one. More background replicas are standby
candidates for the **global** database lease, not extra active supervisors. Each active supervisor
starts local workers, bounded by `background.maxWorkers`. Size memory for those child processes.
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

All API Pods share a cursor key and use `FASTMCP_STATELESS_HTTP=true` so MCP requests can cross
replicas. This transport does not offer persistent MCP sessions; clients must support stateless
Streamable HTTP. Local `/data` and `/tmp` are ephemeral writable directories; durable application
state lives in OceanBase. Local skill publication is disabled.

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
4. With the target application's configuration and credentials, run the documented processing
   migration `plan`, then `apply --maintenance-confirmed` when needed, then `verify`. Follow
   `docs/en/docs/operate/artifact-processing-migration.md`; there is no automatic Helm migration hook.
5. Only after migration verification succeeds, upgrade the chart/image using your saved values and
   restore the desired replica counts. Check readiness, authentication and background progress before
   resuming traffic.

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

The supplied `values-acceptance.yaml` selects two API replicas, one initial background replica,
and the local `test` generation model so the background capability acquires a lease without an
external model provider. This profile is for deployment tests only; leave embedding providers
unconfigured. It does not validate real model quality or interrupted inference recovery.

```sh
helm upgrade --install pc-acceptance deploy/helm/powercontext \
  --namespace powercontext-acceptance --create-namespace \
  -f values.acceptance.local.yaml \
  -f deploy/helm/powercontext/values-acceptance.yaml --wait --timeout 10m
uv run python tests/e2e/helm_acceptance.py \
  --namespace powercontext-acceptance --release pc-acceptance
```

This test writes one test Scope, replaces API Pods, scales background candidates from one to two,
and removes the original leader Pod. It checks HTTP/MCP authentication through the Service, the
Scope through each API replica after replacement, and database lease holder/generation change. It rejects
deployments with fewer than two configured API replicas before mutating the cluster, and requires at least
two Ready, non-terminating API Pods before creating its Scope and after API Pod replacement. Exactly one
initial background replica and a configured processing capability that acquires a lease are also required.
It returns background replicas to one. It does not delete its Scope or database.
Run only against a test deployment. A lease transition is not proof that all accepted inference work
completed: validate application processing and provider-specific recovery separately with representative
workloads. Multi-active-worker recovery and mixed-version rolling upgrades are outside this chart's scope.

### Verified environment

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
The fixes must be integrated separately before this cold-start result applies to a
released image.

| Component | Tested revision |
| --- | --- |
| Chart | `0.1.0`; application image supplied explicitly |
| Application base | `86537e4fbe2f3bec2d0a93c73caea4ec8ebaec24`, package `1.2.1.dev0` |
| Runtime patch SHA-256 | `3b38b98cd3df3cbe806d14bb8bb927ac61a0d07943f820a25ed14e8b1fcadda1` |
| Local image tag | `powercontext-fts-fix:86537e4f-20261005-v3` |
| Local image ID | `sha256:c95925cebba951d9ded320cc887eef9cc8ed0d35a768b7859d1f2b211336d6dd` |

The image ID is a local container identity, not a published registry manifest digest.
Validation used the local `test` generation model and a native UTC tenant. It covers
this deployment configuration; model/provider and schema-migration acceptance belong
to their respective runtime changes.
