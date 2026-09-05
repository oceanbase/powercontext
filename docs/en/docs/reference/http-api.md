---
title: HTTP API
description: Call the PowerContext Server over HTTP and find the complete OpenAPI contract.
---

# HTTP API

The HTTP API is the language-neutral interface to a running PowerContext Server. The default base URL is
`http://127.0.0.1:8000`.

If you are integrating PowerContext into your own AI application rather than looking up one field, start with the
[HTTP API lifecycle tutorial](../tutorials/api-quickstart.md). This page remains the path, contract, and
error-semantics reference.

## Discover the contract

With a local unauthenticated Server running, open `/docs` for the interactive Scalar API reference or
`/openapi.json` for the contract served by that process.

The checked-in source of truth is
[`openapi/powercontext.yaml`](https://github.com/oceanbase/powercontext/blob/master/openapi/powercontext.yaml).
Use it when generating a client or reviewing every request and response field. `/docs` remains public when Server
authentication is enabled so it can render the reference; requests made from it still require authentication.
`/openapi.json` requires the bearer token. A browser address bar cannot add that header, so use a trusted proxy or
browser setup that injects it, or download `/openapi.json` with an authenticated command. Never put the token in the
URL.

## Authenticate requests

Authentication is disabled for the default loopback-only installation. When the operator enables it, include this
header on API and MCP requests:

```http
Authorization: Bearer <token>
```

The examples below use an optional shell variable:

```bash
POWERCONTEXT_URL=http://127.0.0.1:8000
POWERCONTEXT_AUTH_HEADER="Authorization: Bearer ${POWERCONTEXT_CLIENT_API_TOKEN}"
```

Omit `--header "$POWERCONTEXT_AUTH_HEADER"` when authentication is disabled. The `/health/live` and
`/health/ready` endpoints are always public. See [Deploy the Server](../how-to/deploy-server.md) before allowing remote
access.

For an authenticated Server, download the exact contract served by that process with:

```bash
curl --fail \
  --header "$POWERCONTEXT_AUTH_HEADER" \
  --output powercontext-openapi.json \
  "$POWERCONTEXT_URL/openapi.json"
```

## Store and search one Memory

Set `POWERCONTEXT_SCOPE_ID` to an existing ID returned by `create_scope`. Reuse that Scope across sessions; a Session
ID is not a durable project identity.

Store one already-curated Memory entry:

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --header "$POWERCONTEXT_AUTH_HEADER" \
  --data "{
    \"scope_id\": \"${POWERCONTEXT_SCOPE_ID}\",
    \"kind\": \"decision\",
    \"text\": \"Keep the public API asynchronous.\"
  }" \
  "$POWERCONTEXT_URL/v1/memory/remember"
```

The response contains an exact citation. Keep that citation when a later request must revise, retire, or read that
specific immutable revision.

Search active entries in the same scope:

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --header "$POWERCONTEXT_AUTH_HEADER" \
  --data "{
    \"scope_id\": \"${POWERCONTEXT_SCOPE_ID}\",
    \"query\": \"public API\",
    \"limit\": 5
  }" \
  "$POWERCONTEXT_URL/v1/memory/search"
```

## Grant one logical Handoff to a receiver

`scope_id` never grants access by itself. The Handoff owner or an authorized delegator assigns one logical committed Handoff by creating a
Binding for the receiver's authenticated Principal:

```bash
curl --fail \
  --request POST \
  --header 'Content-Type: application/json' \
  --header "$POWERCONTEXT_AUTH_HEADER" \
  --data '{
    "subject": {"type": "user", "id": "idp:user-b", "description": "User B"},
    "resource": {
      "type": "artifact",
      "scope_id": "project:example",
      "identity": {"family": "handoff", "artifact_id": "handoff-42"},
      "selector": null
    },
    "role": "handoff.receiver",
    "idempotency_key": "handoff-42-to-user-b"
  }' \
  "$POWERCONTEXT_URL/v1/access/bindings/create"
```

The receiver can read and acknowledge the Handoff's history, current Revision, and future Revisions. Continue exposes
the citations in the selected Revision's immutable manifest and checks those cited resources without requiring a
second Binding for each citation. This manifest-scoped inspection does not authorize generic Source, Memory, or
Artifact endpoints: the receiver still cannot discover another Handoff or read the parent scope unless a separate
scope or Artifact role allows it. It may request `latest` only for the bound logical Handoff. Use `/v1/access/me` to
verify which Principal the deployment established, `/v1/access/check` for one compound `all` or `any` requirement, and
`/v1/access/resources/list` for a non-discovering list of already visible resources. Creation is idempotent per
grantor and key; revocation uses `binding_id` plus `expected_version`. An atomic `/v1/access/bindings/replace`
revokes one immutable Binding and creates its successor with the same Resource and role. Role descriptors expose
whether they allow `many_per_resource` or `one_per_resource` active Bindings. Relationship and decision events are
available to Server administrators through `/v1/access/audit/list`. When authentication establishes delegated execution,
each audit event keeps the effective `principal` and the trusted `actor` as separate opaque identities.

