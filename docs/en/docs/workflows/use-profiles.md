---
title: Generate and review Scope profiles
description: Use ordinary scopes, subject Source writes, and existing Artifact/Candidate APIs.
---

# Generate and review Scope profiles

A Profile is a complete Markdown snapshot derived from one Scope's evidence, not a source of truth.
User scopes typically describe one person; group scopes retain speaker attribution.
Each Scope has one singleton: `family=profile, artifact_id=profile`.

## Subject Source writes

Supply your business user ID as `subject_key`; `subject_type` defaults to and currently only supports `user`:

```http
POST /v1/scopes/S_GROUP/subject-sources

{"subject_key":"U1","content":{"speaker":"U1","text":"I prefer concise Chinese answers."}}
```

The existing Scope Binding key `subject/user/U1` resolves an ordinary Scope.
If absent, the operation creates a parentless Scope and enables automatic Profile generation.
Alternatively, provide an existing `subject_scope_id`. An explicit ID inconsistent with an existing binding returns 409;
identical business and subject scopes return 422.

The response contains two Sources with the same source_id, content, and digest, different scope_id values, and
independent journal positions. Scope creation, binding, access bootstrap, policy, and both Sources commit together.
Existing Source APIs remain single-scope writes. A new HTTP retry creates a new Source pair; there is no request
idempotency or cross-Scope deletion cascade.

Both actual scopes require contribution permission. Creating a binding additionally requires server.admin;
a newly created Scope grants scope.contributor to the authenticated Principal. This does not grant access to an
existing Scope. subject_key is not an authenticated Principal. Existing rebind/unbind operations do not migrate
history; multiple subjects bound to one Scope mix their evidence.

## Policy and generation

Reusing a Scope does not implicitly change its policy. GET `/v1/scopes/{scope_id}/profile-policy` returns 404 if absent.
Create with expected_version=0; updates require the current version and retain any pending Candidate:

```http
PUT /v1/scopes/S_GROUP/profile-policy

{"generation_enabled":true,"activation_mode":"review_required","expected_version":0}
```

The default activation mode is automatic. Background scheduling is independently opt-in; configuring a generation
model alone does not enable it. When enabled, it runs at 02:00 Asia/Shanghai daily, with an immediate startup
catch-up scan. Configure a generation model and enable scheduling with:

```bash
export POWERCONTEXT_SERVER_RUNTIME_PROFILE_SCHEDULE_ENABLED=true
export POWERCONTEXT_SERVER_RUNTIME_PROFILE_CRON="0 2 * * *"
export POWERCONTEXT_SERVER_RUNTIME_PROFILE_TIMEZONE="Asia/Shanghai"
export POWERCONTEXT_SERVER_RUNTIME_PROFILE_MAX_WORKERS=4
export POWERCONTEXT_SERVER_RUNTIME_PROFILE_MAX_SOURCES_PER_WINDOW=32
```

Automatic cron admission requires a Scope policy with `generation_enabled=true`. Scopes with no policy or a
disabled policy retain their Sources without creating automatic requests or starting Workers. Enabling the policy
makes that existing input eligible for a later cron fire. Already accepted explicit requests retain their authorization
and completion semantics.

Enforced deployments use the existing `POWERCONTEXT_SERVER_ACCESS_BACKGROUND_PRINCIPAL_ID` service identity,
which must have contribution access and write access to existing Profile Artifacts. Local static-administrator
deployments can reuse their existing background identity.

POST `/v1/profile/flush` with `{"scope_id":"S_GROUP"}` processes one bounded window even when scheduling is disabled.
Manual Flush uses the caller's permissions and does not require a background principal. Results are updated, noop,
review_pending, disabled, or conflict. Existing generation timeouts and request limits apply.
Failures retain the cursor; lineage-only Sources are filtered. Unchanged Markdown advances the cursor without
creating an automatic Revision. No new Source means no regeneration.

Windows contain at most 32 raw journal records, or 31 when the previous Profile is also evidence.
Each admitted Scope invocation processes one finite window; remaining ordinary work stays dirty for the next cron opportunity. Multiple instances may duplicate model calls, but
Policy/Cursor/Head checks permit only one committed result.

## Review and manual editing

review_required creates an existing Candidate without changing the committed Profile; pending review blocks later
automatic windows for that Scope. Use existing Candidate Get/List/Revise/Approve/Reject operations:

- Revise changes only proposal.content; preserve source_refs, artifact_refs, and target.
- Approve atomically commits the Profile, closes the Candidate, advances the cursor, and clears the policy pointer.
- Reject requires a reason and consumes the window without automatic retry.
- Manual Replace remains available. A changed Head prevents stale approval but does not prevent rejection.

Use the existing Artifact API to create a Profile:

```http
POST /v1/scopes/S_GROUP/artifacts

{"family":"profile","content":{"content":"# Scope profile\n\n- U1 prefers concise answers."}}
```

GET `/v1/scopes/S_GROUP/artifacts/profile/profile` returns content and an ETag. Another Create conflicts.
PUT the same path with If-Match to replace the Markdown. To restore revision 1, read `.../revisions/1`,
then submit that exact Markdown with the current Head's ETag:

```json
{"content":{"content":"Exact historical Markdown","restored_from_revision":1}}
```

Restoration appends a new Revision; history and cursors never rewind. Generation metadata is server-owned,
not writable input.

To read by subject_key, resolve the exact existing Binding with `allow_default=false`, then use ordinary Scope APIs.
No implicit group traversal or cross-Scope Artifact aggregation occurs. Other Families retain their own generation flows.

Profiles cannot be copied to another Scope using `POST /v1/artifact-publications` or the SDK publication operation.
These requests return HTTP 422 (`artifact_publication_unsupported`, `details.family=profile`), regardless of whether
the target already has a Profile. No target state is created or changed. Generate a Profile from the target Scope's
own Sources, or use its existing Create/Replace API instead. Other supported Artifact Families remain publishable.

## Include the Profile in prepared context

Set `assembly.sections` to `[{"family":"profile","limit":1},{"family":"memory","limit":6}]` in
`POST /v1/context/prepare` to place a committed Profile before relevant Memory. Profile selection reads existing
snapshots and does not generate content. Default prepare requests exclude Profile. See
[Prepare standard context text](prepare-context-text.md#include-profile-snapshots) for Scope order, limits, and plugin configuration.

## Storage and deployment

Only `pc_profile_policies` is new. Profile content and generation metadata use the existing Artifact BLOB;
review context uses the Candidate BLOB. Scope, Binding, Source, Head, Lineage, Cursor, and access tables are reused.
Normal deployments create the policy table through the existing schema initialization without altering existing tables.

Back up any database that used the unpublished Subject Root experiment and validate migration separately.
The service does not delete experimental tables, translate special Profile IDs, or rewrite old payloads automatically.
