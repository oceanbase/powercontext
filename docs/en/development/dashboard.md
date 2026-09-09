# Dashboard design principles

The Dashboard is a content viewer for personal use and demonstrations, authenticated by a static token and disabled by default. This document helps developers and reviewers decide what a page should show, how to organize reading, and whether a change preserves the behavior users need. The API contract in `openapi/powercontext.yaml` and the service implementation define the available capabilities.

## What the Dashboard helps users do

A user may open the Dashboard to check an agreement, find an experience relevant to a similar problem, or resume interrupted work. The page needs to make the current scope clear, help them find the relevant record, and provide access to source material when they need to check it.

The core experience for 1.0 is reading saved content. Memories, experiences, skills and handoffs can exist independently. A scope containing only memories should work normally, and an empty scope should still have a clear entry point and page structure. Existing tools and APIs handle generation, review and saving. Reading their results should not require generation configuration.

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

### Source material supports checking in context

Sources appear after the record's text and can be opened directly from an experience, skill or handoff. They use Tabler's large modal, which fills the screen on smaller devices. A menu switches between sources. Closing the reader returns to the user's place in the record. The same actions are available from the keyboard.

Source text can be long. Size its reading area to the viewport, keep the heading and close control reachable, and let the text scroll within that area. Opening a source should not lengthen the whole page until the user loses their place. If one source fails to load, the open record remains readable.

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

## Boundaries between design and implementation

### Page capabilities come from existing APIs

Pages show actual readable data, and actions correspond to existing capabilities. If one section fails, other independently readable content remains visible. Read errors, insufficient permissions and missing generation configuration have different meanings and must not collapse into an empty state.

The Dashboard supports the built-in static Bearer identity, with the same permissions for every token holder. Enabling it
requires `POWERCONTEXT_SERVER_DASHBOARD_ENABLED=true`, `ACCESS_MODE=enforced`, and `AUTH_TOKEN`. Team deployments that
inject authentication or authorization Providers must disable it. Pages reuse the existing API and its access checks;
they add no data endpoints or member and role management. See [Install and run](../docs/get-started/install-and-run.md)
for personal setup.

Collections can change as users save and revise content; exact references still identify their historical versions. Handle missing or inaccessible references explicitly, without substituting the current version or a similar record.

### Frameworks provide common behavior; pages compose content

Tabler provides common capabilities such as navigation, selectors, cards, pagination, drawers and charts. Reuse available components, including their default typography, spacing, focus and responsive behavior.

Jinja2 organizes content and page structure; HTMX handles navigation and fragment replacement. When an essential interaction needs additional code, use Surreal to connect events and css-scope-inline to keep supplementary styles within the owning component. Global styles cover only the necessary theme adjustments.

Define component boundaries around shared user behavior. Scope selection works the same way on every page, charts and summaries share statistical meaning, and source readers share opening, return and recovery behavior. Keep data interpretation separate from interface interaction so changing the presentation of one record type does not affect unrelated pages.

Departures from framework defaults need a specific reason. For example, replacing a page fragment may remove a modal before it finishes closing, requiring cleanup through the component's public API. Additional code should address that side effect and have a corresponding behavior test. Routine menu positioning and visibility across breakpoints remain the framework's responsibility.

## How to evaluate the design

Start a review with the user's task. Can they tell which scope they are viewing, find the full content, check a source and continue reading after a failure? Those outcomes are more useful than the number of cards on the page.

### Use ablation to check necessity

Remove one piece of information, action or dependency at a time and observe which user task suffers. If a page remains clear without an explanation, reconsider whether that explanation is useful. Saved content should remain readable with generation configuration disabled, and collections should remain independently usable when another content type is absent.

For content reduction, compare answers to the same question using the original text and prepared context at different budgets. Record missing constraints, next actions or references. A high reduction percentage alone does not show that the user still has the information needed to complete the task.

### Check situations that can mislead users

| Scenario | Acceptance focus |
| --- | --- |
| First use, only one content type, no search matches | Accurate states with recognizable layout and entry points |
| Default scopes, duplicate names, empty children and explicit references | Clear reading and statistical boundaries; switching does not change the default |
| Long lists, last pages, long sources and small viewports | Entries can be traversed, full text is readable, controls are reachable and reading position remains sensible |
| Changing language or theme, or returning through browser history | Scope, record identity and information hierarchy remain consistent |
| Unreported usage, incomparable data and partial read failures | Unknown values do not look like zero, and failures do not look like empty content |
| Credential expiry, network interruptions and service recovery | Errors are clear, and signing in or retrying restores reading |

