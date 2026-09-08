# Dashboard development and verification

The Server includes a read-only Dashboard at `/dashboard/home`. It presents scope selection, handoffs, memories, experiences, skills and usage through existing HTTP APIs. `openapi/powercontext.yaml` and the Server implementation define what the pages can claim.

## Pages and contracts

| Page | Public API | Presentation rule |
| --- | --- | --- |
| Scope selection | Scope list, default and descriptor GET endpoints | An absent scope uses the Server default; an explicit empty value opens the chooser; unknown scopes produce an error |
| Home | Memory entries, exact Artifact revisions and statistics | Each section loads independently; content existence does not determine usage existence |
| Memories | `/v1/memory/entries/list`, `/v1/memory/search`, `/v1/memory/entries/get` | Search up to 50 full-text matches; preserve complete text and line breaks with the complete MemoryCitation |
| Handoffs | Scoped handoff Artifact list and revision GET endpoints | Present the recorded state and next action; list order does not imply recency |
| Experiences and skills | Experience list, `/v1/experience/get`, `/v1/skill/library`, `/v1/skill/get` | Browse experiences and skills separately, opening experiences by default; search skills with the library's 200-result limit; preserve exact content and provenance |
| Source material | Scoped source GET endpoint | Verify membership in the selected record before reading; a source failure does not replace the record |
| Usage | `/v1/stats` | Use reported periods, totals, daily values, purposes and comparison coverage |

The Dashboard does not call generation endpoints or require Handoff Report, capabilities or global administration access. Existing tools and APIs own writing, review, generation and distribution. There are no placeholder actions for these operations.

Validate Artifact HTTP content in JSON mode. Strict domain tuples are arrays in JSON and cannot be validated as Python dictionaries without accounting for that representation.

## Scope semantics

An absent `scope` query reads the Server default. Select any scope through the dropdown. Switching the selector changes only the URL, clears record identities and cursors, and returns detail pages to their collection. It does not change tool bindings or the default save destination.

Selection options show their own title before the readable ancestor path. Parent and child links use `parent_scope_id`. Content pages read one exact scope. Usage explicitly offers `exact` and `subtree` selections.

`context_references` governs explicit context preparation references. It is separate from hierarchy: parent content is not inherited by a child collection, and referenced scopes are not automatically part of subtree statistics.

A denied scope list must not prevent trying an explicitly supplied readable scope. An exact Artifact detail can be read with an Artifact grant even when its scope metadata is unavailable. Unknown or denied records remain errors; they must never fall back to an unrelated record or an empty-state message.

## Product language and evidence

The Chinese navigation uses “交接”, “记忆”, “经验与技能” and “用量”. Scope titles and summaries supply the business topic. Do not add evaluation labels, sample content, promotional copy or status explanations that do not help the user act.

A historical assistant report is evidence that the assistant made that claim. It is not independent evidence of completion. Review extracted candidates against the actual conversation window before approval, keeping requested changes and unfinished work distinct from verified results.

Home may shorten excerpts and link to complete records. Detail pages preserve text instead of inventing titles, conclusions or citations by splitting or rewriting it. Long headings use a smaller type scale to keep the reading layout usable.

Usage distinguishes zero, unreported values, unavailable comparisons and read failures. Render `null` as unreported. Preserve unknown purpose names and Server totals. Compute percentages only from the comparable baseline and reduction; show negative reduction as an increase. Keep generation and embedding usage separate. Estimated differences are not billing savings.

Charts and daily tables share the same UTC response data. Do not construct historical dates or values in the browser. Home and usage share the same presenter.

## Implementation ownership

Runtime files live in `src/powercontext/server/dashboard/`.

