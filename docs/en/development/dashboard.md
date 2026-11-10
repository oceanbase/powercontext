---
title: "Dashboard design principles"
description: "Design decisions for reading saved content, showing Scope boundaries, and working with revision history in the Dashboard."
---

# Dashboard design principles

The Dashboard is a content viewer for personal use and demonstrations, disabled by default. It shares the Server access mode: local access can be anonymous, while enforced access requires a static token. This document helps developers and reviewers decide what a page should show, how to organize reading, and whether a change preserves the behavior users need. The API contract in `openapi/powercontext.yaml` and the service implementation define the available capabilities.

## What the Dashboard helps users do

The Atomic Memory processor persists ordinary Source-to-memory processing results. Processing Records reads each attempt's inputs, status, exact published revisions and model explanations recorded during processing. Successful records commit with memory changes and consumption progress; failures and retries remain separate. A start without a completion after restart is shown as unconfirmed, never as proof that a Worker is still running.

An attempt can process multiple Sources. Its result summary describes the batch; each exact memory revision retains its own direct evidence. Intermediate model decisions are distinct from final publication results. Records retain at most 100 explanations of 2048 characters each and disclose truncation. Explanations are Scope-private historical material, never execution instructions. Reading history requires Scope read access and read access to every disclosed input, consulted Artifact and result. Authorization or read failures must not appear as empty history.

Internal Sources created by direct writes expose their server-bound exact target and do not enter ordinary automatic extraction. Working Notes also remain outside this flow. Unrecorded legacy processing remains unknown; citations and current configuration cannot supply historical reasons. Server revision `v1_4_1_005` registers the additive tables without backfilling old processing history. Existing managed databases require the shared offline migration procedure.

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
| Usage | Preparation, delivery and user feedback records, context size estimates, and PowerContext's own model usage |

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

Home starts with the selected Scope. Issues precede all data when processing is blocked, failed, unconfirmed or unreadable; ordinary pending work and review candidates are not faults. Four pale-blue overview cards precede Token usage, an optional saved handoff, recent processing, saved revisions and context preparations. Each preview contains at most three records with a full-directory link and no extra-record disclosure. Home and Token usage share the complete comparison component, including axes, grid, legend, statistics and notices.

The overview summarizes saved sources and active memories, then shows current inventory, pending candidates, and context preparation records for the selected period. Counts come from the statistics contract rather than the number of home previews. Inventory and preparation records use different time bases; they are not a conversion funnel for the same inputs and do not establish Agent adoption or task benefit. Pending counts include artifact and tag-change candidates, with a matching Review filter for all pending candidates. Family inventory counts include all lifecycle states of current heads; destination collections retain their existing filters.

The built-in Runtime reads inventory inside the page's authorized, audited snapshot without loading additional recurrence history. Inventory and usage have independent reads and error displays, so either can remain available alongside readable content when the other fails. Changing the reporting period does not change inventory; switching Scope updates all overview values.

Each section has a clear heading and a link to its collection or details. Excerpts are previews. Wide screens place recent processing beside recently saved content; narrow screens preserve the reading order in a single column. Titles use saved content fields rather than technical identities or serialized envelopes. When a content type has no records, its empty state stays in its own section.

Users can also open records directly from a collection, a bookmark or an exact reference.

### Collections support finding; details support reading

Collections help users identify and select entries. Pagination limits how much they need to scan at once, and search narrows the results. Search coverage and result limits must match the API. Filtering the current page must not appear to search the entire collection.

Memories use a list and reading pane. Experiences, skills and handoffs lead from a collection to a detail page containing the full record and related material. Summaries may be shortened when there are many entries, but users must still have a way to read the full text.

Exact Memory links retain the desktop list on the left and display the selected immutable revision on the right. Selecting a list entry replaces the memory browser in place, preserving lifecycle filters, period and pagination in the URL. Small screens retain the accordion. Artifact-only access keeps a standalone detail without disclosing a Scope directory. Source association checks, missing historical reasons and version comparisons retain their existing behavior. The Profile page uses the same outer content width as other main pages.

After opening a record, the user should be able to return to its collection. A new search starts on the first page. Switching scopes clears the previous scope's record selection and pagination position, returning detail pages to the corresponding collection. Changing only the language or theme preserves the scope, period and record being read.

Memory search follows the deployment's capabilities by default and allows users to choose a retrieval mode. Show the actual mode, each result's matching channels and retrieval score so users can inspect why it was recalled; the score is not a similarity percentage.

### Processing records connect inputs with recorded results