Behavior tests exercise actual reading, lookup and navigation. Regression tests preserve cases where defects have occurred. Tests should allow internal refactoring when the visible behavior still holds. Buffer sizes, private call order and removed labels are not useful targets; current contracts such as access isolation still require tests for denied operations.

### Check presentation against real data

Save and generate verification data through existing APIs, using the database only for read-only checks. Pages, API responses and database records should correspond to the same scope, record and version. Retain clear evidence of configuration errors, failed requests and writes with unknown outcomes, and check existing results before resuming.

Consecutive-day replay checks whether content remains readable and available for recall in later work. Check the previous day's references before importing new content, then verify the day's generation, revisions and usage attribution. Exercise memory extraction, experience and skill generation, and handoff generation and saving through actual APIs. Record failed generation or the absence of new candidates as such. Reviews may use only material available at that point in time; a completion claim in the text does not replace verification.

Run replays in isolation and retain the input window, requests and results. Store screenshots and conversation content outside the repository. Simulated dates can test historical reads and daily attribution, but a few sessions cannot establish recall quality for arbitrary questions or the long-term reliability of production scheduling.

## Appendix: APIs and verification entry points

Use this section to locate the implementation. The code and API specification define the exact fields, limits and component configuration.

### Pages and APIs

| Page capability | API | Boundary to preserve |
| --- | --- | --- |
| Scope selection | `GET /v1/scopes`, `GET /v1/scopes/default`, `GET /v1/scopes/{scope_id}` | Treat defaults, explicit selection, hierarchy and readable scopes separately |
| Memory collection and text | `POST /v1/memory/entries/list`, `POST /v1/memory/search`, `POST /v1/memory/entries/get` | Full-text search uses `fts`, with up to 50 matches; read text through a complete memory citation |
| Handoff collection and text | `GET /v1/scopes/{scope_id}/artifacts/handoff` and exact revision reads | Retain cursors; list order does not imply chronological order |
| Experience collection and text | `GET /v1/scopes/{scope_id}/artifacts/experience`, `POST /v1/experience/get` | Provide paged browsing; there is currently no public HTTP search endpoint |
| Skill collection and text | `POST /v1/skill/library`, `POST /v1/skill/get` | Library queries return up to 200 entries; suggest a narrower query at the limit and preserve source identities |
| Source material | `GET /v1/scopes/{scope_id}/sources/{source_type}/{source_id}` | Verify the source's relationship to the record; `content` is currently the source type with readable text |
| Usage | `POST /v1/stats` | Read the current scope with `exact` and use Server reporting periods and statistical definitions |

Home combines these reads. When an API provides bounded results or a cursor, the page does not infer an unreported total. Preserve complete record and source identities rather than dropping or rewriting them to fit a response format.

### Code and checks

The API specification is in `openapi/powercontext.yaml`, the Dashboard implementation in `src/powercontext/server/dashboard/`, behavior tests in `tests/test_dashboard.py`, and browser acceptance checks in `scripts/dashboard_browser.cjs`. The Dashboard's `static/vendor/` directory contains pinned frontend assets and their licenses.

Run the basic checks from the repository root:

```bash
make check
uv run pytest tests/test_dashboard.py tests/test_server.py tests/test_access_http.py
```

Run browser acceptance checks against a configured, running Server:

```bash
npm install --prefix /tmp/dashboard-browser playwright@1.61.1
/tmp/dashboard-browser/node_modules/.bin/playwright install chromium
POWERCONTEXT_BROWSER_URL=http://127.0.0.1:8765 \
POWERCONTEXT_BROWSER_OUTPUT=/tmp/dashboard-verification \
NODE_PATH=/tmp/dashboard-browser/node_modules \
node scripts/dashboard_browser.cjs
```

If authentication is required, supply the current user's credential through `POWERCONTEXT_REPLAY_TOKEN`. Keep the output directory outside the repository and apply the access restrictions appropriate to its work content.

Use `scripts/dashboard_replay.py` for session replay and `scripts/dashboard_multiday.py` for consecutive-day verification. `scripts/dashboard_review.py` supports candidate review. Both replay scripts provide their arguments through `--help`. Replay configuration, operation logs and experiment results belong to individual verification records rather than page design principles.
