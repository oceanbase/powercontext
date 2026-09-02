---
title: Use Handoff Report
description: Select a Scope view, inspect current Handoffs, and download a Markdown report.
---

# Use Handoff Report

Handoff Report is a read-only view of the latest committed Handoff in each selected Scope. It does not create or edit
Scopes or Handoffs.

## Before you start

Start the Server:

```bash
powercontext server run
```

Handoff Report is enabled by default at `http://127.0.0.1:8000/handoff-reports`. It uses the Server listener and
authentication settings, but does not require the statistics Dashboard to be enabled. If bearer authentication is
enabled, enter the configured token in the page sign-in form.

The Server creates a default Scope during startup. Additional Scopes appear after they are created through an
integration or the Scope API. A Scope does not need a committed Handoff to appear in the report.

## 1. Commit a Handoff

Create a durable Handoff milestone in the Scope you want to report. In Codex, follow
[Hand off work in Codex](handoff-with-codex.md). The Codex integration writes to the Scope bound to the current Session.

The report reads committed Handoff Revisions only. A temporary Prepared Handoff is not included.

## 2. Select a Scope view

Open Handoff Report and choose one of the shared Scope selections:

- **All** includes every Scope visible to this Server.
- **Scope and descendants** includes one root Scope and all of its descendants.
- **Focus** includes exactly one Scope.

Parent only expresses organization. It does not make a child's Context or Handoff visible to the parent, so each row
shows only that Scope's own latest Handoff. A selected Scope without a committed Handoff is shown as **No Handoff**.

Use **Refresh** after a Handoff or Scope changes.

## 3. Read or download the report

The page shows the selected Scopes, their parent relationship, Handoff status, objective, next action, and exact
Revision address. Summary counts use the same frozen selection as the rows.

Select **Download Markdown** to request the Markdown projection from the Server. The browser does not rebuild the
report from the rendered page. The JSON and Markdown projections carry selection and report digests so a consumer can
identify the exact generated result.

## Disable Handoff Report

Set the feature flag before restarting the Server:

```bash
export POWERCONTEXT_SERVER_HANDOFF_REPORT_ENABLED=false
powercontext server run
```

Disabling the feature removes `/handoff-reports` and the Report API route. The Dashboard, HTTP API, MCP, Memory, and
Handoff operations remain independently configured.

For the Scope and Report operations, see [Interfaces](../reference/interfaces.md). For exact Server settings, see
[Configuration](../reference/configuration.md).
