- Proposal Name: `dashboard_profile_and_handoff_markdown_export`
- Start Date: 2026-09-16
- RFC PR: [#1629](https://github.com/oceanbase/powercontext/pull/1629)
- Design Baseline: [master / 534460e0](https://github.com/oceanbase/powercontext/tree/534460e068ef733ec22a672b838734635fa1677e).
- Related RFCs: [Handoff Artifact](0048_handoff_artifact.md), [Handoff Report](0082_handoff_report.md),
  [Source and Artifact REST API](1437_source_artifact_rest_api.md), [Profile Artifact](1485_profile_artifact.md).

# Summary

Add a read-only Profile page to the PowerContext Dashboard and Markdown downloads for individual Handoffs in the
existing collection and detail pages. Profiles support current content, exact historical revisions, and evidence
inspection. Handoff downloads preserve the selected immutable revision, complete content, known omissions, and
citations. The implementation reuses Scope and Artifact APIs, authentication, and the Tabler/Jinja2/HTMX architecture.
It does not create another artifact store or ask a model to summarize content again.

The baseline already includes `/dashboard/prompts` and `/dashboard/topics`. Prompts expose scoped configuration;
Topic Memory supports browsing, search, and exact revision reads. These remain existing capabilities, outside the
new implementation scope of this RFC.

# Motivation

The Dashboard can read memories, experiences, skills, handoffs, prompts, and topic memories, but has no page for a
Scope's saved Profile. Inspecting profile content, provenance, or historical revisions still requires direct API calls.

Handoff details show objectives, state, next actions, and omissions but cannot download Markdown. Copying the page
manually can lose citations and exact identity; abbreviated collection previews cannot substitute for complete content.

`POST /v1/handoff-reports/get` already downloads Markdown, but selects each Scope's latest committed Handoff.
New Handoff writes use a Scope singleton, while detail links can select historical revisions. Reading
`handoff/handoff@3` must download revision 3 even after revision 4 appears. The latest-report endpoint cannot implement that promise.

## Goals and non-goals

- Read the current Scope's Profile, historical revisions, and associated evidence.
- Download complete, deterministic Markdown for the exact Handoff selected in a collection or detail page.
- Distinguish absent content, read errors, historical content, and failed downloads.
- Preserve collection return context and recover exact reading destinations after authentication.
- Preserve existing Prompt and Topic Memory routes, layouts, search, and pagination without duplicate entry points.
- Exclude profile editing, rollback, regeneration, review, and processing policy management.
- Exclude an all-artifacts browser, cross-Scope aggregation, batch downloads, PDF, public sharing, imports, and a new
  machine-readable Agent handoff contract.

# Guide-level explanation

## Navigation and scope

Add Profile while retaining the relative order of existing navigation: Home, Handoffs, Memories, Experiences & Skills,
Profile, Topic Memory, Prompts, Usage. Profile remains reachable when empty; unavailable generation does not prevent
reading saved content.

Profiles and handoffs belong to the selected Scope. Parent relationships and Context References do not implicitly
aggregate content. Switching Scope clears the previous record, historical selection, and collection position. Language
and theme changes preserve the Scope and exact revision being read.

## Profile reading

A Scope has at most one Profile head, identified by `profile/profile`. The page displays complete saved Markdown and
available revision, generation mode, timestamp, and source-window metadata. The presentation layer does not invent
sections or conclusions.

Version history opens a paginated revision collection. Selecting a revision labels the page Historical profile,
Revision N, with a return-to-current-profile link. Historical reading neither rolls back the head nor generates
content. An absent committed Profile displays No profile in this scope, rather than implying processing failed.

Sources and references follow the body. Evidence inspection requires both permission and association with that exact
revision. Unreadable evidence is not replaced with unrelated content; permitted reference information remains visible.

## Handoff Markdown download

Each readable collection item and the detail heading offer Export Markdown. Reading and downloading use the same
artifact ID and revision. Downloads always contain the full body, even when the collection preview is abbreviated.

For example, `/dashboard/handoff-detail?scope=S1&artifact=h1&revision=3` downloads `handoff-r3.md`, including complete
identity in its body. The file contains the objective, state, next action, omissions, statement citations, direct sources,
and upstream artifact references. Fixed headings support English and Chinese; business content is not translated.

Downloading does not create revisions, change work state, or resume a handoff. The browser reports successful downloads.
Failures produce readable errors and recovery links, never an error document disguised as Markdown. After credentials
expire, signing in returns to the exact detail; the user explicitly downloads again.

## UI pre-design

These illustrations use sample content to specify layout and action placement, not implemented product screenshots.
The shared Chinese-language mockups use existing Tabler typography, spacing, navigation, buttons, and source dialogs;
sample body sections do not define a new content schema. All implemented controls support both interface languages.

### Profile reading page

The existing Scope selector and navigation remain on the left. The main column presents the heading, revision metadata,
complete body, and references. Version history opens server-paginated revisions with metadata only where available;
unavailable history is not represented by an empty clickable action.

Historical content is labeled with its revision. Return to current profile resolves the current head; return to version
history restores the previous history position. Closing evidence retains the reading position.

### Handoff collection with download

Keep the objective, state, preview, and reading link, adding a secondary Export Markdown action per item. Continuable,
blocked, and complete are textual states, not color-only distinctions. Empty collections have no objectless download
action. Each read/download pair carries the same exact reference; exporting one item does not generate the latest Scope
report. Do not add an ambiguous collection-level export button.

### Handoff detail with download

The heading shows the objective, artifact identity, and revision beside the primary Export Markdown action. Preserve
complete state, next action, omissions, citations, and evidence inspection. Return to collection restores its position.
Use native browser downloads with HTMX boost disabled, no format picker, and no premature success message. An error
page links to the same Handoff; authentication preserves that exact detail as its return destination.

### Mobile reading

Reuse the collapsible navigation. Stack the title, revision, primary action, and body in reading order. Download appears
before the body; actions and long references wrap. Wide code scrolls within its own block, not the whole page. Profiles
use the same reading order and accessible history control. Evidence dialogs retain the existing full-screen mobile mode.

### Empty, error and version states

| State | Presentation | Recovery or action |
| --- | --- | --- |
| No Profile | Preserve heading and Scope; display no profile | Select another Scope |
| Historical Profile | Label the selected revision; show complete historical content | Return to history or current profile |
| Expired history cursor | Explain that the position expired | Reopen the first history page |
| Evidence read failure | Keep the record readable; show the error in the evidence area | Retry or close evidence |
| Missing or inaccessible exact revision | Report the actual error without resolving another head | Return to collection or select Scope |
| No Handoffs | Preserve collection structure without a download action | Select Scope |
| Download failure | Readable error without attachment headers | Return to exact detail and retry |
| Expired credentials | Sign-in page retains a validated reading destination | Sign in, then explicitly download again |

Validate both languages, themes, keyboard operation, 200% zoom, complete Scope names, and reading position after dialogs.

# Reference-level explanation

## Existing capabilities and API reuse

Baseline `534460e0` already registers `prompts` and `topics`, loaded by `load_prompts` and `load_topics`. The generic
Artifact collection also exposes Topic Memory `title`, `summary`, `published_at`, and `source_count`. This RFC neither
adds a topic collection API nor requires extra per-topic body reads.

| Capability | Existing API | Boundary |
| --- | --- | --- |
| Current profile | `GET /v1/scopes/{scope_id}/artifacts/profile?limit=1`, then the exact revision endpoint | An authorized empty collection means no committed Profile; preserve the returned revision |
| Profile history | `GET /v1/scopes/{scope_id}/artifacts/profile/profile/revisions` | Server cursor pagination |
| Exact profile | `GET /v1/scopes/{scope_id}/artifacts/profile/profile/revisions/{revision}` | No current-head fallback |
| Exact handoff | `GET /v1/scopes/{scope_id}/artifacts/handoff/{artifact_id}/revisions/{revision}` | Complete content, digest, references |
| Evidence | `GET /v1/scopes/{scope_id}/sources/{source_type}/{source_id}` | Verify association with the exact record first |

DashboardAPI calls the authenticated HTTP surface rather than the database. Validate Profiles using `ProfileContent`
and Handoffs using `HandoffContent`. Downloads must additionally retain the exact response's identity, `content_digest`,
`sources`, and `artifacts`; a presentation object that discarded metadata is insufficient.

## Routes and reading state

| Route | Parameters | Purpose |
| --- | --- | --- |
| `/dashboard/profile` | `scope`; optional `revision`, `view=history`, `profile_cursor`, `profile_history`, `return_to` | Current, exact historical, or history collection |
| `/dashboard/handoff-download` | Required `scope`, `artifact`, `revision`; optional `lang`, `return_to` | Single Handoff download |
| Existing `/dashboard/handoff-detail` | Existing parameters plus optional `return_to` | Exact reading and collection return |

Combining `view=history` and `revision` returns 422. Revisions must be positive integers. Profile history starts with the
first server cursor and preserves previous-page positions using validated input. An initial Profile entry can resolve
the default Scope under existing rules; historical links fix the concrete Scope.

Register download before the generic `/{page}` route. The download family is always Handoff. Never use arbitrary family,
path, or template input for dynamic imports or filesystem reads. Extend evidence-origin recognition and exact record
loading for Profile so it cannot fall through to the default Experience origin.

## Directory return context

`return_to` affects navigation only, never the record selected for reading or download. When opening a Handoff detail,
the link builder constructs a relative `/dashboard/handoff` URL with the current `scope`, `cursor`, `handoff_history`,
`period`, and `lang`. Profile history uses `/dashboard/profile?view=history`, the same Scope, `profile_cursor`,
`profile_history`, and language.

Encode this destination into the detail link so refreshing, copying the URL, or changing language can preserve it.
Do not rely on Referer, `history.back()`, or a shared last-page cookie across tabs.

Accept only these two paths and their parameter allowlists. Reject schemes, hosts, protocol-relative paths,
backslashes, control characters, duplicate parameters, and nested `return_to`. Require the same Scope and validate
cursors/history against existing constraints. Limit the return URL to 8 KiB. Discard invalid or oversized destinations
and return to the default collection in the selected Scope, without changing a valid exact body read.

Clear the destination on Scope changes. Expired collection cursors offer an explicit first-page recovery. Download error
pages reconstruct exact details from `scope/artifact/revision`, retaining a valid collection destination; a collection
URL is not a substitute for the exact post-login Handoff destination.

## Authentication recovery

Existing `save_session` always redirects to `/dashboard/home` and accepts only a token. This RFC explicitly extends that
flow instead of assuming it can already recover a record.

For Profile/Handoff 401 responses, add hidden sign-in field `next`, constructed server-side. Allow only
`/dashboard/profile` and `/dashboard/handoff-detail` with route-specific parameter validation, concrete Scope, and the
selected revision. Convert download destinations to their exact detail route. Permit one validated collection
`return_to` inside `next`, not arbitrary nested destinations.

Accept `token` and `next` in the session form while retaining Origin validation, token validation, and cookie security.
Limit `next` to 16 KiB and the encoded form body to 32 KiB. Invalid next falls back to Home; form retries retain valid
destinations. Do not turn 403 authorization failures into repeated sign-in requests.

After session establishment, redirect to the selected reading page, which still runs normal authorization. Never replay
a download automatically. Missing or denied records produce their exact error rather than another head.

## Profile rendering and evidence

Read Markdown from `ProfileContent.content` and metadata from `generation`, without filling absent fields. Disable raw
HTML and image requests in rendered Markdown, allow only safe link protocols, and use safe external-link attributes.
Never apply Jinja `safe` to unprocessed content.

Reuse Tabler reading layouts, history pagination, evidence dialogs, translations, and themes. Evidence association uses
both `source_type` and `source_id` from the exact revision; a URL alone does not establish association or permission.

## Exact Handoff Markdown download

Downloads are same-origin GETs requiring `scope/artifact/revision`. Missing or invalid identity returns 422 without
default-Scope or current-head fallback. Use the Dashboard session and DashboardAPI to read and validate the exact record.

```http
Content-Type: text/markdown; charset=utf-8
Content-Disposition: attachment; filename="handoff-r3.md"
Cache-Control: no-store
X-Content-Type-Options: nosniff
```

The filename uses a fixed prefix and validated integer revision, never raw titles or IDs. Full identity is in the body.
Links explicitly include the page language, supporting `zh` and `en`; missing language follows Dashboard preferences.

Disable HTMX boost. Preserve failure statuses such as 404, 403, 422, and 503, with readable recovery pages and attachment
headers only on success. Handle 401 through sign-in recovery. Unavailable source bodies do not prevent downloading saved
citations; exports never fetch those bodies.

## Markdown document contract

The renderer accepts the exact Artifact response and locale, parsing the content with domain models. It does not read
HTML or call a model. Fixed sections contain:

1. Title and format version `powercontext.handoff-markdown.v1`.
2. Scope, family, artifact ID, revision, and `content_digest`.
3. Objective and disposition.
4. Every state statement, in original order, with its citations.
5. Next action and citations, or an explicit absence statement.
6. Every omission and optional citation.
7. Direct SourceRefs and upstream ArtifactRefs from the revision.

Source citations retain type and ID; artifact citations retain family, ID, and revision; memory citations retain the
complete exact entry-version reference. Render citations as escaped text or safe code blocks, without inventing URLs,
embedding source bodies, or claiming evidence was reverified.

Use literal UTF-8, LF, and fixed section order. Equal input and locale produce equal bytes, without current timestamps or
random values. `content_digest` identifies stored content, not the exported file, and is not a fabricated Report digest.

Escape HTML, links, and Markdown structure while preserving readable business text. Do not activate images or remote
resources. Choose code-fence length based on the longest backtick run in content. Reject outputs above 10 MiB of UTF-8
with 413 rather than truncating content or references.

Keep the renderer independent of authentication. Helpers can be shared without altering behavior, but do not construct a
fake latest Report for exact export. Existing Report schema, selection, digest, and output contracts remain unchanged.
Disabling Handoff Report does not disable Artifact-backed single-record downloads.

## Compatibility and implementation boundary

New routes are Dashboard presentation adapters, not new `/v1` operations. No database, Artifact schema, Head/Candidate
lifecycle, or generation-policy changes are required. Existing Prompt and Topic Memory behavior remains compatible.

Dashboard stays disabled by default and retains its static Bearer deployment requirements. This RFC does not expand
support for injected authentication/authorization providers. Scope and Artifact permissions still govern reading,
history, evidence, and downloads.

Implementation primarily touches Dashboard routes, loaders, API adapters, presenters, session handling, templates,
labels, and a standalone renderer. New return parameters must not change existing page-parameter interpretation.
If a public HTTP contract change becomes necessary, edit `openapi/powercontext.yaml`, run `make api-generate` and
`make contract-test`, and never hand-edit generated Python. This design requires no such public contract change.

## Acceptance and validation

| Scenario | Required behavior |
| --- | --- |
| No Profile, Profile-only Scope, unavailable generation | Reachable page, readable stored content, accurate empty state |
| Current and historical Profile | Body, metadata, and evidence match the selected revision |
| History pagination, detail refresh, return | Restore position; offer first page on expired cursor |
| Scope switch and duplicate names | Clear previous identity/return state; no implicit aggregation |
| Abbreviated Handoff preview | Download complete body, citations, and omissions |
| New head or another Handoff | Download the explicitly addressed artifact and revision |
| Later collection page to detail, refresh, return | Preserve cursor and previous-page history |
| Credentials expire during download | Sign in to the exact detail; no automatic download |
| Malicious next/return_to or large form | No external redirects, mixed Scope state, or unbounded body |
| Missing, denied, or unavailable content | No alternate-version fallback or error attachment |
| Repeated equal input and locale | Equal file bytes; no new artifacts or state mutation |
| Both languages/themes and narrow screens | Reading, history, evidence, and downloading work |
| HTML, links, controls, and fences | No structural or executable injection |
| Output budget exceeded | Explicit failure, no silent truncation |
| Existing Prompt and Topic Memory | Preserve routes, configuration, browsing, search, and exact reads |

Test observable behavior through public APIs, navigation, and downloads. Dashboard tests cover reading and recovery;
focused renderer tests protect identity and content/reference completeness; cross-component acceptance belongs in
`tests/e2e/`. Do not freeze private call order, template inventories, or concurrency counts.

Run `make check`, relevant Dashboard/Server/Access/renderer tests, and `make docs-test`. Extend
`scripts/dashboard_browser.cjs` for real downloads, authentication recovery, and return navigation. Run generation and
contract checks only when public contracts change. Keep screenshots, downloads, credentials, caches, and generated
website output outside committed source.

# Drawbacks

Profile adds navigation space and needs narrow-screen and long-name validation. Historical reading and return state add
URL parameters that need shared validation and size limits. Authentication recovery expands the existing form and must
avoid open redirects and automatic repeated downloads; it does not introduce a new permission model.

Downloaded files no longer follow subsequent server permission changes. Downloads reflect explicitly selected readable
handoffs, without extra source bodies or public links. Exact exports and latest reports have different selection
semantics that must remain distinct when sharing rendering helpers.

# Rationale and alternatives

A dedicated Profile page fits one complete scoped profile without first requiring a generic artifact browser. Prompts
and Topic Memory already have pages and do not need to be redesigned here.

Server-side exact reads preserve complete content and references; copying DOM cannot reliably recover truncated
previews or artifact metadata. Latest Report selection cannot export arbitrary historical revisions. Extending that
public selection and digest contract would broaden the feature, so use a Dashboard download adapter.

Allowlisted relative destinations survive refreshes and copied links; browser history and Referer cannot reliably
distinguish normal navigation, direct entry, and authentication. Returning to exact detail after login preserves an
explicit download action. Profile editing, generation, and rollback require conditional writes, review, and conflict
recovery and remain available through existing tools and APIs.

# Prior art

Artifact REST APIs provide current heads, revision history, exact reads, digests, and lineage. The Profile RFC defines
profile identity, content, and lifecycle; Handoff defines complete content and exact citations; Handoff Report provides
Markdown for latest scoped reports. Existing Dashboard pages cover memories, experiences, skills, handoffs, prompts,
and topics. Follow the [Dashboard design principles](../development/dashboard.md) for Scope isolation, complete reading,
independent errors, both languages, and both themes.

# Unresolved questions

Feature scope, exact export selection, return context, and authentication recovery are specified here. Select a safe
Markdown dependency and verify its license before implementation; validate navigation space, long content, and history
in a browser. Implementation choices must retain exact reading, complete output, and input validation. Cross-Scope
aggregation, Profile editing, and batch reports require separate designs.

# Future possibilities

Future work can add conditional Profile editing, historical comparisons, and explicitly named latest-Scope report
downloads. Improvements to existing Prompt and Topic Memory pages should address demonstrated gaps separately while
retaining Scope permissions and exact references.