The processing page opens on an overview of the selected period: completed inputs, confirmed deliveries, explicit adoption, and inputs or unknown adoption that need attention. Counts come from `GET /v1/scopes/{scope_id}/processing/summary`. Stages count independently, not as conversion rates for one input cohort. Deliveries count only Bootstrap receipts created in the period whose current state is `injected`. Prepared context, pending receipts and failed receipts do not count as successful delivery. Adoption and unreported adoption use the latest feedback before the window ends for each preparation or Bootstrap record created in the period; repeat or changed feedback does not add adoption counts.

The automatic processing tab retains individual attempts; the source and execution tabs cover inputs and Dream runs. A successful retry retains the failed attempt in a separate history disclosure without leaving the successful input listed as incomplete. Current outstanding inputs include unconsumed Sources whose failed or unconfirmed attempt is outside the selected period, as well as never-attempted Sources. Inputs with attempts inside the period remain in those status counts. A capped scan cannot establish an empty backlog, even when its current lower bound is zero.

Memory detail counts preparations of the exact selected revision in the period and shows the latest preparation time, including preparations owned by another Scope. Only preparations whose Scope and every included Artifact are readable by the caller contribute to the count or timestamp. The database filters saved item addresses within the period; query work can grow with preparation history because there is no index on item addresses. This does not establish delivery, adoption or benefit.

Processing records provide separate paged directories for public Content Sources and Dream executions. The built-in Source directory uses descending journal order; other runtimes retain their public API order. Dream runs use descending acceptance order and support status filtering. These directories cover the current Scope. A saved Source does not establish that processing succeeded or produced new content.

A Source detail shows the stored input and up to 20 exact Artifact revisions that directly cite it, with an explicit notice when more relationships exist. This is a relationship lookup, not a reconstructed execution history. The built-in Runtime reads those identities in an authorized, audited snapshot without decoding the whole Artifact collection. The lineage lookup is scoped and bounded in result size, but its database work can grow with the Scope's lineage rows because there is no source-first index. Other runtime adapters show that relationship lookup is unavailable instead of presenting an empty result.

Dream details show the persisted status, outcome, reason, attempt count and timestamps, plus saved input-manifest references and a link to the recorded candidate. Candidate publication and current review state are checked in Review. Input-manifest nodes can supply resolved upstream Source references; these execution inputs are not automatically direct evidence for every output. Per-attempt timings, errors and missing history are not invented. Dream records cover only recorded Dream executions, not all automatic Source processing.

### Review explains the proposal and the saved result

The inbox directory uses existing reasons and family labels; an opened candidate can use its saved content as its title. The built-in directory keeps its metadata-only reads rather than loading every proposal for a title. Details put readable content fields before raw data. Updates compare the candidate with its recorded target revision, which may no longer be current. An unavailable baseline is shown as an unavailable comparison, not as unchanged content or an empty base.

Evidence bodies and candidate history remain explicit, deferred reads. Full proposal data, raw diffs, package metadata and the complete versioned editor remain available in disclosures. Approval effects are separate from the candidate's reason and evidence. Decisions retain current permission checks, same-origin protection and candidate-version guards.

After a decision, Review opens the same candidate under its saved status and preserves the language, Scope and reporting period. Approved content links to the exact recorded result revision, even after a later head change. Approved tag changes show the tags saved by that decision and the recorded content basis; they do not create a content revision or establish that those tags remain current.

### Usage records separate preparation, delivery and user reports

Usage retains resource statistics and adds a separate records tab. Preparation details save the included content's Scope and exact revisions, byte count, truncation and degradation, without saving the query or body. The built-in Server records future preparations only when Dashboard is enabled; an unconfigured SDK adds no such storage. Existing Bootstrap receipts keep their recorded states. Older aggregate statistics cannot supply missing per-call history.

An opened record links to the included revisions and supports returning to the same record, period and language. Cross-Scope content returns to the preparation's Scope; the return link grants no authority, and the destination performs its own authorization. Users report adoption, helpfulness and a reason separately. Feedback identifies the reporting actor and time; neither task success nor delivery supplies these answers. Submission requires contribution authority in the current Scope and read access to all referenced content. Writes retain same-origin protection and idempotent identity: repeating identical content adds no record, while different content under the same identity conflicts. An update adds a new feedback record; readers show the latest one.

