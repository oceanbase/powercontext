---
title: "Dashboard design principles"
description: "Design decisions for reading saved content, showing Scope boundaries, and working with revision history in the Dashboard."
---

# Dashboard design principles

The Dashboard is a content viewer for personal use and demonstrations, disabled by default. It shares the Server access mode: local access can be anonymous, while enforced access requires a static token. This document helps developers and reviewers decide what a page should show, how to organize reading, and whether a change preserves the behavior users need. The API contract in `openapi/powercontext.yaml` and the service implementation define the available capabilities.

## What the Dashboard helps users do

A user may open the Dashboard to check an agreement, find an experience relevant to a similar problem, or resume interrupted work. The page needs to make the current scope clear, help them find the relevant record, and provide access to source material when they need to check it.

The core experience for 1.0 is reading saved content. Existing pages also list revision history, compare two revisions, and roll an exact stored revision forward through the same conditional replace the API already uses. Each Atomic Memory shows its own revisions and comparison read-only. Restoring an Atomic Memory belongs to its restoration flow, which previews every affected memory before confirmation. Memories, experiences, skills and handoffs can exist independently. A scope containing only memories should work normally, and an empty scope should still have a clear entry point and page structure. Existing tools and APIs handle generation, review and saving. Reading their results should not require generation configuration.

Scope names, summaries and saved content supply the business topic. A payment service, customer interviews and personal research can use the same interface without separate navigation for each domain. Before adding a section, explain which task it helps the user complete and whether the available data supports what it claims.

## What content and scopes mean

### What each content type tells the user

| Content | What the user learns |
| --- | --- |
| Memories | Saved agreements, preferences, facts and decisions |
| Experiences | What happened in a particular situation, what was learned, and the conditions and evidence behind that lesson |
| Skills | Reusable practices, steps and checks |
| Handoffs | The objective, progress and next action recorded for a piece of work |
| Usage | Preparation counts, context size estimates, and PowerContext's own model usage |

Experiences need their original context, skills need their conditions of use, and handoffs describe the state at the time of saving. Related records may support different conclusions. A record's claim that a fix is complete still needs to be checked against the relevant evidence.

Home and collection pages may shorten previews; detail pages preserve the full text. The presentation layer shows existing information rather than deriving new conclusions by splitting or rewriting sentences. Source references let users check the content. Link to source text when it is readable, and retain the reference when only its identity is available.

### Scopes determine what the user is viewing

A scope defines content membership and selects what the user reads. An entry point without an explicit scope uses the Server's configured default. Choosing another scope in the dropdown changes the browsing location, not the default save destination or tool bindings.

Users need to distinguish scopes with similar names. The selector shows each scope's name and recorded ancestry, using the Server's parent-child relationships. A child collection does not automatically include its parent's content. Explicit references to other scopes during context preparation are also separate from hierarchy.

Content collections and usage both read the current scope and update when the user switches scopes. Usage for a parent does not automatically include its children. For example, an empty child may have no memories even when its parent has some. That is a valid state; the page must not fill the child with the parent's content.

### What the data supports

No records, no comparable data, unreported values and read failures need distinct presentation. Show zero only when the API reports zero. If a scope or record cannot be read, explain the failure and offer an available recovery action instead of silently substituting other content.

Prepared-context estimates cover only comparable records. A reduction percentage describes a change in text size. It does not establish completeness, content quality or billing savings. Show estimated increases as increases. List model input, output and embedding usage separately, and leave missing values as unreported.

Home summaries, daily charts and detail tables use the same statistical definitions. The Server supplies the reporting period and daily attribution; the interface does not invent historical data.

## How pages organize reading

### Home provides an overview and entry points

Home starts with the selected Scope. Issues precede all data when processing is blocked, failed, unconfirmed or unreadable; ordinary pending work and review candidates are not faults. Four pale-blue overview cards precede Token usage, an optional saved handoff, recent Sources and saved revisions. Each preview contains at most three records with a full-directory link and no extra-record disclosure. Home and Token usage share the complete comparison component, including axes, grid, legend, statistics and notices.

