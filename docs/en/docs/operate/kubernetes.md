---
title: Deploy on Kubernetes
description: Deploy API and background roles with Helm and external OceanBase.
---

# Deploy on Kubernetes

The [Helm chart](https://github.com/oceanbase/powercontext/tree/master/deploy/helm/powercontext) packages the current
`api` / `background` roles with an external OceanBase database. It includes Secret references,
API health probes, a ClusterIP Service, optional Ingress and configurable resources.

The chart requires an explicitly pinned application image and existing Secrets. Background
replicas use a single active supervisor with database lease election. The background runner has
no HTTP health endpoint; Pod readiness alone does not establish processing health.
`background.maxWorkers` controls the Topic Memory quota through a legacy alias, not the
total process count. With `maxWorkers=2` and all seven built-in families enabled, their quotas
sum to 11; the acceptance profile's `test` model enables four families with quotas totaling eight.
Without a generation model, no families are enabled by default. Actual concurrency depends on
enabled capabilities and scheduling. Size resources accordingly.

Use `commonLabels`, `podAnnotations`, `podSecurityContext` and `securityContext` for cluster
policy overrides. In clusters that assign UID/GID values, set `runAsUser`, `runAsGroup` and
`fsGroup` to `null` under `podSecurityContext`; validate admission and volume permissions.
PDBs and topology-spread constraints are managed outside this chart. MCP is mounted as
stateless Streamable HTTP by the runtime; the chart does not configure that property.

Follow the chart's installation, coordinated upgrade and acceptance-test instructions. SQLite
and embedded seekdb remain single-process evaluation options outside this chart.
Before the first install, initialize even an empty OceanBase database with the target image's
full Server [database migration](database-migrations.md) and verify `server_ready=true`.
OceanBase maintenance requires a persistent evidence directory and `--lock-coordination single-host`;
all commands and retries use one fixed maintenance host, stable hostname and the same directory.
The chart README provides the container commands and stages startup with one API Pod and zero background
replicas to seed the static bearer Access bindings before restoring the desired replica counts.
Existing databases also need a reviewed migration plan,
backup and stopped writers; [Atomic Memory migration](atomic-memory-migration.md) and
[processing migration](artifact-processing-migration.md) describe domain-specific compatibility paths.
The chart does not run migrations automatically. Historical cluster evidence uses an older image and schema;
validate initialization, replica startup and Pod replacement with the target application image separately.

For cluster acceptance, use the chart's `values-acceptance.yaml` together with your local image and
Secret references. It configures two API replicas, one initial background replica, and the local
`test` generation model. The acceptance script rejects fewer than two configured API replicas and
requires two Ready API Pods before creating test data and after replacing API Pods. Run it only in
an isolated test namespace with a disposable external OceanBase database; it creates data, deletes
Pods, and changes background replica counts. Pass `--confirm-disposable` explicitly. The script
also creates two child Scopes, obtains a cursor with `limit=1` on one API Pod and resumes it
on each other API Pod to verify shared signing keys without skipped or repeated results.
A passing configuration test is not cluster acceptance.