Preparation and feedback use built-in authorization, auditing and persistence. `POST /dashboard/usage/feedback` is an HTML form operation, not a new public JSON API. Additive migration `v1_4_1_006` adds preparation and feedback tables without backfilling past queries or bodies. Handoff details link only acknowledgements of the exact revision and outcomes that explicitly cite those acknowledgements. These are receiver reports, not proof that memory caused a task benefit.

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
| Processing records | `GET /v1/scopes/{scope_id}/processing`, `GET /v1/scopes/{scope_id}/processing/summary`, Source processing history, Source and Dream directories and exact reads | Keep independent cursor order, saved status and recorded outcomes; do not infer missing attempts or results |
| Usage | `POST /v1/stats` | Read the current scope with `exact` and use Server reporting periods and statistical definitions |

Home shows the selected Scope's memories, handoffs, experiences, skills, and usage summaries. Collections show readable entries and pagination without inferring an unreported total. Record and source references preserve their complete identities.

### Observation and the home summary

Issues show the recorded cause, latest error, impact and checks. Missing causes remain unknown. The retry ceiling explains why retries stopped, not why the first attempt failed. The Topic Memory detail link targets the same diagnosis on Processing Overview, including error code, blocked time and attempt count. Mobile rows retain readable time, result and input without page overflow.

The sidebar provides separate Observation, Retrieval and use, and Runtime state entries. Observation contains Overview and Processing records. Existing `/dashboard/processing` and `/dashboard/usage` links remain supported. Overview and automatic processing use the same selected UTC calendar period as usage; choosing a specific processing day overrides that period. Source inventory and Dream run history are not period-filtered and do not show a period selector.

The overview displays its exact UTC reporting window. With no processing records, it shows unavailable history rather than zero-valued results or a healthy-state claim.

Processing results separate unique recorded inputs, completed inputs, memory changes, and completed runs without changes. These are not a conversion funnel. Source records and background tasks have separate entry points. Queue, running, and retry states are not fully available; pending completion is not proof of a live worker.

`/dashboard/runtime` requires the same Dashboard authentication as the other pages but does not require a Scope. It reads this instance's existing `/health/live` and `/health/ready` contracts. A readiness response with HTTP 503 is displayed as not ready; a failed probe read is displayed as unavailable. These checks do not establish health for every worker or for metrics, logs, and tracing pipelines. Access coverage is secondary detail, separate from dependency health.

Processing Overview and automatic processing also offer a 90-day shortcut and a custom start/end date form. Both selected UTC dates are included; a range may cover at most 92 days. These processing dates are retained across processing tabs, status filters, Scope changes and pagination. Invalid or reversed ranges remain editable. Retrieval/use statistics retain their own existing period presets.

Current inventory and context preparation summaries belong on Home and Retrieval and use. Processing Overview does not read or repeat these counters. Its outstanding-input count retains the bounded source scan semantics: inputs covered by attempts in the selected period are excluded, and a scan limit is shown as a lower bound. It must not be presented as a complete live queue.

Processing Overview reads current Topic Memory status through the authorized public status API, independently of the history window. It shows idle, pending, or blocked without treating pending as proof of worker activity. Block details remain expandable. On the built-in runtime, the six newest sources appear with authorized processing summaries and links to their exact source history. Source positions are not pending counts.

Usage record detail puts the saved outcome and reason first, followed by the exact prepared content. Delivery notices appear only on delivery records. Saved feedback remains visible; the optional feedback editor is collapsed until opened. Record identity, integration and content bytes are labeled in an expandable detail section.

Automatic processing uses a table with Time, Processing type, Original text, Processing result and Action columns. Each attempt shows up to three authorized original source excerpts and a short recorded reason or error. Retry identity is explicit. Details expand below the same row, preserving decision reasons, exact output revision links, input source links, start/completion times, errors and cursor ranges. Additional inputs are counted; missing source text stays unavailable. The table is horizontally scrollable on narrow screens.

Source detail uses three numbered stages: saved input, recorded processing, and saved output. Missing attempts remain unknown. Content that cites the source is shown separately from outputs attributed to the selected attempt; provenance alone does not prove execution timing or creation.

Source metadata and full source text belong to Input, the execution-record entry belongs to Processing, and cited revisions belong to Output. The separate bottom technical section is removed. Additional cited versions can be expanded within Output.

`record_type=dreams` is labeled Dream task records. It includes supported refinement and revision operations beyond Experience and Skill. Empty states distinguish no accepted tasks from no matches for a selected status and link to Automatic processing for source-to-memory records.

Home recent processing uses three columns: Time, Result, User input. The input excerpt links to the exact source processing detail; batch notes stay with the result.

Recent saved content uses the same table alignment with Time, Type and revision, and Content columns. The first three records remain visible and remaining records can be expanded.

