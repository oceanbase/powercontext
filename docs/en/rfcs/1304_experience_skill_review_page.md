- Proposal Name: `experience_skill_review_page`
- Start Date: 2026-08-20
- RFC PR: [oceanbase/powercontext#1304](https://github.com/oceanbase/powercontext/pull/1304)
- Related RFCs: [RFC 0050](0050_artifact_candidate_review_inbox.md),
  [RFC 0051](0051_experience_skill_artifact_families.md),
  [RFC 0072](0072_scoped_statistics_and_usage.md)

# Summary

This RFC adds a Server-owned Review page for Experience and PowerContext-managed Skill Candidates. The page is a
user-facing projection of the existing Candidate and Review lifecycle. It does not create another review model,
change Candidate persistence, or bypass the existing HTTP operations.

PowerContext already exposes a personal Dashboard, configured Dashboard scopes, Bearer authentication, and Review
operations for listing, reading, revising, approving, and rejecting Candidates. The proposed `/reviews` page combines
those capabilities into one scoped Review Inbox. A reviewer can:

1. select one configured scope;
2. filter current Candidate heads by status and Family;
3. inspect the typed Experience or Skill proposal and its exact evidence references;
4. revise the proposal without changing its evidence;
5. approve the exact current version or reject it with a reason; and
6. explicitly publish an approved managed Skill as a configured local Agent Skill package and verify discovery; and
7. recover explicitly when another reviewer changes the Candidate first.

Pending remains the default view. Approved and rejected Candidates are available as read-only views. Experience and
Skill share one page because they share one Candidate lifecycle, while each Family retains its own rendering and edit
form. The first version adds no Candidate generation, evidence-content preview, reviewer identity, RBAC, assignment,
notification, bulk action, or Skill execution capability. Publication is a separate explicit action after approval and
can write only to a host-local Agent target that configuration marks as writable.

# Motivation

Experience and managed Skill are intentionally gated by Review. A generated proposal is untrusted, does not receive
final Artifact identity, and cannot enter retrieval or PreparedContext until a reviewer approves it. This boundary is
already implemented across HTTP, the Python Client, CLI, and MCP.

The Server Dashboard currently shows how many Candidates are pending, but it does not let a user inspect or act on
them. Completing the review requires command-line or MCP calls with exact IDs, versions, proposal shapes, and evidence
references. That is suitable for automation and debugging, but it makes routine human governance hard to discover and
easy to postpone.

Review also differs by Family. An Experience reviewer must judge whether the situation, action, outcome, and lesson form
a reusable conclusion. A Skill reviewer must inspect a name, description, instructions, and validation checklist, while
remembering that approval governs content only and grants no installation or execution authority. A generic JSON editor
would expose the transport shape without helping either decision.

The smallest useful product slice is therefore not a new workflow engine. It is a scoped, structured page over the
existing Review contract:

```text
Dashboard pending count
  -> Review Inbox
  -> select scope and Candidate
  -> inspect typed proposal and exact evidence references
  -> approve | reject with reason | revise then approve
  -> approved Artifact Revision
  -> explicit publish -> standard SKILL.md package -> verified local Registry discovery
```

# Guide-level explanation

## Enter the Review Inbox

When the Dashboard is enabled, the Server navigation contains a **Review** entry next to Dashboard and Handoff Report.
Opening it loads `/reviews` from the same Server origin. It reuses the Dashboard login and Bearer token behavior; the
page does not introduce another credential store or authentication flow.

The reviewer first selects one of the scopes configured by `POWERCONTEXT_SERVER_DASHBOARD_SCOPES`. If no scopes are
configured, the page explains that Review requires at least one Dashboard scope and performs no Candidate request.

Changing the scope clears the current list, selected Candidate, pagination cursor, conflict state, and unsaved revision
draft before loading the new scope. A delayed response from the previous scope must not update the page.

## Work through one unified queue

The page defaults to:

```text
status = pending
family = all
limit = 50
```

The reviewer may choose Experience, Skill, or all Families, and may switch among pending, approved, and rejected
statuses. Changing either filter starts again from the first cursor page. A **Load more** action follows
`next_cursor`; the page does not invent offset pagination or a total count that the API does not provide.

The list and detail pane are shown together on a wide screen and stacked on a narrow screen. Each list row contains only
stable fields available in the Candidate contract:

- Family and status;
- Candidate ID and current version;
- Experience situation and lesson, or Skill name and description; and
- the Candidate reason when present.

The contract has no creation or update timestamp, so the page does not display or sort by a fabricated date. It keeps
the server-provided cursor order.

## Review an Experience

An Experience detail view renders the four typed fields separately:

| Field | Review question |
| --- | --- |
| `situation` | Is the situation specific enough to know when this applies? |
| `action` | Does it describe what was actually done? |
| `outcome` | Does it state the observed result without overstating it? |
| `lesson` | Is the conclusion reusable and supported by the evidence? |

The page also shows the Candidate reason, target Artifact when present, exact Source references, and exact Artifact
references. The first version displays these references as structured identifiers with a copy action. It does not fetch
or render Source bodies because the public HTTP contract does not provide a general exact Source-read operation.

For example, a reviewer may see:

```text
Candidate: cand_exp_123@2
Situation: The OpenAPI source contract changed.
Action: Regenerate the checked-in client and run contract tests.
Outcome: Generated operations and the bundle stayed in sync.
Lesson: Treat contract generation and contract tests as one change.
Evidence: source:task-outcome/run_42
```

The reviewer can approve version 2, reject it with a reason, or open the structured revision form.

## Review a managed Skill

A Skill detail view renders:

- `name`;
- `description`;
- `instructions` as untrusted plain text; and
- each `validation` item as a separate checklist entry.

Instructions are never interpreted as HTML or executed by the page. Approval only creates or replaces a governed Skill
Artifact Revision. After approval, the page switches to that approved Candidate and shows a separate publication area;
publication still requires another reviewer confirmation.

The approved Candidate remains immutable. Its delivery area also offers **Create revision**, which requires a short
change-evidence note and copies the approved Skill content into an editable form. Saving captures the note as bounded
Source evidence and submits a new pending Skill Candidate whose `target` and Artifact evidence identify the exact
approved Skill Revision. It does not modify the approved Revision or published package. The new Candidate must pass
through Review before its resulting Revision can be published as an update.

This distinction is visible beside the approval action:

```text
Approval governs this Skill content. Publication is separate, and publication does not execute the Skill or grant authority.
```

## Publish an approved managed Skill

The publication area appears only for an approved Skill with an exact `result_artifact`. Targets come from Codex or
Claude Code entries in `POWERCONTEXT_SERVER_EXTERNAL_SKILLS` with `allow_managed_publish=true`; the page cannot submit
an arbitrary filesystem path.

The first publication creates a standard package named after the Skill under the selected root. It contains `SKILL.md`
and `powercontext.json`; the latter records the exact Artifact Revision and `SKILL.md` digest. The Server immediately
refreshes the current scope's External Skill Registry. The page reports package Revision and locator/fingerprint-backed
Registry availability separately.

A later approved Revision can explicitly update the same PowerContext-owned package, including a valid name change.
Before updating, the Server verifies the manifest, Artifact identity, Revision, and content digest. It refuses to
overwrite when:

- the destination belongs to an external Skill or cannot be proven to come from the current managed Artifact;
- `SKILL.md`, the manifest, or the package file set was modified locally;
- one root contains multiple projections for the same Artifact;
- a higher Revision is already published; or
- managed content violates the selected Agent's name, description, or package constraints.

Publication does not load or execute instructions and does not bypass Agent discovery, approval, sandbox, tool, or
secret policy. It only places a standard package in the configured local root. The host decides whether a running Agent
needs a new session to observe it.

## Revise before approval

Selecting **Revise** opens a Family-specific form initialized from the current proposal. Experience uses four required
text areas. Skill uses required name, description, and instructions fields plus an ordered validation list.

Saving a revision sends a complete replacement proposal with the current `expected_version`. The first version of the
page preserves the Candidate's existing Source references, Artifact references, target, and reason exactly. It does not
provide a general evidence or lineage editor. A reviewer who needs to change lineage must use an existing programmatic
surface or create a new Candidate through the owning generation flow.

A successful revision produces the next immutable pending version. The page then renders that returned version. It does
not approve automatically; the reviewer must inspect and approve the revised content as a separate action.

## Approve or reject

Approve requires a short confirmation that identifies the Candidate and version. The page sends no content changes with
approval. On success, the Candidate becomes approved and identifies the exact result Artifact.

Reject requires a non-empty reason of at most 2,000 characters. On success, no Artifact is written and the Candidate
becomes terminal.

After a decision, the page switches to the returned terminal filter and keeps the decided Candidate selected. An
approved Skill can therefore create a successor Candidate or continue to explicit publication. Approved Experience
and rejected Candidate content remains read-only, and the page cannot reopen a terminal Candidate.

## Handle concurrent review explicitly

Every revise, approve, and reject request uses the version currently displayed in the detail pane. If the Server returns
a Candidate or Artifact conflict, the page does not retry the write and does not merge content automatically.

It fetches the current Candidate head and explains that another write won the race. For an approval or rejection, the
reviewer must inspect the new version before acting again. For a revision, the page retains the unsaved local text until
the reviewer either discards it or manually applies it to the new current proposal.

# Reference-level explanation

## Goals and non-goals

The first version has these goals:

- make the existing Experience and managed Skill Review lifecycle usable from the Server UI;
- keep scope selection explicit and limited to configured Dashboard scopes;
- render each Family as a reviewable domain object rather than generic JSON;
- preserve exact Candidate-version and target CAS behavior;
- keep untrusted content inert and keep approval separate from execution authority;
- let an approved managed Skill be explicitly published to a configured local Agent target with package and Registry verification;
- support English and Chinese, keyboard use, narrow screens, and the existing light and dark themes; and
- remain a thin projection over the current OpenAPI contract.

The following are out of scope:

- generating, incubating, importing, or forking Candidates;
- reviewing Memory or Handoff;
- editing Candidate evidence, target, lineage, or generation reason;
- rendering Source content or arbitrary Artifact evidence previews;
- automatic publication, arbitrary-path export, Skill execution, runtime hot loading, or rollback;
- reviewer identity, RBAC, SSO, assignment, notifications, service-level targets, and bulk actions;
- Candidate retention, reopening, deletion, semantic diff, or version-history browsing;
- a generic form renderer for future Artifact Families; and
- a new frontend framework or standalone web application.

## Existing foundation

The design reuses current Server behavior:

| Existing surface | Use on the Review page |
| --- | --- |
| `GET /dashboard/scopes` | List the scopes deliberately exposed by Server configuration |
| `POST /v1/artifact-candidates/list` | Page current heads by scope, status, Family, and cursor |
| `POST /v1/artifact-candidates/get` | Refresh one current Candidate head |
| `POST /v1/artifact-candidates/revise` | Append one complete replacement proposal |
| `POST /v1/artifact-candidates/approve` | Approve the exact displayed version atomically |
| `POST /v1/artifact-candidates/reject` | Reject the exact displayed version with a reason |
| managed Skill exact read and Agent projection helper | Read an approved Revision and render a standard `SKILL.md` package |
| `POST /dashboard/skill-projections/status` | Inspect package Revision, integrity, and Registry state in configured targets |
| `POST /dashboard/skill-projections/publish` | Explicitly create or safely update a package, then refresh the scoped Registry |
| Dashboard authentication utilities | Send the existing Bearer token to same-origin requests |
| Dashboard page UI utilities | Reuse locale, theme, status, and stale-request handling patterns |

No OpenAPI change, generated client change, database migration, or new public persistence contract is required. Like
`/dashboard/scopes`, the two `/dashboard/skill-projections/*` endpoints are authenticated Server UI supporting surfaces.
They operate on explicitly configured roots on the Server host, are not a cross-host PowerContext API, and never accept
a caller-provided path. Portable exact reads remain on the public `get_skill` contract, and CLI export remains available.

## Page availability and routing

The Review page is part of the personal Dashboard feature:

- route: `GET /reviews`;
- availability: mounted only when `DashboardConfig.enabled` is true;
- scopes: the same ordered `DashboardConfig.scopes` used by the statistics Dashboard;
- authentication: the same Server Bearer policy and same-origin request helper; and
- navigation order: Dashboard, Review, Handoff Report when all three are available.
- publication targets: only explicit `AgentSkillTarget` entries with `allow_managed_publish=true`; legacy
  `CodexSkillRoot` entries remain a Codex-only compatibility form, and no target is writable by default.

Disabling the Dashboard removes both the Dashboard and Review routes. Handoff Report may remain independently
available under its existing configuration.

The Review page does not accept an arbitrary `scope_id` from a query parameter in the first version. It selects the
first configured scope initially and lets the reviewer switch through the configured picker. This avoids presenting
scope-shaped input as authorization and avoids a deep link that may expose an unconfigured scope.

## Page state and request ordering

The page maintains these client-side values:

```text
authentication state
configured scopes
selected scope
selected family filter
selected status filter
Candidate rows and next cursor
selected Candidate ID and current head
optional revision draft
optional managed Skill projection state and selected publication root
optional conflict or request error
```

Scope changes cancel or invalidate every in-flight list, detail, and decision response and reset all Candidate state.
Filter changes invalidate list and detail responses and reset pagination. Selecting a row fetches its current head before
enabling a write action, so a stale row does not become an immediate approval request.

Only one decision or publication request may be active for the selected Candidate. Its write controls are disabled while
it runs. A delayed success from an earlier selection, scope, or Artifact Revision must not update the new selection.

## List, pagination, and selection

The list request is:

```json
{
  "scope_id": "project:powercontext",
  "status": "pending",
  "family": null,
  "cursor": null,
  "limit": 50
}
```

`family` is omitted or `null` for the combined queue and is `experience` or `skill` for a Family filter. The page appends
rows only when a **Load more** response belongs to the same scope, filters, and request generation. Candidate ID is the
row key; version changes replace the current row rather than creating a duplicate.

After a pending decision, the page switches to the returned terminal status and reselects the same Candidate in the
refreshed list. This lets an approved Skill continue to publication while a rejected Candidate remains available for
decision-reason verification. The scope is unchanged, and the refreshed list remains authoritative.

## Family-specific rendering and editing

The page dispatches on the closed current Family set:

| Family | Summary | Detail and revision fields |
| --- | --- | --- |
| Experience | `situation`, then `lesson` | `situation`, `action`, `outcome`, `lesson` |
| Skill | `name`, then `description` | `name`, `description`, `instructions`, ordered `validation` |

The implementation must reject an unknown Family or a proposal shape that does not match its Family. It shows an
unsupported-content error and disables all decision actions. It must not guess a generic form and submit data it cannot
validate.

Revision uses the limits already enforced by the public contract:

- each Experience field is required and at most 8,000 characters;
- Skill name is at most 128 characters;
- Skill description and each validation item are at most 2,000 characters;
- Skill instructions are at most 32,000 characters; and
- Skill validation contains 1 through 32 non-empty items.

Client validation improves feedback but does not replace Server validation. A `422` response is displayed beside the
form without changing the current Candidate head.

## Evidence and trust boundary

Candidate proposal, reason, rejection reason, instructions, and reference identifiers are untrusted data. The page:

- inserts them through text nodes or form values, never `innerHTML`;
- does not render Candidate Markdown or load remote resources named by Candidate content;
- does not execute instructions or convert them into links;
- does not log proposal bodies, reasons, or evidence identifiers from browser code; and
- retains the Server's existing restrictive Content Security Policy.

Source and Artifact references are displayed as exact structured values. The page does not infer local paths, URLs,
permissions, or availability from an identifier. `scope_id` remains a business partition, not an ACL. Listing a scope in
Dashboard configuration controls UI discovery only; Server authentication and deployment policy remain responsible for
access control.

Pending and rejected content remains excluded from Artifact discovery and PreparedContext. The page never calls an
approved Artifact read as a substitute for reviewing a pending Candidate.

## Review actions and concurrency

The UI maps actions to the existing lifecycle:

```text
pending version N --revise(expected=N)--> pending version N+1
pending version N --approve(expected=N)-> approved + exact result Artifact
pending version N --reject(expected=N)--> rejected + decision reason
approved Skill Revision --create revision-> new pending Candidate targeting that exact Revision
approved Skill Revision --publish(target_id)-> exact Agent-local package + refreshed Registry
```

Approve and reject are available only for a current pending head. Revise is available only for a current pending head
with a supported Family shape. Approved and rejected heads remain read-only; creating a Skill revision produces a new
Candidate rather than reopening or mutating the terminal head.

For `409 Conflict`:

1. stop the attempted transition;
2. keep an unsaved revision draft in page memory when one exists;
3. fetch the Candidate current head;
4. show the old and new version numbers and the conflict category returned by the Server; and
5. require a new explicit reviewer action.

The page never changes `expected_version`, retries, approves, or merges automatically after a conflict.

## Publication action and overwrite boundary

A publication status request selects an exact approved ArtifactRef:

```json
{
  "scope_id": "project:powercontext",
  "candidate_id": "cand_123",
  "artifact": {"family": "skill", "artifact_id": "skill_123", "revision": 2}
}
```

The Server first verifies that the Artifact is the exact `result_artifact` of the identified approved Skill Candidate.
It then returns targets only for configured Dashboard scopes and Agent targets that allow managed publication. Each
target carries `target_id`, `agent_kind`, and installation scope, plus a stable package state: `unpublished`, `current`,
`update_available`, `conflict`, `drifted`, or `incompatible`.
Discovery is reported independently as `available`, `unavailable`, or `not_published`.

A publish request adds `target_id`, never an Agent kind or destination path supplied by the browser. The Server resolves
both from configuration, reads the exact approved Skill again, rechecks filesystem state, and stages within the same
target. An existing projection can be moved aside and replaced only when its
manifest identity and digest match. A failure restores the previous package. Repeating publication of the same Revision
is idempotent but still refreshes the Registry. If file or Revision state changed, the endpoint returns `409`; the page
reloads status without broadening overwrite authority.

The operation manages only the generated `SKILL.md` and `powercontext.json`. Managed content does not carry arbitrary
scripts, references, or assets in this version, so extra package files count as drift and are never deleted.

## Loading, empty, and failure states

The page distinguishes:

| State | Behavior |
| --- | --- |
| No configured scopes | Explain the Dashboard scope configuration requirement; send no Candidate request |
| Empty filtered page | Explain which scope, status, and Family have no Candidates |
| Loading list | Keep filters visible and mark the list busy |
| Loading detail | Keep the selected row visible and mark the detail pane busy |
| `401` | Clear the stored tab token and return to the existing login screen |
| `404` on detail | Remove the stale row and refresh the current filtered page |
| `409` | Follow the explicit conflict flow and perform no automatic write |
| `422` | Preserve the form and show validation feedback |
| No publication root | Keep the approved Skill readable and explain that an explicit writable target is required |
| Projection conflict or drift | Block publication, preserve the directory, and show a safe error |
| Current package but unavailable Registry binding | Allow an explicit discovery refresh without rewriting content |
| `503` or network failure | Preserve scope and filters; provide an explicit retry |

A list failure must not erase a previously rendered list from another scope and make it appear current. Stale content is
hidden as soon as scope changes.

## Accessibility, localization, and responsive behavior

English and Chinese strings ship together. Family and status values are translated for display but submitted using
their stable API values. Candidate content and identifiers are never translated.

The page supports:

- a logical heading order and a named primary navigation region;
- explicit labels and error associations for every form control;
- keyboard list selection and visible focus states;
- focus return to the next row after a decision;
- an announced status region for successful decisions, validation errors, and conflicts;
- native buttons and form controls instead of clickable generic containers;
- no color-only distinction among statuses; and
- a stacked list/detail flow on narrow screens without hiding review fields or actions.

Theme and locale use the existing Server page utilities. The page does not create Review-specific preference storage.

## Implementation slices

Implementation should proceed as five reviewable slices:

1. **Read-only Inbox**: route, navigation, authentication, scope picker, filters, pagination, list, and typed detail;
2. **Decisions**: approve and reject with expected-version confirmation and pending-list advancement;
3. **Revision and conflict**: Family forms, complete replacement proposal, local draft preservation, and explicit `409`
   recovery; and
4. **Managed Skill publication**: explicit root allowlist, status, safe create/update, manifest integrity, and Registry refresh; and
5. **Product hardening**: English/Chinese parity, responsive behavior, accessibility, packaging, and browser tests.

Each slice uses the real Server endpoints. Mocked unit tests may cover rendering helpers, but they do not replace an
acceptance scenario that persists a Candidate, loads it through the page, performs a decision, and verifies the
resulting Candidate and Artifact state.

## Acceptance

| Scenario | Passing condition |
| --- | --- |
| Availability | `/reviews` exists only when the Dashboard is enabled and appears in primary navigation |
| Authentication | The existing optional Bearer flow protects page data and handles `401` without another token store |
| Scope isolation | Switching scopes clears rows, detail, cursor, conflicts, and drafts before another response renders |
| Default Inbox | The first request lists pending Experience and Skill current heads for the first configured scope |
| Filtering | Family or status changes restart pagination and never mix rows from different filters |
| Pagination | Load more follows `next_cursor`, preserves server order, and deduplicates by Candidate ID |
| Experience | The four typed fields, reason, target, and exact evidence references are readable |
| Skill | Name, description, instructions, validation, reason, target, and exact evidence references are readable |
| Revise | A complete replacement proposal creates version N+1, preserves lineage fields, and remains pending |
| Approve | Only the exact current version succeeds; the response identifies the committed Artifact |
| Reject | A non-empty reason produces a rejected terminal Candidate and no Artifact |
| Decision continuation | A decision switches to the returned terminal view and reselects the same Candidate |
| Conflict | A stale write is not retried; the current head is loaded and any local revision draft is preserved |
| Successor revision | An approved Skill can seed a new pending Candidate targeting its exact current Artifact Revision |
| Publish target | The page lists only roots with `allow_managed_publish=true` and accepts no arbitrary path |
| First publication | An exact approved Revision creates standard `SKILL.md` and manifest files and is Registry-available |
| Safe update | A later Revision updates only an identity/digest-matching PowerContext-owned package |
| Drift and conflict | Foreign directories, local modifications, duplicate projections, and Revision rollback are not overwritten |
| Trust boundary | Candidate text stays inert; approval does not publish, and publication grants no Skill execution authority |
| Terminal views | Terminal content is read-only; approved Skill can create a successor Candidate or publish its exact result |
| Accessibility | Core review, revision, and decision flow is usable with keyboard and announced to assistive technology |
| Responsive UI | The same fields and actions remain available in the stacked narrow-screen layout |
| Localization | English and Chinese cover the same states, actions, errors, and authority warnings |
| Packaging | Templates and static assets are present in the built wheel and work from the installed Server |

The implementation pull request must run `make check`, `make test`, and `make docs-test`, plus focused Server-page tests
and a real browser flow covering both Families, scope switching, all three decisions, a stale-version conflict, an
approved Skill successor Revision and publication update, optional authentication, both locales, and a narrow viewport.

# Drawbacks

- The page adds another Server-owned JavaScript state machine and duplicates some authentication, scope, and status
  patterns already used by Dashboard and Handoff Report.
- Exact references without Source-body preview limit how much evidence a reviewer can inspect in one screen.
- A unified Inbox needs Family-specific rendering and validation branches even though the lifecycle is shared.
- Keeping evidence immutable in the first revision form means some corrections still require CLI, MCP, or a new
  Candidate.
- The page improves access to governance but does not provide reviewer attribution, authorization separation, or an
  organizational audit log.
- Host-local publication affects the Server process host; a remote browser does not publish to its own device.

# Rationale and alternatives

| Option | Decision |
| --- | --- |
| Keep Review in CLI and MCP only | Rejected; the Dashboard exposes pending work without a human completion path |
| Build separate Experience and Skill pages | Rejected; it duplicates one lifecycle and fragments one scoped queue |
| Use one unified Inbox with typed Family details | **Adopted**; it shares navigation and actions without hiding domain shape |
| Render and edit arbitrary Candidate JSON | Rejected; it exposes transport details and makes unsafe submissions easier |
| Add a Review-specific backend or persistence table | Rejected; the existing OpenAPI and Candidate store already own the lifecycle |
| Replace Server pages with a new SPA framework | Rejected for this slice; the current packaged HTML/static model is sufficient |
| Add evidence-body reads to this RFC | Deferred; general Source reading needs its own trust, retention, and authorization contract |
| Support bulk approval | Rejected for the first version; each Candidate requires content and evidence judgment |
| Publish automatically after approval | Rejected; content governance and host filesystem mutation require separate authorization |
| Let the browser submit any destination | Rejected; root IDs keep the Dashboard from becoming an arbitrary file-write API |

Not implementing the page leaves the governance contract technically complete but operationally hidden. Reviewers can
still use existing programmatic surfaces, but pending Experience and Skill are more likely to accumulate or be approved
without an ergonomic structured inspection flow.

# Prior art

- RFC 0050 defines the Family-neutral Candidate lifecycle, expected-version writes, terminal states, and Review Inbox
  query model. This RFC presents that contract without changing it.
- RFC 0051 defines Experience and managed Skill proposal shapes, lineage, and the boundary between Skill approval and
  execution authority. This RFC gives those shapes separate review views.
- RFC 0072 and the existing Dashboard establish configured scope discovery, scoped pending counts, authentication,
  localization, theme, and Server-owned static delivery.
- The Handoff Report page demonstrates that a focused workflow can share Server navigation and page utilities without
  becoming part of the statistics Dashboard itself.

No external review product is adopted as a protocol or compatibility target. The design follows PowerContext's current
Candidate contract rather than copying issue-tracker or code-review semantics that do not have the same Artifact gate.

# Unresolved questions

No unresolved question blocks the first version. The following decisions are intentionally deferred:

- whether a future exact Source-read contract can safely support evidence previews;
- whether reviewer identity and decision attribution belong in Candidate persistence or a separate audit stream;
- whether a stable Candidate deep-link contract is useful after authorization and scope discovery are stronger; and
- whether repeated review volume justifies assignment, notification, or bulk triage without bulk approval.

# Future possibilities

Natural extensions include:

- safe, bounded evidence previews backed by an explicit exact-read and redaction contract;
- Candidate version history and a semantic diff between the generated and revised proposal;
- reviewer identity, decision attribution, RBAC, assignment, notification, and service-level reporting;
- URL-addressable Candidate details after scope authorization is explicit;
- saved filters and queue triage for large installations;
- governed rollback, unpublish, and cross-host publication receipts; and
- read-only links from Dashboard pending counts directly to the corresponding Review filter.

These extensions must preserve the central boundary: a Candidate is untrusted until approval, and approving managed
Skill content never grants installation or execution authority.
