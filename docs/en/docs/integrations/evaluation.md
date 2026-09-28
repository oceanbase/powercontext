---
status: evaluation
title: Evaluation integrations
description: Bub evaluation use and capability boundaries.
---

# Evaluation integrations

`evaluation`: Bub is for evaluation only. The repository classifies it as an `evaluation_harness`; it is not one of the
8 Agent Hosts or 3 Python framework adapters. Its current availability is `master_only`.

The Bub plugin connects to the Server through the Python Client and provides Memory search, writes, and context
preparation, including context before model calls. Event capture is disabled by default. When enabled, completed
events become Sources and can be flushed periodically. No Handoff capability is declared.

Loopback HTTP is allowed by default. Non-loopback HTTP requires `POWERCONTEXT_BUB_ALLOW_INSECURE_HTTP=true` or
`PowerContextSettings(allow_insecure_http=True)`, covering both plugin hooks and tools. HTTPS certificate validation
stays enabled. See [Connect to a remote Server](../operate/connect-remote-server.md) for shared client settings;
Bub has no PowerContext setup installer.

See the [Bub integration README](https://github.com/oceanbase/powercontext/blob/master/integrations/bub/README.md) for
installation and capture settings. See [benchmarks](/en/benchmarks/) for methods, results, and limitations.
Evaluation results do not imply equal capabilities or stability across Agent integrations.
