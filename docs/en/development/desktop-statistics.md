---
title: "Desktop Scope statistics"
description: "Exact-Scope native statistics, isolation and qualification."
---

# Desktop Scope statistics

## Inventory and usage view

**Observation → Current-Scope inventory & usage** contains the Statistics view.
Explicitly select the statistics-qualified `sqlite-stats-27404931-v1` profile,
activate the connection, select an exact Scope, choose a UTC usage period
and press **Load statistics**. Loading is explicit; opening the view does not
silently restore a connection or issue a statistics request. The shared shell mounts `Statistics` in the Observation inventory panel with the
current state, language, pending-context guard and state-refresh callback.

The view displays the Server's `as_of`, exact returned Scope coverage and
inclusive UTC dates. Inventory is a current snapshot. Model request/input/output
token counts, purpose breakdowns and daily rows retain returned zero and null
values; null tokens display **Unknown**. The page does not fill missing rows or
infer complete provider reporting from recorded calls. Recall coverage includes
preparation, ready and comparable counts, estimator identity/version, baseline,
recalled tokens and signed reduction. These are estimates, not billing savings
or answer-quality measurements.

Every chart has an equivalent table with captions and row/column headers. Tables
and charts have keyboard-focusable scroll regions for narrow layouts. Labels are
available in English and Chinese. Changing connection, Scope or period clears
the old view and invalidates late success/error responses. A statistics failure
does not disable independent Memory actions.

## Native boundary

Desktop consumes `POST /v1/stats` through the native `get_stats` command. The
renderer supplies only the active generation and `today`, `7d` or `30d`; native
code obtains the current Scope and constructs an exact, single-Scope selection.
There is no renderer-supplied endpoint, Scope list or all/subtree mode.

The connection must have a matching qualified operation profile. Each read
rechecks identity using the existing connection policy, preserves Server
authorization failures, and applies the existing transport timeout and 1 MiB
response bound. A denial never retries against a broader selection. Responses
must retain the requested selection, Scope IDs and period. Aggregate and per-Scope
recall estimators may be null, as permitted by the public contract.

Statistics use their own cancellation channel. A replacement statistics read,
`cancel_stats_reads`, or a generation change cancels pending statistics; Memory
read cancellation does not cancel them. Submitted Memory writes retain their
existing original-target behavior.

The operation catalog and reachable wire models come from
`pnpm --dir desktop generate`; IPC declarations come from the Rust `export_ipc`
example. The native exact-selection projection reuses concrete generated field
types while preserving the nullable estimator and prohibiting broader selections.
Neither Server APIs nor historical qualification records are changed.

See [the qualification record](https://github.com/oceanbase/powercontext/blob/master/desktop/VALIDATION.md#current-scope-statistics-qualification)
for the tested Server/contract/wheel identities and actual validation limits.
Synthetic tests establish local handling; the built-wheel harness separately
establishes the observed SQLite protocol behavior. The record also distinguishes
the exercised macOS ARM64 bundled debug-app journey from Windows installed UI,
Intel, IME and screen-reader gates. The Windows installed journey compares the
page's tables with independent real Server responses.