The overview summarizes saved sources and active memories, then shows current inventory, pending candidates, and context preparation counts for the selected period. Counts come from the statistics contract rather than the number of home previews. Inventory and preparation counts use different time bases; they are not a conversion funnel for the same inputs and do not establish Agent adoption or task benefit. Pending counts include artifact and tag-change candidates, with a matching Review filter for all pending candidates. Family inventory counts include all lifecycle states of current heads; destination collections retain their existing filters.

The built-in Runtime reads inventory inside the page's authorized, audited snapshot without loading additional recurrence history. Inventory and usage have independent reads and error displays, so either can remain available alongside readable content when the other fails. Changing the reporting period does not change inventory; switching Scope updates all overview values.

Each section has a clear heading and a link to its collection or details. Excerpts are previews. Wide screens place recent processing beside recently saved content; narrow screens preserve the reading order in a single column. Titles use saved content fields rather than technical identities or serialized envelopes. When a content type has no records, its empty state stays in its own section.

Users can also open records directly from a collection, a bookmark or an exact reference.

### Collections support finding; details support reading

Collections help users identify and select entries. Pagination limits how much they need to scan at once, and search narrows the results. Search coverage and result limits must match the API. Filtering the current page must not appear to search the entire collection.

Memories use a list and reading pane. Experiences, skills and handoffs lead from a collection to a detail page containing the full record and related material. Summaries may be shortened when there are many entries, but users must still have a way to read the full text.

Exact Memory links retain the desktop list on the left and display the selected immutable revision on the right. Selecting a list entry replaces the memory browser in place, preserving lifecycle filters, period and pagination in the URL. Small screens retain the accordion. Artifact-only access keeps a standalone detail without disclosing a Scope directory. Source association checks, missing historical reasons and version comparisons retain their existing behavior. The Profile page uses the same outer content width as other main pages.

After opening a record, the user should be able to return to its collection. A new search starts on the first page. Switching scopes clears the previous scope's record selection and pagination position, returning detail pages to the corresponding collection. Changing only the language or theme preserves the scope, period and record being read.

Memory search follows the deployment's capabilities by default and allows users to choose a retrieval mode. Show the actual mode, each result's matching channels and retrieval score so users can inspect why it was recalled; the score is not a similarity percentage.

## Source processing

The processing page reads Content Sources from the existing Source journal and checks the existing Artifact lineage relation. A Source cited by exact Artifact revisions shows one tag per type, such as Memory, Topic Memory, Experience or Skill. Each tag opens the matching type group in the Source detail. Groups show the type first, followed by each result title and body, with links to exact revision details. Results use pages of at most 20 exact revisions, with previous and next links; selecting a type starts its own result page. The original Source section preserves the complete content, including every field of a structured JSON value. A Source without a lineage row shows None. A runtime without a lineage projection shows Unknown. Neither establishes that processing failed. The page shows the first server save time in UTC from the stored Source envelope. Historical envelopes without this metadata show Unknown and are not backfilled. Readers predating this envelope field cannot read newly written envelopes; do not roll back to such a reader after new writes. This view adds no table, no public JSON interface and no execution or feedback record.

Dream details show the persisted status, outcome, reason, attempt count and timestamps, plus saved input-manifest references and a link to the recorded candidate. Candidate publication and current review state are checked in Review. Input-manifest nodes can supply resolved upstream Source references; these execution inputs are not automatically direct evidence for every output. Per-attempt timings, errors and missing history are not invented. Dream records cover only recorded Dream executions, not all automatic Source processing.

### Review explains the proposal and the saved result

The inbox directory uses existing reasons and family labels; an opened candidate can use its saved content as its title. The built-in directory keeps its metadata-only reads rather than loading every proposal for a title. Details put readable content fields before raw data. Updates compare the candidate with its recorded target revision, which may no longer be current. An unavailable baseline is shown as an unavailable comparison, not as unchanged content or an empty base.

Evidence bodies and candidate history remain explicit, deferred reads. Full proposal data, raw diffs, package metadata and the complete versioned editor remain available in disclosures. Approval effects are separate from the candidate's reason and evidence. Decisions retain current permission checks, same-origin protection and candidate-version guards.