### Manual Experience and Skill generation

Methods pages offer Generate experience and Generate Skill. Users choose exact evidence from the current Scope and submit the current page selection. Experience supports Sources, active Memory revisions, and approved Experiences. Skill supports Sources or approved Experiences with the matching provenance origin. Supplemental notes are candidate metadata.

The Dashboard posts to the existing generation API using the current credentials and requires a same-origin request. It never approves the candidate. Pending candidates open in the review inbox; a semantic no-op is reported explicitly. Calls have a bounded 120-second wait with no automatic retries. Direct Generate calls do not create Dream execution records.

Experience and Skill share one sidebar entry and retain their internal type tabs and generation actions. Retrieval and use has its own sidebar entry under observation and configuration, opening usage records directly. Processing retains Overview and Processing records tabs; it no longer nests Retrieval and use.

Usage record lists use Time / Result / Recalled items / Details columns. Counts come from the saved selection; list rendering does not read Artifact content. Details open the numbered content table and read authorized exact Artifact revisions. Empty selections show zero items. Truncated selections retain their completeness notice. Original queries are not reconstructed.

Token usage and Retrieval and use are separate sidebar entries. Their internal navigation tabs are removed; Scope changes retain the selected view.

Usage record detail displays recalled items in a numbered table: No., Recalled content, Type, Revision, and Saved at. Bodies use plain text; exact revision links are separate. Saved at uses the referenced revision timestamp when available, otherwise Not recorded; it does not substitute the preparation timestamp. Long bodies can be expanded.

The current Profile and empty Profile page provide a Generate profile button. It submits to the existing `/v1/profile/flush` API and processes the next source window under the existing generation and activation policy. Updated, unchanged, disabled and conflict outcomes are explicit; pending candidates open in Review. Historical revisions do not offer generation. The action preserves Scope authorization and same-origin checks.

Memory recall ranking uses `POST /v1/atomic-memory/list` with `sort_by: recall_count` and `period: today | 7d | 30d`. The API default remains `identity`; the Dashboard defaults to most recalled and offers identity order. `recall_counts` maps the returned current memory IDs to recorded preparation counts. All revisions of one memory are combined, and one preparation counts each identity once. Only preparations whose Scope and every selected Artifact are readable contribute, including readable cross-Scope use. The backend orders before pagination, uses identity to break ties and binds the cursor to filters, dates, principal and the initial preparation cutoff. Search keeps its relevance order. Detail usage remains specific to the opened revision.

Ranking reads the eligible memory identities and related preparation payloads in the selected window; it does not decode the entire memory collection. Sorting costs O(M log M) for M eligible identities, plus authorization of the relevant preparations. This is not a constant-cost indexed leaderboard. A local SQLite sample with 1,000 current memories and 200 preparations returned six items in 0.372 seconds; this does not qualify OceanBase performance or larger histories.

The Source processing sidebar opens processing records directly (`record_type=attempts`). That page contains four processing summary cards for its selected window and complete processing records. Home retains only recent processing details and a directory link. Legacy overview links remain readable and support an anchor to the current Topic Memory diagnosis.

The sidebar order is Home, Runtime data (Automatic processing, Dream processing), Saved content, Work, Observation, and Configuration. Home has no local date picker; its current period remains visible as text. Notes lists all content matching its state or search criteria without a date filter or period caption. Recall statistics retain the request period. Every Notes directory mode, including identity order, recall order, search and inactive lifecycle filters, retains its recall-count line. The server batches a bounded `artifact_ids` selection of displayed identities through the existing list API; incomplete or unavailable counts show an unavailable state rather than zero. The desktop excerpt reserves two lines so changing order does not resize metadata rows.

The built-in Home projection supplies the all-time processing summary. The public summary API retains its seven-day default and maximum 92-day window. All-time summaries decode the Scope attempt history, so cost grows with history size. Public-API-only injected applications show an unavailable summary rather than substituting seven-day totals.

The four pale-blue Home cards show saved sources, six grouped artifact counts, pending candidates and period context preparations. Each grouped count retains its directory link. The Processing page has four cards for its selected window: completed sources, memory changes, sources without a successful run and unconfirmed results. Mobile cards use two columns.

