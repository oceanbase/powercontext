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
| Usage | Estimated changes in prepared context size and PowerContext's own model usage |

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

Home shows usage first, memories alongside experiences and skills next, and handoffs last. Usage provides an overview of activity. Memories and reusable content are available for reference, while handoffs lead into specific work records.

Each section has a clear heading and a link to its collection or details. Excerpts are previews. Wide screens place memories beside experiences and skills; narrow screens preserve the reading order in a single column. When a content type has no records, its empty state stays in its own section.

Users can also open records directly from a collection, a bookmark or an exact reference.

### Collections support finding; details support reading

Collections help users identify and select entries. Pagination limits how much they need to scan at once, and search narrows the results. Search coverage and result limits must match the API. Filtering the current page must not appear to search the entire collection.

Memories use a list and reading pane. Experiences, skills and handoffs lead from a collection to a detail page containing the full record and related material. Summaries may be shortened when there are many entries, but users must still have a way to read the full text.

After opening a record, the user should be able to return to its collection. A new search starts on the first page. Switching scopes clears the previous scope's record selection and pagination position, returning detail pages to the corresponding collection. Changing only the language or theme preserves the scope, period and record being read.

Memory search follows the deployment's capabilities by default and allows users to choose a retrieval mode. Show the actual mode, each result's matching channels and retrieval score so users can inspect why it was recalled; the score is not a similarity percentage.

### Source material supports checking in context

Sources appear after the record's text and can be opened directly from an experience, skill or handoff. They use Tabler's large modal, which fills the screen on smaller devices. A menu switches between sources. Closing the reader returns to the user's place in the record. The same actions are available from the keyboard.

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
