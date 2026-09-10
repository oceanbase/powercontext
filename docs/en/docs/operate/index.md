---
title: Deploy and operate
description: Run a persistent Server and find deployment, monitoring, and recovery procedures.
---

# Deploy and operate

Start with the operational task you need:

| Task | Guide |
| --- | --- |
| Keep a personal Server running, deploy a container, or enable authentication | [Deploy the Server](deploy-server.md) |
| Upgrade or switch background supervision mode | [Migrate Artifact processing state](artifact-processing-migration.md) |
| Inspect logs, metrics, and traces | [Observability](observability.md) |
| Send traces to an analysis service | [Phoenix](trace-with-phoenix.md), [Langfuse](trace-with-langfuse.md) |
| Diagnose failures and restore service or data | [Troubleshoot](troubleshoot.md) |
| Look up defaults and environment variables | [Configuration](configuration.md) |

The native personal service uses systemd on Linux, LaunchAgent on macOS, and Task Scheduler on Windows.
Windows support is `experimental`. Check the service-specific installation and environment-file requirements.