The source table has five columns: original text, current processing result, latest processing time, recorded attempt count and details. Counts use the existing authorized history endpoint for up to six displayed sources and twenty attempts per source. A next page produces 20+; failed reads remain unknown and empty history means no recorded attempts. Public Source records have no saved-time field; processing times must not be labeled as capture times, and missing timestamps remain unknown. Processing summaries place create/revise counts under memory changes, execution/no-change counts under completed sources, and historical failed-run counts under sources without success. Source counts and execution counts remain distinct, including failures followed by successful retries. The duplicate statistics disclosure is removed. The page subtitle covers source text, processing results and failures; preparation, delivery and feedback belong to Retrieval and use and do not prove Agent adoption.

The Runtime data group contains Automatic processing and Dream processing. Sources is the primary view of Automatic processing; Execution records is an auxiliary view with the same heading, browser title and active sidebar entry. Existing sources, attempts and dreams route parameters and legacy links remain supported. Automatic and Dream processing have hover/focus explanations beside the heading. Scope switches retain the record type; peer links clear foreign cursors, detail identities and filters. Partial navigation updates the document title.

Source processing details use h1 24px, h2 20px, h3 18px and h4 16px, with 14px body text and 12px metadata. The page has one h1; results and reasons inside steps use subordinate headings. Expanded previous attempts share the same type scale, spacing and card border, including on mobile.

Source outputs use Content, Type, Revision and Details columns. Topic Memory shows its saved title and summary. Previews read exact family/artifact/revision identities, prioritizing current execution and linked outputs within a 20-body budget. Unavailable or over-budget previews show a missing-content state and identity rather than substituting source text. Direct output, retained memories and citation-only related revisions stay distinct. Previous attempts use concise Input, Processing and Output sections, with short source text, one reason or error, and up to three outputs by default; remaining evidence expands on demand.

Source details have three H2 cards: Raw data input, Processing reason and Processing results. Their header bands use different shades of blue. The second card contains only the reason; saved-content counts and output tables belong to the third. Execution metadata expands after the three cards. Citation-only related outputs are collapsed by default with a count and the citation disclaimer retained.

Generate experience/skill defaults to an asynchronous Dream task. The selection panel has a distinct Start task action, a selection count, automatic evidence-type loading and no duplicate entry button or inventory below it. Synchronous generation is a secondary entry with an explicit explanation that it waits for results and creates no Dream record. Experience uses exact Memory or Experience revisions with refine_experience; Skill uses Experience revisions with derive_skill. Existing Dream APIs own task creation. Source-only synchronous Generate calls remain available and are not represented as Dream tasks. A form idempotency key prevents duplicate tasks. The detail page refreshes every three seconds while queued or running and stops at a terminal state. Candidates still require review.

Blocked processing diagnostics on Home and Processing offer Retry processing for the exact observed block_id. The existing recovery endpoint owns scope.admin authorization, idempotent acceptance and stale-block conflicts. Dashboard checks same-origin requests and form size, preserves backend error statuses, and reports acceptance without claiming completion or retrying other Scopes.

Sidebar entries use distinct local SVGs from Tabler Icons v3.35.0, retaining consistent stroke and dimensions: automation settings, moon/stars, brain, hierarchy, tools, user scan, checklist, exchange arrows, search, coins, activity and adjustments match their respective labels.

Runtime configuration comes from an allowlisted snapshot of the BuiltinConfig used to assemble this Server and is returned in Dashboard HTML. There is no new public JSON configuration endpoint. It shows the service role, explicit Dream switch, Memory/Topic Memory/Experience intervals, Profile cron and time zone, generation configuration presence, concurrency and timeouts. It does not reload environment files, substitute source defaults for unavailable configuration, or expose credentials, model URLs or database paths. Health still comes from /health/live and /health/ready. Enabled configuration does not prove active execution; database cursor reads used during investigation are not a source for this configuration panel.

Dream task detail follows Input, Processing, and Output in order. Input shows up to 20 authorized exact Artifact previews. Identity and source references are collapsed by default. The processing stage retains saved status, reason, error, timestamps and attempt count. Output links to the recorded candidate or states that no new output was recorded; unfinished tasks keep polling.

Cloud-native monitoring in the observation navigation opens a static guide covering managed Docker Compose deployment, Server telemetry settings, Grafana/Phoenix access, read-only verification and external platform connections. The page keeps Dashboard authentication, requires no Scope or running monitoring stack, and performs no deployment actions.

The source table shows the first server save time in UTC, independently of processing. The timestamp is server metadata in the stored Source envelope; replays and receipt upgrades preserve it. Existing records without this metadata show Unknown and are not backfilled. The built-in Dashboard projection exposes it; hosts using only the public Source API show Unknown. Readers predating this envelope field cannot read newly written envelopes; do not roll back to such a reader after new writes.
