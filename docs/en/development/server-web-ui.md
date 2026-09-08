# Add a Server-owned web page

PowerContext serves a small multi-page web UI from the same FastAPI process as its HTTP API. Use this structure for
Server-owned pages that read PowerContext APIs. Do not introduce a separate frontend build or client-side router unless
the product requires an independently built application.

## Directory layout

The web UI is organized by responsibility:

```text
src/powercontext/server/
├── web.py
├── static/
│   ├── auth.js
│   ├── dashboard.js
│   ├── topics.js
│   └── site.css
└── templates/
    ├── base.html
    ├── components/
    └── pages/
        ├── dashboard.html
        └── topics.html
```

`web.py` owns the Jinja environment, page router, static mount, and UI support endpoints. `base.html` owns the document
head, global header and footer, and asset slots. `auth.js` owns bearer-token session storage and authenticated requests.
Page templates provide page content. Components contain complete, reusable fragments such as the login form, activity
heatmap, and recall trend.

Templates and static files are package resources. Keep them below `powercontext.server` so both editable installs and
built wheels expose the same files.

## Add a page

Create a template below `templates/pages/` and extend the shared layout:

```html
{% extends "base.html" %}

{% block title %}Page title{% endblock %}

{% block content %}
<section>
  <h1>Page heading</h1>
</section>
{% endblock %}
```

Register an explicit FastAPI route in `mount_web_ui()`. Pass the incoming `Request` to `TemplateResponse` so Jinja can
generate application URLs correctly:

```python
async def page(request: Request) -> Response:
    return _templates().TemplateResponse(
        request=request,
        name="pages/page.html",
        headers=_PAGE_HEADERS,
    )


router.add_api_route(
    "/page",
    page,
    methods=["GET"],
    response_class=HTMLResponse,
    name="page",
)
```

The root path is the Dashboard entry point. Add later pages at explicit paths and keep API routes under their existing
versioned prefixes.

## Understand Dashboard data

The browser authenticates against `/dashboard/scopes`, builds the shared Scope selector, then posts the selected
`ScopeSelection` and period to `/v1/stats`. The selector exposes three observation views: `all`, one root's `subtree`,
or one `exact` Scope. A Parent relation organizes the selector; it does not make parent data visible to a child.

| Dashboard value | Source |
| --- | --- |
| Sources | Selected Scopes' Source journal positions |
| Memory entries | Entries in the selected Scopes' Memory Artifacts |
| Artifacts | Selected Scopes' Artifact heads grouped by family |
| Pending review | Selected Scopes' Candidate heads grouped by family and status |
| Skill origin | Immutable lineage for managed Skills; registration for external Skills |
| Model usage | Persisted daily generation and embedding usage |
| Recall hits, token reduction, and savings trend | Persisted daily recall measurements for the configured estimator |

The Runtime resolves the selection to exact Scope IDs, aggregates totals, pending Sources, family counts, daily buckets,
and token reduction on the Server, and returns both the selection and resolved IDs. The browser presents
`ready_preparations` as recall hits and plots the
signed daily `token_reduction` as the savings trend. Each heatmap cell combines those two fields for its date. Its fixed
bands are no hit, hit without a positive reduction, 1–255, 256–1023, and 1024 or more estimated tokens reduced. The
fixed thresholds keep sparse activity and outliers from changing the meaning of every other cell.

The Skills page makes the origin of every item visible with the same compact badge treatment as lifecycle state. An
ordinary managed Skill is labeled Generated, an exact import is labeled Imported, a fork is labeled Forked, and an
Agent-native package that has not entered Review is labeled Local. Import, fork, and Agent-native
details also show the registration's source machine, Agent, external Skill ID, installation scope, and original location.
Later managed Revisions trace through upstream Skill lineage to the first external snapshot, so a revision does not lose
the takeover machine.

## Share only stable page structure

Put document-level structure in `base.html`. Put a fragment in `templates/components/` when it is reused or represents
a self-contained UI unit. Import `auth.js` instead of implementing token storage or bearer headers in each page. Keep
page-specific sign-in errors, data loading, and rendering in that page's static script.

Do not create a generic chart abstraction from one chart type. Share markup and styles first. Extract a JavaScript data
or rendering contract only after a second page needs the same behavior.

## Add the Handoff Report page

When Handoff Report is enabled, the Server hosts a read-only report page at `/handoff-reports`; the Dashboard remains
optional. Both pages load Scopes from `/dashboard/scopes` and use `scope-selection.js` to expose the same `all`,
`subtree`, and `exact` views.

The page posts the selected `ScopeSelection` to `/v1/handoff-reports/get`. The Server resolves it to exact Scope IDs
and projects each Scope's descriptor and latest exact Handoff. A Scope without a committed Handoff remains visible as
`no_handoff`. Parent does not infer Context sharing, and the report does not edit Handoff state.

JSON is the browser projection. Markdown download repeats the same selection with `format=markdown` and
`download=true`; the browser does not reconstruct Markdown from the rendered DOM. Disabling Handoff Report removes
the page and report API without changing Dashboard selection or statistics behavior.

## Browse Topic Memory

When the Dashboard is enabled, `/topics` provides a read-only management projection over the scopes returned by
`/dashboard/scopes`. With an empty query, the browser calls the private
`POST /dashboard/topic-memories/list` support route and follows its opaque current-head keyset cursor in recently
published order. With a focused query, it calls the public `POST /v1/topic-memory/search` operation with a limit of 20;
there is no search pagination or caller-selected retrieval mode. These are the only two ordering semantics: recent for
browse and relevance for search.

Selecting a result sends the same exact `ArtifactRef` to the private `POST /dashboard/topic-memories/get` support
route. The response adds publication time, current/historical state, the current exact ref, and direct SourceRef
identifiers to the full Topic detail already selected by the application. The page never fetches Source content or
metadata and exposes no create, edit, review, retire, publish, delete, or flush action. All generated fields are inserted
as text nodes; Topic detail is displayed as text rather than interpreted as HTML or Markdown.

Both support routes are hidden from OpenAPI and MCP and delegate to the same `TopicMemoryApplication` used by the
public operations. They exist only while the Dashboard is enabled and reject scopes absent from the configured
Dashboard list with a uniform 404.

## Preserve the security boundary

The Dashboard shell and static assets are public so a browser can render the sign-in form. They must not contain bearer
tokens, configured scope names, statistics, or other private data. UI support endpoints and `/v1/` data endpoints remain
behind `StaticBearerMiddleware`.

`DashboardConfig.scopes` controls UI discovery and is not a per-user or per-scope authorization list. The current
Bearer credential is one Server-wide token: anyone holding it can call protected Server operations for arbitrary valid
scope IDs, independent of whether those IDs appear in the Dashboard list. The two private Topic support routes add an
extra configured-scope check for the UI projection, but that check must not be represented as an ACL. Deployments that
disable authentication deliberately expose data routes according to the existing Server policy. Per-user/per-scope
authorization requires a separate authentication design.

Return Server-owned pages with the shared Content Security Policy and `Cache-Control: no-store`. Prefer external CSS
and JavaScript. The short inline script in `base.html` exists only to apply the saved theme before first paint.

## Validate behavior

Test through the public HTTP surface. Cover page routing, protected data requests, scope isolation, and data obtained
from a real database-backed Server. Assert user-visible behavior or preserve a concrete regression. Do not assert DOM
IDs, static asset paths, JavaScript source text, Jinja internals, or private function call order.

Run:

```bash
uv run pytest tests/test_dashboard.py -q
make check
make test
make build
```

After building, confirm the wheel contains `powercontext/server/templates/` and `powercontext/server/static/`.