After a decision, Review opens the same candidate under its saved status and preserves the language, Scope and reporting period. Approved content links to the exact recorded result revision, even after a later head change. Approved tag changes show the tags saved by that decision and the recorded content basis; they do not create a content revision or establish that those tags remain current.

### Source material supports checking in context

Sources can be opened directly from a memory, experience, skill or handoff. They use Tabler's large modal, which fills the screen on smaller devices. A menu switches between sources. Closing the reader returns to the user's place in the record. The same actions are available from the keyboard.

Source text can be long. Size its reading area to the viewport, keep the heading and close control reachable, and let the text scroll within that area. Opening a source should not lengthen the whole page until the user loses their place. If one source fails to load, the open record remains readable.

## Scope, authority, and read results

Without an explicit Scope selection, the page uses the default Scope. Changing the default binding affects subsequent requests once the current request has selected its Scope. Viewing another Scope does not change the default. Default selection requires `server.observe`; reading its content still requires content authority. Observer authority alone does not grant content access.

Scope read authority permits browsing that Scope's collections and usage. An Artifact-only grant permits the selected record's exact revision and references; the page does not expose the Scope title, neighboring records, or Scope usage. Valid grants remain effective when an owner relation is missing, while owner-derived authority requires the actual relation. Retrying a page does not restore revoked authority; see [Artifact ownership](../rfcs/1396_handoff_access_control.md#artifact-ownership).

An exact link's body and references correspond to the requested Revision. The current revision and lifecycle state are shown separately; historical content must not be labeled as current. Content, state, and authorization in one response must agree. Subsequent requests evaluate the updated authority and content state.

Home's memories, handoffs, experiences, and skills remain independently readable. A missing or unreadable content type does not hide other readable sections. Unavailable usage preserves authorized Scope information and readable content. Read failures appear as errors rather than empty collections or zero usage. Content and Scope metadata are exposed only after their required authorization and audit succeed.

An empty browse or search result does not establish that an exact historical revision is absent. Existing exact references remain readable under current authority. Rebuilding browse or search data must preserve historical bodies, references, and revision identities. Page reads do not establish ownership or repair broken default bindings; recovery uses the corresponding maintenance operation.

The Scope selector supports paging and search. The selected Scope remains readable while other Scopes are unloaded. Review shows a candidate directory, opens the selected record, and exposes history and evidence bodies when the reader requests them.

Opening a record preserves its exact body and shows its paged revision history. Comparisons show the two requested revisions; an unavailable comparison does not replace the opened body. History, comparison, and rollback controls use current authority. A rollback creates a new revision with its source, reason, and actor; a concurrent head change requires the user to review the new current revision before retrying.

## Keeping layout and interaction consistent

### Content changes preserve page organization

Populated, empty and unmatched search states keep the same heading hierarchy, main sections and reading order. An empty memory collection does not turn into a different full-page view, and a period without records does not remove the usage regions.

A stable layout reserves useful reading space without requiring every state to have identical pixel dimensions. Allow for the excerpt length, page capacity and available viewport; longer text can grow naturally. Pagination should remain easy to find when the last page has fewer entries. Short pages should not push the start of the content downward.

### Choose columns and scrolling to suit the available space

Page containers, side margins and grid spacing follow Tabler's defaults. Use columns when both sides have enough width for reading. Stack them in order when space is limited, rather than reducing body text size to retain a split view.

On smaller screens, memories expand within the list. Opening another entry closes the previous one. Desktop screens use a directory beside the text. Search results follow the same reading pattern. Previous, current page and next controls remain visible, with unavailable directions disabled.

Lists usually scroll with the page, with the entries on each page directly visible. Local scrolling suits long text, source panels and wide tables where it helps preserve context. Add a scroll region when it helps the user keep their place, not merely to make cards look even.

### Copy and visual treatment establish hierarchy

Headings describe a page or section; buttons describe the action they perform. Preserve business content as written and use direct, specific interface copy. Explanatory text need not repeat what the navigation and content already make clear.

Prefer Tabler's default typography, font weights, buttons and form states. Use regular weight for memory, experience and skill entries so every item does not compete for attention. Cards at the same level use consistent borders and spacing. Arrows belong to actions with a directional meaning.

Chinese, English, light and dark settings apply across the Dashboard, including login and standalone source pages. Translations may wrap naturally, but should preserve navigation order, button groups and column proportions. Theme changes retain the information hierarchy and readability. Changing the interface language does not rewrite business content. Use existing brand assets for the full logo and only the graphic for the favicon.

## Enablement and access

Enable Dashboard with `POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true`. Local `ACCESS_MODE=disabled` opens directly without a token. With `ACCESS_MODE=enforced`, it requires `AUTH_TOKEN` and uses the built-in static Bearer identity, with the same permissions for every token holder. Team deployments that inject authentication or authorization Providers must disable it. Pages follow the Server access mode and current authority, and provide no member or role management. See [Install and run](../docs/get-started/install-and-run.md) for personal setup.

## Visible behavior acceptance

| Scenario | Acceptance focus |
| --- | --- |
| First use, only one content type, no search matches | Accurate states with recognizable layout and entry points |
| Default scopes, duplicate names, empty children and explicit references | Clear reading and statistical boundaries; switching does not change the default |
| Long lists, last pages, long sources and small viewports | Entries can be traversed, full text is readable, controls are reachable and reading position remains sensible |
| Changing language or theme, or returning through browser history | Scope, record identity and information hierarchy remain consistent |
| Unreported usage, incomparable data and partial read failures | Unknown values do not look like zero, and failures do not look like empty content |
| Credential expiry, network interruptions and service recovery | Errors are clear, and signing in or retrying restores reading |

## Appendix: Page APIs

The HTTP API specification defines request fields and limits.

### Pages and APIs

| Page capability | API | Boundary to preserve |
| --- | --- | --- |
| Scope selection | `GET /v1/scopes`, `GET /v1/scopes/default`, `GET /v1/scopes/{scope_id}` | Treat defaults, explicit selection, hierarchy and readable scopes separately |
| Memory collection and text | `POST /v1/atomic-memory/list`, `POST /v1/memory/search`, exact Atomic Memory revision and state reads | Preserve Atomic Memory identities, lifecycle filters, the selected search mode and hit evidence; return up to 50 matches |
| Handoff collection and text | `GET /v1/scopes/{scope_id}/artifacts/handoff` and exact revision reads | Retain cursors; list order does not imply chronological order |
| Experience collection and text | `GET /v1/scopes/{scope_id}/artifacts/experience`, `POST /v1/experience/get`, `POST /v1/scopes/{scope_id}/artifacts/experience/search` | Dashboard provides paged browsing and Family relevance search; search covers approved active revisions only, returns up to 200 matches, and suggests a narrower query at that limit |
| Skill collection and text | `POST /v1/skill/library`, `POST /v1/skill/get` | Library queries return up to 200 entries; suggest a narrower query at the limit and preserve source identities |
| Source material | `GET /v1/scopes/{scope_id}/sources/{source_type}/{source_id}` | Verify the source's relationship to the record; `content` is currently the source type with readable text |
| Usage | `POST /v1/stats` | Read the current scope with `exact` and use Server reporting periods and statistical definitions |

Home shows the selected Scope's memories, handoffs, experiences, skills, and usage summaries. Collections show readable entries and pagination without inferring an unreported total. Record and source references preserve their complete identities.

`/dashboard/runtime` requires the same Dashboard authentication as the other pages but does not require a Scope. It reads this instance's existing `/health/live` and `/health/ready` contracts. A readiness response with HTTP 503 is displayed as not ready; a failed probe read is displayed as unavailable. These checks do not establish health for every worker or for metrics, logs, and tracing pipelines. Access coverage is secondary detail, separate from dependency health.

### Manual Experience and Skill generation

Methods pages offer Generate experience and Generate Skill. Users choose exact evidence from the current Scope and submit the current page selection. Experience supports Sources, active Memory revisions, and approved Experiences. Skill supports Sources or approved Experiences with the matching provenance origin. Supplemental notes are candidate metadata.

The Dashboard posts to the existing generation API using the current credentials and requires a same-origin request. It never approves the candidate. Pending candidates open in the review inbox; a semantic no-op is reported explicitly. Calls have a bounded 120-second wait with no automatic retries. Direct Generate calls do not create Dream execution records.