The Access wire contract has only three Resource Kinds: `server`, `scope`, and `artifact`. An Artifact Resource uses
the logical identity `{family, artifact_id}` and deliberately contains no Revision. Memory can narrow a grant with a
`memory_entry` selector containing only `entry_id`. Unknown Families, `prompt` when no Prompt lifecycle is implemented,
and mismatched selectors or roles never create a Binding. `/v1/access/me` reports the current mode, Provider
capabilities, and each Artifact Family's enabled state.

Cross-Scope Artifact publication uses `POST /v1/artifact-publications`. The request selects an exact source Revision,
but authorization checks `artifact.share` on its logical `{family, artifact_id}` identity and `scope.admin` on the
target Scope. Consequently, one logical sharing grant covers earlier and later source Revisions while every
publication still records the exact copied Revision and its provenance. Host-local Dashboard projection remains an
operational surface protected by the corresponding Scope and Artifact checks.

The standard Skill lifecycle uses the same Access boundary. Library listing requires `scope.read`; lifecycle changes
require `artifact.write`; package manifest/download requires `artifact.read`; package proposals require
`scope.contribute` and, when replacing an existing Skill, `artifact.write`; usage capture requires both
`scope.contribute` and `artifact.read`. Remote target administration requires `scope.admin`, while publishing an exact
Revision also requires `artifact.read` for that Skill. The enrollment endpoint is protected by its one-time code, and
Receiver reconcile/download/receipt endpoints use the separately issued `TargetBearerAuth` credential instead of a
user Principal. Dashboard data routes apply the corresponding Access checks before scope lookup, package inspection,
target lookup, or filesystem work.

The built-in static token represents one local administrator and cannot model different A/B users. A real multi-user
deployment must authenticate each caller to a different Principal and inject an Authorization Provider. HTTP and MCP
use the same policy enforcement point; MCP tool visibility is not permission.

## Find an operation

| Area | Main paths | Purpose |
| --- | --- | --- |
| Health and capabilities | `/health/*`, `/v1/capabilities` | Probe the deployment and discover enabled runtime behavior |
| Access Control | `/v1/access/*` | Inspect identity, check decisions, and administer roles, Bindings, and audit events |
| Source and context | `/v1/sources/content`, `/v1/context/prepare` | Capture evidence and prepare bounded context |
| Work continuity | `/v1/work/*` | Create work contracts, prepare or acknowledge Handoffs, and record outcomes |
| Low-level Handoff | `/v1/handoff/*` | Activate, prepare, finalize, commit, or continue a Handoff |
| Memory | `/v1/memory/*` | Flush, remember, search, list, get, revise, retire, and inspect changes |
| Experience and Skill | `/v1/experience/*`, `/v1/skill/*`, `/v1/skills/*` | Propose, review, package, govern, distribute, and read managed Skill revisions |
| Review | `/v1/artifact-candidates/*` | List, inspect, revise, approve, or reject pending Candidates |
| External Skills | `/v1/external-skills/*` | Scan configured targets and resolve or import packages |
| Handoff Reports | `/v1/handoff-reports/*` | Generate a read-only report for a Scope selection |
| Statistics | `/v1/stats` | Read scoped usage statistics |

The OpenAPI contract defines the complete path list, schemas, limits, and status codes. The higher-level workflow and
Python examples are in [Interfaces](interfaces.md).

## Handle errors and concurrent changes

Errors use one JSON envelope:

```json
{
  "error": {
    "code": "invalid_request",
    "message": "The request violates the API contract.",
    "details": {}
  }
}
```

Common statuses are:

| Status | Meaning |
| --- | --- |
| `401` | The Server requires a valid bearer token |
| `403` | The authenticated Principal is not authorized for the requested action and resource |
| `404` | The requested immutable value does not exist |
| `409` | The request conflicts with current immutable state or an expected version |
| `413` | A selected Handoff Report exceeds its output limit |
| `422` | The JSON body violates the transport or application contract |
| `503` | A required Runtime binding or dependency is unavailable |
| `500` | The Server failed without exposing internal details |

Every response includes `X-PowerContext-Request-ID`; record it when diagnosing a failed call. Preserve exact citations
for Memory revision and retirement. Candidate review writes require the current `expected_version`; after a `409`, read
the Candidate again before deciding whether to retry.
