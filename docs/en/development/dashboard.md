# Dashboard development and verification

The Server includes a read-only Dashboard at `/dashboard/home`. It presents scope selection, handoffs, memories, experiences, skills and usage through existing HTTP APIs. `openapi/powercontext.yaml` and the Server implementation define what the pages can claim.

## Pages and contracts

| Page | Public API | Presentation rule |
| --- | --- | --- |
| Scope selection | Scope list, default and descriptor GET endpoints | An absent scope uses the Server default; an explicit empty value opens the chooser; unknown scopes produce an error |
| Home | Memory entries, exact Artifact revisions and statistics | Each section loads independently; content existence does not determine usage existence |
| Memories | `/v1/memory/entries/list`, `/v1/memory/entries/get` | Preserve complete text and line breaks; links contain the complete MemoryCitation |
| Handoffs | Scoped handoff Artifact list and revision GET endpoints | Present the recorded state and next action; list order does not imply recency |
| Experiences and skills | Scoped Experience and Skill Artifact lists and revision GET endpoints | Paginate lists; preserve complete content on detail pages |
| Source material | Scoped source GET endpoint | Verify membership in the selected record before reading; a source failure does not replace the record |
| Usage | `/v1/stats` | Use reported periods, totals, daily values, purposes and comparison coverage |

The Dashboard does not call generation endpoints or require Handoff Report, capabilities or global administration access. Existing tools and APIs own writing, review, generation and distribution. There are no placeholder actions for these operations.

Validate Artifact HTTP content in JSON mode. Strict domain tuples are arrays in JSON and cannot be validated as Python dictionaries without accounting for that representation.

## Scope semantics

An absent `scope` query reads the Server default. Switching the selector changes only the URL, clears record identities and cursors, and returns detail pages to their collection. It does not change tool bindings or the default save destination.

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
| `labels.json` | Interface copy, without business data |
| `static/` | Shared layout variables, pinned assets and licenses |

The in-process HTTP transport calls the same Server and forwards incoming credentials through its authentication and authorization checks. Templates must not read the runtime or database directly. Never inject a deployment administrator token on behalf of the browser.

A submitted Bearer credential is stored in an HttpOnly, SameSite Strict cookie scoped to `/dashboard`, with Secure on HTTPS. Session submissions require the same origin. API endpoints do not accept this cookie directly. HTML uses `no-store`; HTMX history caching is disabled so restored pages read the Server again.

## Components and visual baseline

Reuse Tabler navigation, breadcrumbs, cards, lists, forms, buttons, Accordion, Offcanvas, Alert, Spinner and Table. Charts use its ApexCharts integration. HTMX owns navigation and fragment replacement; Surreal handles necessary event connections between those frameworks.

Pinned assets are Tabler Core 1.4.0, Tabler Icons 3.31.0, HTMX 2.0.4 and ApexCharts 3.54.1. Surreal 1.3.4 uses commit `cd8f18d34067e073d0aa25675cc0649e304292a3`; css-scope-inline 1.1.0 uses `14e835ebe3b8596d0f3ee456162edf63bddc95ba`. Licenses ship beside the assets.

Use `me()`, `on()` and `off()` as described by [Surreal](https://github.com/gnat/surreal). Place styles within their component root and use `me` according to [css-scope-inline](https://github.com/gnat/css-scope-inline). Do not implement another selector or component system.

Sources use Tabler `offcanvas-xxl`: side-by-side reading on large screens and a native drawer on smaller screens. Tabler owns breakpoint behavior. Explicit hide/dispose cleanup is reserved for HTMX removing a node before the closing transition restores scrolling. Destroy charts when their owning node is removed.

Keep the white background, blue actions, fine borders, left navigation and split reading layout. Centralize shared colors, borders, sidebar width and gutters. Verify widths of 390, 1024 and 1536 pixels and the 1399/1400 source-panel boundary. Reject horizontal overflow, leftover backdrops and scroll locks.

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

```bash
uv run python scripts/dashboard_multiday.py \
  --output /tmp/private-dashboard-multiday \
  --session /path/to/rollout.jsonl \
  --last-line 1600 \
  --start-date 2026-09-06 \
  --env-file .env
```

Check prior citations after restart, 1024/8000-byte budgets, empty child isolation and UTC daily attribution. Use existing revise/retire operations to test current recall and historical addresses. Use scope updates to test explicit references separately from subtree statistics. All writes go through APIs; database inspection uses a read-only connection.

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

## Limits confirmed by replay

A three-day extraction retained an earlier three-page restriction after a later user request superseded it. Existing revise and retire endpoints corrected current recall while 23 historical citations still returned their original text. A stale write returned 409. Successful extraction does not replace review of conflicts and currency.

In independent Codex consumers answering the same continuation question, the complete conversation supplied page scope and the next action. Context retrieved with `Tabler` under an 8000-byte budget retained technology constraints only; the 1024-byte budget removed another constraint. Consumers reported insufficient evidence instead of inventing a next action. Budgets are ceilings, not returned lengths, and a high reduction percentage does not establish task completeness.

These experiments use real conversations and model calls with a controlled UTC clock in an isolated process. They verify consecutive writes, restart persistence, exact citations, scope relationships and daily attribution. They do not establish several days of scheduler reliability or arbitrary natural-language recall coverage.
