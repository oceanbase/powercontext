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
│   └── site.css
└── templates/
    ├── base.html
    ├── components/
    └── pages/
        └── dashboard.html
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

The browser authenticates against `/dashboard/scopes`, then requests `/v1/stats` with the selected `scope_id` and a
`30d` period. The Server reads one scoped snapshot and returns inventory, model usage, and recall statistics.

| Dashboard value | Source |
| --- | --- |
| Sources | Current scoped Source journal position |
| Memory entries | Entries in the current Memory Artifact |
| Artifacts | Current Artifact heads grouped by family |
| Pending review | Current Candidate heads grouped by family and status |
| Model usage | Persisted daily generation and embedding usage |
| Recall hits, token reduction, and savings trend | Persisted daily recall measurements for the configured estimator |

The Runtime performs these reads in one database transaction and calculates totals, pending Sources, family counts,
daily buckets, and token reduction on the Server. The browser presents `ready_preparations` as recall hits and plots the
signed daily `token_reduction` as the savings trend. Each heatmap cell combines those two fields for its date. Its fixed
bands are no hit, hit without a positive reduction, 1–255, 256–1023, and 1024 or more estimated tokens reduced. The
fixed thresholds keep sparse activity and outliers from changing the meaning of every other cell.

## Share only stable page structure

Put document-level structure in `base.html`. Put a fragment in `templates/components/` when it is reused or represents
a self-contained UI unit. Import `auth.js` instead of implementing token storage or bearer headers in each page. Keep
page-specific sign-in errors, data loading, and rendering in that page's static script.

Do not create a generic chart abstraction from one chart type. Share markup and styles first. Extract a JavaScript data
or rendering contract only after a second page needs the same behavior.

## Preserve the security boundary

The Dashboard shell and static assets are public so a browser can render the sign-in form. They must not contain bearer
tokens, configured scope names, statistics, or other private data. UI support endpoints and `/v1/` data endpoints remain
behind `StaticBearerMiddleware`.

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
