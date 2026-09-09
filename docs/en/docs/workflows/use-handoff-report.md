---
title: Use Handoff Report
description: Select Scopes and request a JSON or Markdown Handoff report through the HTTP API.
---

# Use Handoff Report

Handoff Report is a read-only projection of the latest committed Handoff in each selected Scope. It does not create or
edit Scopes or Handoffs.

## Before you start

Start the Server and set its base URL:

```bash
powercontext server run
export POWERCONTEXT_URL=http://127.0.0.1:8000
```

Handoff Report API routes are enabled by default. If bearer authentication is enabled, also set an authorization
header variable and add `--header "$POWERCONTEXT_AUTH_HEADER"` to each request:

```bash
POWERCONTEXT_AUTH_HEADER="Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}"
```

The Server creates a default Scope during startup. Create additional Scopes through an integration or the Scope API.

## 1. Commit a Handoff

Create a durable Handoff milestone in the Scope you want to report. In Codex, follow
[Hand off work in Codex](handoff-with-codex.md). The report reads committed Handoff Revisions only; it does not include
a temporary Prepared Handoff.

## 2. Choose a Scope selection

The API accepts the common Scope selections:

- `{"mode":"all"}` includes every visible Scope.
- `{"mode":"subtree","root_scope_id":"..."}` includes one root Scope and all descendants.
- `{"mode":"exact","scope_ids":["..."]}` includes only the listed Scopes.

Parent relationships express organization only. They do not make a child's Context or Handoff visible to the parent.
A selected Scope without a committed Handoff returns an explicit `no_handoff` result.

## 3. Request JSON or Markdown

Request the canonical JSON projection:

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --data '{"selection":{"mode":"all"},"format":"json"}' \
  "$POWERCONTEXT_URL/v1/handoff-reports/get"
```

Request a Markdown projection for one exact Scope:

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --data "{\"selection\":{\"mode\":\"exact\",\"scope_ids\":[\"${POWERCONTEXT_SCOPE_ID}\"]},\"format\":\"markdown\"}" \
  --output handoff-report.md \
  "$POWERCONTEXT_URL/v1/handoff-reports/get"
```

Both projections carry selection and report digests so consumers can identify the exact generated result.

## Disable Handoff Report

Set the feature flag before restarting the Server:

```bash
export POWERCONTEXT_SERVER_HANDOFF_REPORT_ENABLED=false
powercontext server run
```

Disabling the feature removes the Report API route. HTTP API, MCP, Memory, and Handoff operations remain independently
configured.

For the Scope and Report operations, see [Interfaces](../develop/interfaces.md). For exact Server settings, see
[Configuration](../operate/configuration.md).
