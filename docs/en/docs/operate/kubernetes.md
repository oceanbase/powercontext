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

Follow the chart's installation, coordinated upgrade and acceptance-test instructions. SQLite
and embedded seekdb remain single-process evaluation options outside this chart. See
[processing migration](artifact-processing-migration.md) before reusing an existing database.

For cluster acceptance, use the chart's `values-acceptance.yaml` together with your local image and
Secret references. It configures two API replicas, one initial background replica, and the local
`test` generation model. The acceptance script rejects fewer than two configured API replicas and
requires two Ready API Pods before creating test data and after replacing API Pods. Run it only in
an isolated test namespace with a disposable external OceanBase database; it creates data, deletes
Pods, and changes background replica counts. A passing configuration test is not cluster acceptance.
