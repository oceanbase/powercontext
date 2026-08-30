---
title: Use Handoff Report
description: Open the Server report, select a scope, inspect Handoff history, and save a Revision.
---

# Use Handoff Report

Handoff Report presents committed Handoff Revisions in a Server-owned web page. Use it to inspect current work by
scope, save a complete Handoff snapshot, or request a Markdown projection.

## Before you start

Start the Server:

```bash
powercontext server run
```

Handoff Report is enabled by default at `http://127.0.0.1:8000/handoff-reports`. It uses the Server listener and
authentication settings, but does not require the statistics Dashboard or its scope list. If bearer authentication is
enabled, enter the configured token in the page sign-in form.

In the default unauthenticated mode, initial and manual report loads work without a token. The current browser code
requires a stored bearer token for background refresh and Markdown download, so those two controls do not issue report
requests until authentication is enabled and the page has a token.

The page discovers scopes that contain at least one committed Handoff. Without one, it shows a data-free template
preview and disables search, period filters, editing, and download.

## 1. Commit a Handoff in the target scope

Create a durable Handoff milestone in the scope you want to report. In Codex, follow
[Hand off work in Codex](handoff-with-codex.md) and keep the exact scope and Handoff Revision from the result.

Reload Handoff Report after the commit succeeds. The page calls `list_handoff_report_known_scopes` and should include
the scope. Committing a Handoff makes the scope discoverable; no Report Project or Workstream registration is required.

## 2. Select a scope

Search by `scope_id`, then select the scope. The page requests its report with `scope_id` and shows the current
objective, state, disposition, next action, and known omissions.

The report also shows the latest Handoff history, newest first. The JSON projection contains at most the latest 20
Revision summaries and marks when earlier history was truncated. The HTTP request schema retains an optional
`project_id` field for wire compatibility; it is deprecated and ignored when the Server generates a scope report.

The page starts a five-second refresh timer, but background requests currently run only when a bearer token is stored.
With authentication disabled, use **Refresh** to load changes manually. Background refresh also pauses while edits are
unsaved or a Handoff action is running.

## 3. Save a new Handoff Revision

Select **Edit**, update the five current-snapshot fields as one document, then select **Save Revision**. The Server
prepares and commits the complete document as a new immutable Handoff Revision.

Saving is a write operation. Scope switching stays paused while the editor is open. The page does not record receiver
acceptance; acknowledgements and Task Outcomes remain read-only entries in the **Continuity timeline**.

## 4. Understand the current period controls

The current scope report accepts and normalizes day, week, month, or custom period input, but Activity integration is
not configured. It returns no Activity events, reports `activity_coverage: not_configured`, and does not produce a
previous-period comparison.

Do not use the period controls to infer historical work or compare Activity yet. The Handoff snapshot is the current
exact selection, not a reconstructed state at the end of the chosen period.

## 5. Download Markdown

With bearer authentication enabled and a token stored in the page, select **Download Markdown** to export the same
scope, locale, and normalized period. The browser requests Markdown from the Server rather than rebuilding it from the
rendered page. Downloads enable evidence checks by default and use the filename `handoff-report.md`.

In the default unauthenticated mode, the current browser guard does not send this download request. The underlying HTTP
operation and Python Client remain available without a token while Server authentication is disabled.

## Disable Handoff Report

Set the feature flag before restarting the Server:

```bash
export POWERCONTEXT_SERVER_HANDOFF_REPORT_ENABLED=false
powercontext server run
```

Disabling the feature removes `/handoff-reports` and the Report API routes. The Dashboard, HTTP API, MCP, Memory, and
Handoff operations remain independently configured.

For scope discovery and Report operations, see [Interfaces](../reference/interfaces.md). For exact Server settings,
see [Configuration](../reference/configuration.md).