| File | Responsibility |
| --- | --- |
| `routes.py` | URLs, scopes, exact references, complete and partial HTML |
| `api.py` | Existing HTTP calls with incoming credentials, content validation and stable read errors |
| `content.py` | Per-page loading and independent section failures |
| `presenters.py` | Contract responses projected into template contexts |
| `session.py` | Browser credential transport and authentication recovery |
| `templates/components/` | Navigation, headings, reading layout, sources, charts and errors |
| `preferences.py` | Interface language, preference links and cookie |
| `pagination.py` | Complete-list paging and backward navigation over opaque API cursors |
| `labels.json`, `labels.en.json` | Chinese and English interface copy, without business data; write Chinese directly rather than Unicode escapes |
| `static/` | Shared layout variables, pinned assets and licenses |

Experience details use `/v1/experience/get`. Skills use `/v1/skill/library` and `/v1/skill/get`, retaining named provenance such as skill-usage. The generic Artifact response currently restricts SourceTypeReference to content and cannot represent those records completely. Never relabel or drop provenance to make a response fit. The public source-body endpoint currently supports content only; preserve other source identities without inventing readable body links.

Memory search uses the existing `fts` mode without requiring a vector model. Retain API ranking and exact citations, and prompt for narrower keywords at the 50-result limit. Skill search uses the library’s `query`. Experience has no public HTTP search endpoint and provides paged browsing; do not present filtering one page as a complete search or use context preparation for collection lookup.

The skill library shows available heads, with exact access retained for retired revisions. Its maximum is 200 results; prompt for a narrower search at that limit rather than inventing a total or pagination cursor.

The in-process HTTP transport calls the same Server and forwards incoming credentials through its authentication and authorization checks. Templates must not read the runtime or database directly. Never inject a deployment administrator token on behalf of the browser.

A submitted Bearer credential is stored in an HttpOnly, SameSite Strict cookie scoped to `/dashboard`, with Secure on HTTPS. Session submissions require the same origin. API endpoints do not accept this cookie directly. HTML uses `no-store`; HTMX history caching is disabled so restored pages read the Server again.

## Components and visual baseline

Reuse Tabler navigation, breadcrumbs, cards, lists, forms, buttons, Accordion, Offcanvas, Alert, Spinner and Table. Charts use its ApexCharts integration. HTMX owns navigation and fragment replacement; Surreal handles necessary event connections between those frameworks.

Pinned assets are Tabler Core 1.4.0, Tabler Icons 3.31.0, HTMX 2.0.4 and ApexCharts 3.54.1. Surreal 1.3.4 uses commit `cd8f18d34067e073d0aa25675cc0649e304292a3`; css-scope-inline 1.1.0 uses `14e835ebe3b8596d0f3ee456162edf63bddc95ba`. Licenses ship beside the assets.

Use `me()`, `on()` and `off()` as described by [Surreal](https://github.com/gnat/surreal). Place styles within their component root and use `me` according to [css-scope-inline](https://github.com/gnat/css-scope-inline). Do not implement another selector or component system.

Narrow screens use Tabler Collapse for the scope selector and menu; the desktop sidebar stays sticky beside the page.

Follow Tabler's [page layout](https://docs.tabler.io/ui/layout/page-layouts) and [typography](https://docs.tabler.io/ui/base/typography) conventions against the pinned 1.4.0 stylesheet. Use native grid columns for the sidebar and main content: 3/9 from lg and 2/10 from xxl. Keep the sidebar in `sticky-lg-top` with navigation filling its column, and share `container-xl` between content and breadcrumbs. Organize content with `row`, `g-4` and the 12-column grid. Home uses 6/6 columns from xl, memory uses 4/8, detail reading uses 7/5 from xxl, and usage uses 4/8 from xl. Compact home usage uses 4/8 from lg and 3/9 from xxl. Stack below each breakpoint rather than reducing body text to fit two columns.

Navigation and reading text use a 16 px baseline. Keep Tabler's normal 400, medium 500 and heading 600 weights; list entries use normal weight. Page titles use 32 px, or 24 px on small screens, and section headings use the native 20 px size. Usage percentages can be larger but must not squeeze labels, period controls or charts. Period controls use the native small button group and must fit English labels on narrow screens.

Show record references as secondary information when present, without a separate heading or disclosure. Omit empty references. Memory text, reference lists, and expanded usage details use bounded scroll regions with complete content and keyboard access.

Sources use Tabler `offcanvas-xxl`: side-by-side reading on large screens and a native drawer on smaller screens. Tabler owns breakpoint behavior. The desktop source column stays within the viewport and remains sticky beside the document; source text scrolls independently. Opening or switching a source must not lengthen the page. On smaller screens, keep the native Offcanvas with accessible headings, tabs, and source text. Standalone source pages also keep reading within the viewport. Error regions scroll when necessary so recovery controls remain accessible. Explicit hide/dispose cleanup is reserved for HTMX removing a node before the closing transition restores scrolling. Destroy charts when their owning node is removed.

After HTMX swaps a page, css-scope-inline applies component styles through a MutationObserver. Capture the chart owner with Surreal `run`, then `await tick()` before constructing ApexCharts; skip construction if that node was removed. Measuring before scoped layout is ready causes a visibly oversized first render. Place the compact chart in a native grid column that can shrink with its container. Do not add custom resize listeners.

Keep the white background, blue actions, fine borders, left navigation and split reading layout. Centralize shared colors, borders and gutters; allocate sidebar and content widths through grid columns. Check widths from 320 to 1920 pixels, including both sides of the 991/992, 1199/1200, and 1399/1400 breakpoints. Use the CSS viewport after browser zoom: a 1536-pixel window at 200% corresponds to a 768-pixel layout. Check text within page and card boundaries, not only document scrollWidth. Reject leftover backdrops and scroll locks.

Global CSS is limited to shared theme variables, base body typography, gutters, and necessary document layout corrections. Buttons, forms, cards, focus, and transitions retain Tabler defaults. Avoid global element selectors that restyle components. Page headings and reading dimensions belong to their component-local css-scope-inline styles; logos use Tabler utilities.

Shared Tabler Dropdown controls apply language and appearance across every Dashboard page, standalone source view, and login page. Pages in the same browser load the shared preferences. Jinja2 selects interface copy; `lang=zh|en` sets a Dashboard-only preference cookie. Business records retain their original language. Preference links preserve scope, period and exact record identity and load a complete page so the root language agrees with HTMX fragments. Navigation uses stable column counts and row heights that accommodate two lines; language and appearance controls stay in one row. Responsive grid columns allocate space to titles and period controls. Scope options split into equal columns on narrow screens with room for wrapped labels. Translations may wrap naturally without changing navigation order, control grouping or main-column proportions. The source panel displays the complete text directly.

Load Tabler 1.4.0's `tabler-theme.min.js` before styles. It owns `theme=light|dark`, local storage and `data-bs-theme`. Charts use that theme with a transparent background. Do not add a separate theme state machine or breakpoint listener.

Reuse the website's `website/assets/powercontext-color.png` and `powercontext-reverse.png`, switching them with Tabler's `hide-theme-dark` and `hide-theme-light`. The favicon uses a square SVG viewport over the original left-hand symbol; omit the wordmark and do not redraw the artwork. Memory, experience and skill rows use regular weight. List text uses 16–20 px and reading text uses 16 px. Long experience and handoff headings use 22–24 px; skill names use the same hierarchy. Memories stack below the xl breakpoint. Model usage uses Tabler `table-mobile-sm` for labeled input and output values on narrow screens, with model type beneath purpose. Daily tables retain keyboard-accessible horizontal scrolling. Memory details show the text directly, without a reading heading, revision bar or record identity. Keep arrows for directional actions such as scope submission, back navigation and new windows. There is no Getting Started page; legacy `/dashboard/guide` URLs redirect home with their parameters intact.

Home memories use Tabler Card and List Group. Native grid and Flex utilities align the top and bottom card edges with the experiences and skills section. Memory navigation, reading content and experience/skill lists use independently scrollable regions with stable heights. Pagination stays at the bottom of its region when item counts or text lengths change.

Lists use six items per page and Tabler Pagination. Page the complete memory list, up to 50 memory search matches, and bounded skill-library results locally; retain Server cursors for experiences and handoffs, carrying visited cursors in navigation links for backward navigation. Do not infer totals from cursors. Memory deep links locate the selected entry's page. Page changes clear the preceding entry identity; scope changes clear pagination. Search and filter changes return to the first page. Lists may change after new saves while exact detail references remain stable.

## Test boundaries

Tests protect user-visible behavior and reproduced defects. Pagination checks cover complete traversal, returning to previous records, and opening the selected text. They do not freeze page size, CSS classes, or heading tags. Browser acceptance checks use actual API data to verify reading boundaries, scope and preference changes, source panels, and recovery. Click language and theme choices on each page and verify that scope, period, record references, and source text remain unchanged. Check expanded regions in both languages and themes, including short screens. Long sources must support wheel and keyboard reading to the end, and switching sources starts at the beginning. For the reproduced page-growth defect, compare page height before and after opening a source. Internal rewrites should preserve these tests when the experience stays the same.

Do not add coverage tests for straightforward scripts, enumerate internal errors normalized by one abstraction, or assert buffer sizes, private call order, or module ownership. Do not add tests whose only purpose is to assert that removed code, routes, or fields remain absent. Negative results remain appropriate for current authorization, isolation, and persistence contracts.

## Local verification

```bash
uv sync
uv run powercontext server --help
make check
uv run pytest tests/test_dashboard.py tests/test_server.py tests/test_access_http.py
make contract-test
```

With a configured Server running, verify readable scopes, responsive pages, navigation, sources and network recovery using real Chromium:

```bash
npm install --prefix /tmp/dashboard-browser playwright@1.61.1
/tmp/dashboard-browser/node_modules/.bin/playwright install chromium
POWERCONTEXT_BROWSER_URL=http://127.0.0.1:8765 \
POWERCONTEXT_BROWSER_OUTPUT=/tmp/dashboard-verification \
NODE_PATH=/tmp/dashboard-browser/node_modules \
node scripts/dashboard_browser.cjs
```

Use `POWERCONTEXT_REPLAY_TOKEN` when authentication is required. Screenshots and logs may contain work content; store them in a private directory outside the repository.

## Real sessions and consecutive days

Start a Server with an isolated SQLite database and local provider configuration. Match model prefixes to the actual provider protocol, such as `openai-chat:` for Chat Completions. Configuration failures must not look like absent content.

`scripts/dashboard_replay.py` reads complete user and assistant messages from an existing Codex JSONL, excluding analysis, tool output and injected environment instructions. It bounds the window by characters and records source paths, line numbers, hashes and responses. Content capture, memory extraction, experience generation and handoff preparation use existing APIs.

```bash
uv run python scripts/dashboard_replay.py \
  --output /tmp/private-dashboard-replay \
  --session /path/to/rollout.jsonl \
  --title Dashboard \
  --summary "Dashboard implementation and UI quality" \
  --max-chars 16000
```

Review candidates against the source window before using revise/approve. Save handoffs through finalize/commit. Generate skills through the existing endpoint and review them; do not replace failed generation with hand-authored content presented as a model result.

The consecutive-day experiment controls UTC time in an isolated process and restarts the Server against the same experiment database each day. It distributes complete chronological messages across three days, recalls prior content before ingesting the next window, then extracts, recalls and reads statistics. Time is a simulated condition; sources and model results use real APIs. This does not demonstrate three elapsed production days.

Every day runs memory extraction, experience generation and review, handoff preparation and commit, and skill generation and review. Skills cover `source`, `experience` and `usage` origins. For usage, an isolated Codex consumer applies a skill to the transcript, then existing content and skill-observation APIs capture its actual report. The consumer has no code, browser or test tools; validation and outcome remain unknown. Preserve a generated `no_op` without manufacturing a candidate.

`scripts/dashboard_review.py` uses the locally configured Codex CLI with tools, memories and plugins disabled. Its ephemeral review receives only the as-of-day window and records inputs, hashes, outputs, limitations and exact quotes. Rejected reviews or quotes absent from the transcript stop persistence. Inspect the failure, archive its review inputs and outputs together, then resume successful API checkpoints in the same directory. Never introduce future-day evidence. Model review does not replace human acceptance.

Replay creates a separate business scope without changing the Server default. A fresh Server retains `Default`; an existing Server retains its configured selection. Open replay pages with an explicit scope and verify the implicit default and scope switching independently.

```bash
uv run python scripts/dashboard_multiday.py \
  --output /tmp/private-dashboard-multiday \
  --session /path/to/rollout.jsonl \
  --last-line 1600 \
  --start-date 2026-09-06 \
  --env-file .env
```

Check prior citations after restart, 1024/8000-byte budgets, empty child isolation and UTC daily attribution. Use existing revise/retire operations to test current recall and historical addresses. Use scope updates to test explicit references separately from subtree statistics. All writes go through APIs; database inspection uses a read-only connection.

Each day's model usage must contain memory extraction, experience generation, handoff generation and skill generation. Candidate revision requests must preserve the original target. Read yesterday's exact revisions and search the skill library before importing today's window; reading after ingestion does not establish next-day recall.

Successful requests are cached in the replay journal. Changed payloads require a separate output directory. HTTP failures retain attempt records. Transport timeouts have unknown outcomes: reconcile through read APIs before retrying a potentially completed write.

## Ablation and acceptance

| Experiment | Required observation |
| --- | --- |
| Disable generation and Handoff Report | Saved content remains readable; generation fails explicitly |
| Grant only one scope | Read that scope without global observation access or unrelated titles |
| Deny source access | Keep the record visible and provide source-error recovery |
| Fresh scope or memories only | Independent collection states without prerequisites on other families |
| Same question with raw text, 8000 bytes and 1024 bytes | Record retained or lost constraints, next actions and citations; do not assume equivalence |
| Broad continuation question versus business keyword | Record empty recall separately from keyword hits |
| Next-day revision, retirement and reference changes | Current recall follows new state; exact historical citations remain readable |
| Network, authentication or service failure and recovery | Errors differ from empty states; navigation and scrolling recover |

A few sessions can expose specific failures but cannot establish recall quality for every question. Verification records should include configuration, bounded inputs, API requests and responses, screenshots, read-only database observations and conditions still unverified.

## Replay verification and known limits

The isolated three-day replay calls memory, experience, handoff, and skill generation each day. Skills cover source, experience, and usage origins. Read exact references after persistence and verify previous-day content before the next import. Daily model usage distinguishes all four purposes; failed and repair requests also contribute to request counts. Read-only database checks compare API entries, revisions, and daily usage, rather than merely counting stored rows.

Extraction can retain superseded constraints. This replay retained a three-page limit after the user requested all pages, and context prepared with an 8000-byte budget included that stale limit. Revising it through the existing API corrected current recall, while 25 historical memory citations still returned their original text and stale writes returned 409. Successful extraction does not establish conflict resolution or freshness.

For the same continuation question, the full transcript identified the latest page scope and unfinished check. The 8000-byte context carried an outdated scope; the 1024-byte context retained only component constraints and could not establish scope or next action. Budgets are upper limits, not returned lengths. A high reduction percentage does not establish task completeness. Preserve both original and corrected results for inspection.

An explicit scope reference lets an empty child recall parent context without copying memories into its directory or widening its subtree selection. Keep a separate unlinked empty child for fresh-start checks and preserve the server default scope.

The experiment controls UTC time in isolated processes. It exercises consecutive writes, restart reads, exact references, scope relationships, and daily statistics. It does not establish production scheduler reliability over several real days or recall quality for arbitrary queries.
