---
title: Plugin hot reload and custom APIs
description: Building on the host processes of plugin interfaces RFC #2549, plugins can be added, updated, and removed at runtime, and can declare their own HTTP APIs and Dashboard pages, which core mounts, authorizes, and isolates.
---

- Proposal Name: `plugin_reload_and_apis`
- Start Date: 2026-11-10
- RFC PR: [oceanbase/powercontext#2579](https://github.com/oceanbase/powercontext/pull/2579)

# Summary

This proposal adds three capabilities on top of [plugin interfaces RFC #2549](https://github.com/oceanbase/powercontext/pull/2549):

- **Hot reload**: each Server process scans the plugin directory periodically and applies added, updated, or removed
  plugins at runtime, with no Server restart. Plugin code is imported only in check child processes, host processes, and
  Workers, so updating a plugin means replacing those child processes; no module is replaced inside a running Python
  process.
- **Plugin-defined APIs**: a plugin can declare its own HTTP routes. Core mounts them at
  `/v1/plugins/{name}/api/scopes/{scope_id}/...`, performs authentication and Scope authorization first, and then hands
  the request to the plugin's host process.
- **Plugin pages**: a plugin can ship static pages with its code, shown in the Dashboard in a sandboxed iframe. A page can
  call only its own plugin's APIs, through forwarding by the hosting page, and never gets the user's session.

Deployers still only place plugin directories under `POWERCONTEXT_SERVER_PLUGINS_DIR`; core stores no plugin state and
does not distribute plugin code.

This document describes the target design; it does not mean the mainline or a release already provides it. Interface
names and code examples illustrate the contract; the implementation determines the final syntax.

# Motivation

RFC #2549 defines the plugin interfaces with two limits:

- **Changes require a restart**: adding, updating, or removing a plugin restarts Server, and every API is briefly
  unavailable. When plugins change often, this hurts a shared central memory service.
- **Plugins are reachable only through existing APIs**: callers can use only core's existing Source and Artifact APIs.
  Many plugins need domain-specific operations, for example:
  - a document knowledge base needs file upload, paged reads of chunks at a fixed revision, reads of original files, and
    hybrid search across several knowledge bases;
  - a plugin needs to show users its own pages for processing progress, configuration, or search results.

  These operations do not fit core's generic APIs, and plugins have no other entry point.

RFC #2549 already lays the groundwork for hot reload and plugin-defined APIs:

- the Server process imports no plugin code, synchronous calls run in host processes core manages, and each background
  call starts a new Worker;
- each Server process keeps an in-memory record of its active plugins and persists no plugin state.

Hot reload in the old Artifact plugin framework (RFC #2175) relied on persisted desired state, management phases, and
registration generations. This proposal does not bring those mechanisms back: the plugin set is decided by the directory
contents, and switching happens only at the child process level.

Goals:

1. Deployers place, replace, or remove a plugin directory, and Server applies it within one scan interval, with no restart
   and no management API call.
2. A call already started during an update either completes with the old code or fails and is retried; results from old
   code are never committed under a new declaration.
3. Plugins can declare their own HTTP APIs; core handles routing, authentication, authorization, request size, and time
   limits, and plugins write only the handlers.
4. Writes to core data from plugin APIs follow the same permission and provenance rules as user changes in RFC #2549.
5. Plugin pages are isolated from the Dashboard: page code cannot read the user's session or call any Server API other
   than its own plugin's APIs.

# Guide-level explanation

## 1. Hot reload

Deployers work on the plugin directory directly:

| Action | Effect |
| --- | --- |
| Add a new plugin directory | Checked and loaded at the next scan; once the check passes, its Source types, family, APIs, and pages become available |
| Replace the contents of an existing plugin directory | Checked again at the next scan; once the check passes, new synchronous and background calls use the new code |
| Remove a plugin directory | Unloaded at the next scan; its Source types, family, APIs, and pages stop being available, and saved data is kept |

- **When it applies**: each Server process scans every `POWERCONTEXT_SERVER_PLUGINS_RESCAN_SECONDS` (30 seconds by
  default). Setting it to 0 turns hot reload off, and the behavior matches RFC #2549.
- **Replace as a whole**: a plugin directory is checked only after its contents are unchanged across two consecutive
  scans. Deployers should write the new version to a temporary directory and rename it into place, so a scan never sees
  half-written files.
- **A failed update keeps existing data usable**: when a new version fails its check, the plugin becomes `degraded`:
  reads and search keep working (they run no plugin code), synchronous calls and plugin APIs return 503, and background
  calls pause until a later version passes the check.
- **Multiple instances**: each Server instance scans the plugin directory on its own machine. Deployers distribute the
  code to every instance; until every instance has scanned, different instances may briefly run different versions.

## 2. Plugin-defined APIs

A plugin adds `api` to its declaration and lists its routes; the handlers are methods on the object `create(ctx)`
returns:

```python
from pydantic import BaseModel

from powercontext.plugins import ApiRequest, BinaryResponse, PluginApi, Route, SourcePlugin


class UploadResult(BaseModel):
    source_id: str


class Documents:
    def __init__(self, ctx) -> None:
        self.ctx = ctx

    async def upload(self, request: ApiRequest) -> UploadResult:
        # Original bytes go into the plugin's dedicated storage; the Source records only the location and digest
        location, digest = await save_original(self.ctx.storage, request.body, request.query["name"][0])
        ref = await self.ctx.sources.submit("document-input", DocumentInput(location=location, sha256=digest))
        return UploadResult(source_id=ref.source_id)

    async def original(self, request: ApiRequest) -> BinaryResponse:
        content, media_type = await load_original(self.ctx.storage, request.path_params["source_id"])
        return BinaryResponse(content=content, content_type=media_type)


async def create(ctx) -> Documents:
    return Documents(ctx)


plugin = SourcePlugin(
    definitions=(DocumentInputDefinition(),),
    api=PluginApi(routes=(
        Route(method="POST", path="/documents", handler="upload", access="scope.contribute",
              body="binary", response_model=UploadResult),
        Route(method="GET", path="/documents/{source_id}/original", handler="original", access="scope.read"),
    )),
    create=create,
)
```

Callers use it like this:

```text
POST /v1/plugins/documents/api/scopes/team-a/documents?name=sop.md     (the body is the file bytes)
GET  /v1/plugins/documents/api/scopes/team-a/documents/{source_id}/original
GET  /v1/plugins/documents/openapi.json                                (the OpenAPI description of this plugin's APIs)
```

- Every route is mounted under a Scope. Core authenticates the caller, checks the caller's permission on the Scope in the
  path (the route's declared `access`), and then hands the request to the plugin's host process.
- Handlers can read and write core data under the same rules as user changes in RFC #2549: writes are made as the caller,
  and core checks permissions and records provenance.
- When a handler involves other Scopes (for example, searching several knowledge bases), it checks the caller's
  permission on each with `ctx.require(...)`.
- Plugin APIs are not part of `openapi/powercontext.yaml`, and generated clients and MCP do not include them; core
  generates each plugin's API description from its declaration.

## 3. Plugin pages

A plugin adds `pages` to its declaration, pointing to static files in the plugin directory:

```python
from powercontext.plugins import PluginPages

plugin = SourcePlugin(
    ...,
    pages=PluginPages(directory="ui", entry="index.html", title={"zh": "文档", "en": "Documents"}),
)
```

- The plugin's page appears in the Dashboard navigation. When opened, the Dashboard loads it in a sandboxed iframe under
  the current Scope.
- The plugin page runs in an opaque origin: it cannot read the Dashboard's cookies or call Server directly. It asks the
  Dashboard to forward requests through `postMessage`, and the Dashboard forwards only to this plugin's APIs, as the
  signed-in user.
- Pages are static files only (HTML, JavaScript, CSS, images, fonts). Core serves them directly from the plugin directory
  and runs no plugin code for them.

Calls from a page:

```javascript
const client = powercontextPluginClient();            // a small script core provides that wraps postMessage
const { scopeId } = await client.context();
const result = await client.request("GET", `/documents/${sourceId}/original`);
```

# Reference-level explanation

## 1. Hot reload

### 1.1 Scanning and checking

```text
In each Server process, every rescan interval (and at startup):
  -> list the direct subdirectories of the plugin directory and compute a fingerprint for each: the relative path,
     size, and modification time of every file (excluding __pycache__)
  -> the fingerprint matches the last scan and the active version: skip
  -> the fingerprint differs from the last scan: mark it as settling and do not check it this round
  -> the fingerprint is the same in two consecutive scans and differs from the active version: check it in a
     time-limited check child process (the same check as RFC #2549's startup check)
  -> add, update, degrade, or remove according to the result
```

- The fingerprint uses only file metadata and reads no file contents, so scan cost is proportional to the number of files
  in the plugin directory. Plugin dependencies are installed in Server's Python environment, not in the plugin directory,
  and are not scanned.
- The check child process computes a declaration digest over the serializable part of the declaration. It is kept in the
  active plugin record and used to tell whether the declaration changed.
- The first scan at startup does not wait for settling, matching RFC #2549's startup loading.

### 1.2 Switching

| Case | Handling |
| --- | --- |
| Added, check passes | Register its Source types, family (reads, search, merge registry), APIs, and pages; register its binding when this process is the Supervisor leader |
| Updated, check passes, declaration unchanged | Replace the host process (see 1.3); background calls use the new code from the next dispatch |
| Updated, check passes, declaration changed | Re-register from the new declaration: added Source types, routes, and pages take effect and removed ones stop being available; the binding's scheduling parameters follow the new declaration; replace the host process |
| Updated, check fails | Becomes `degraded`: the read registrations of its Source types and family are kept, the host process stops, the plugin's binding pauses, and synchronous calls and plugin APIs return 503 |
| Removed | Unregister its Source types, family, APIs, pages, and binding; stop the host process after in-flight synchronous calls finish or time out |

- Renaming a family is the same as removing the old family and adding a new one. The old family's Artifacts stay in core
  and are handled as for a disabled plugin in RFC #2549.
- A removed Source type no longer accepts writes and returns 422; saved Sources are kept.
- Invocation intents left behind by a removed plugin are no longer processed, as in RFC #2549's execution constraints;
  when a plugin with the same name comes back, it continues from its cursor and checkpoints.
- The leader process registers Supervisor bindings. When leadership moves, the new leader registers bindings from its own
  active plugin record.

### 1.3 Replacing the host process

```text
The new version passes its check
  -> start a new host process, import the new code, and compare the declaration
  -> once the new process is ready, send new synchronous calls and plugin API requests to it
  -> the old process accepts no new requests and waits for in-flight ones; it is terminated once a call exceeds its limit
```

If the new process fails to start, this is handled as a failed check (`degraded`), and the old process stops too: the old
code is no longer on disk, so there is no guarantee that modules it imports later match the parts already loaded.

### 1.4 Fencing background calls

A Worker re-imports the plugin on every call. To avoid committing results from old code under a new declaration, the
executor passes the plugin directory's fingerprint to the Worker at dispatch:

- before importing the plugin, the Worker recomputes the fingerprint and fails without calling the plugin if it differs
  from the one at dispatch;
- before committing the write transaction block, it computes the fingerprint again; if it differs, it rolls back, the call
  fails, and the Supervisor redispatches after backoff.

The Worker and the plugin directory are on the same machine, so comparing fingerprints needs no persisted state and no
registration generation. The existing Supervisor lease fence still blocks stale commits after a leadership change. A small
window remains between the last check and the commit: what is committed then is the old code's result under the old
declaration, equivalent to "finished just before the update".

### 1.5 Differences between processes

The active plugin record lives only in each process's memory and is not synchronized across processes:

- Until every instance has scanned, the same request may get different results on different instances (for example,
  one instance has loaded a new plugin and another has not).
- `GET /v1/plugins` and `GET /v1/plugins/{name}` return the view of the instance that handled the request. The response
  gains `declaration_digest`, `state` (`loaded`, `degraded`), and `activated_at`, so deployers can compare instances.
- Background calls run only in the leader's Workers, fenced as in 1.4; synchronous calls run on the instance receiving the
  request, with that instance's current version.

## 2. Plugin-defined APIs

### 2.1 Declaration

```python
class Route(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str                                   # relative path starting with /, may contain {parameters}
    handler: str                                # a method name on the object create(ctx) returns
    access: Literal["scope.read", "scope.contribute"]
    body: Literal["none", "json", "binary"] = "none"
    request_model: type[BaseModel] | None = None    # validates a json body and feeds the API description
    response_model: type[BaseModel] | None = None
    timeout_seconds: float = 30

class PluginApi(BaseModel):
    routes: Sequence[Route]
```

- Both `SourcePlugin` and `ArtifactPlugin` can declare `api`. A plugin that declares `api` needs a host process.
- Routes are mounted at `/v1/plugins/{name}/api/scopes/{scope_id}{path}`. `(method, path)` must be unique within a plugin;
  `handler` must exist and be an async method, which the check child process verifies.
- `access` is the permission the caller must have on the Scope in the path. A route that writes core data must declare
  `scope.contribute`.
- Plugins cannot declare global routes outside a Scope, nor routes that require `server.admin`.

### 2.2 Requests and responses

```python
class Caller(BaseModel):
    principal_id: str                           # caller identity; carries no credentials

class ApiRequest(BaseModel):
    scope_id: str
    route: str                                  # the path template from the declaration
    path_params: dict[str, str]
    query: dict[str, list[str]]
    body: BaseModel | bytes | None              # per the body declaration: a request_model instance, bytes, or None
    caller: Caller

class BinaryResponse(BaseModel):
    content: bytes
    content_type: str
    filename: str | None = None

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None: ...   # status must be 4xx
```

- A handler returns an instance of `response_model` (serialized as JSON with status 200), a `BinaryResponse`, or `None`
  (204).
- An `ApiError` returns its status, which must be within 400–499 and must not be 401 or 403 (core decides authentication
  and authorization); any other exception returns 500, and only the exception type is logged.
- Requests and responses have size limits: 1 MiB for JSON request bodies, and
  `POWERCONTEXT_SERVER_PLUGIN_API_MAX_BODY_BYTES` (32 MiB by default) for binary request bodies and responses. Beyond
  them, core returns 413 without calling the plugin.
- Exceeding `timeout_seconds` returns 504; an unavailable host process or a `degraded` plugin returns 503; an unknown
  plugin or route returns 404.
- Core provides no idempotency keys or request records for plugin APIs; the plugin defines retry semantics.

### 2.3 What handlers can do

Handlers run in the host process, and `create(ctx)` runs once per Scope and is then reused (as in RFC #2549). Besides the
RFC #2549 context (dedicated storage, models, logger), plugin API calls provide:

| Capability | Description |
| --- | --- |
| `ctx.require(action, scope_id)` | Checks whether the caller has `scope.read` or `scope.contribute` on the given Scope and raises otherwise, so the API returns 403. Used for operations across Scopes |
| `ctx.reads(scope_id=None)` | A read-only Artifact view of the given Scope (the path's Scope by default), checked against the caller's permission before reading; `reads(...).reads(family)` reads other families |
| `ctx.writer` | The Artifact plugin's writer, provided only on `scope.contribute` routes, scoped to the path's Scope and this plugin's family |
| `ctx.sources.submit(definition, value)` | A Source plugin submits one Source of a definition it provides, with the same validation, `resolve`, and Projections as the Source write API, and gets a `SourceRef`; provided only on `scope.contribute` routes |

Write rules:

- Writes from plugin APIs are made as the caller, with the same permissions as the generic APIs: creating needs the Scope
  contribute permission, and the caller owns the result; changing an existing Artifact needs the caller's write
  permission on it (`artifact.write`). Core checks each write operation as it runs and raises when the check fails, so
  the API returns 403.
- For every write operation that creates or changes content, core records a `lineage_only` Content Source under RFC
  #2549's user-change rules.
- A request opens at most one write transaction block, whose writes commit in one transaction, following RFC #2549's write
  transaction rules.
- Handlers cannot advance cursors or checkpoints and cannot write other plugins' families.

### 2.4 API description

`GET /v1/plugins/{name}/openapi.json` (authenticated; readable with any Scope permission) returns an OpenAPI document
that core generates from the route declarations: paths, methods, the JSON Schema of `request_model` and `response_model`,
and the common error responses. The plugin owns its API compatibility; for an incompatible change it can put a version in
its paths (for example `/v2/documents`).

## 3. Plugin pages

### 3.1 Declaration and static files

```python
class PluginPages(BaseModel):
    directory: str = "ui"                       # relative path inside the plugin directory
    entry: str = "index.html"
    title: dict[str, str]                       # navigation title per locale; must include en
```

- Static files are served at `/dashboard/plugins/{name}/ui/{path}`, only from under `directory`; `..` and symlinks that
  point outside are rejected.
- Only these types are allowed: `.html`, `.js`, `.css`, `.json`, `.svg`, `.png`, `.jpg`, `.webp`, `.woff2`; each file is
  at most 5 MiB.
- Static files contain only plugin code and assets, no user data, so they do not require sign-in.
- Response headers:

  ```text
  Content-Security-Policy: sandbox allow-scripts allow-forms allow-downloads;
                           default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline';
                           img-src 'self' data:; font-src 'self'; connect-src 'none'; frame-ancestors 'self'
  X-Content-Type-Options: nosniff
  ```

  The `sandbox` directive keeps the page in an opaque origin even when opened directly; `connect-src 'none'` prevents
  the page from making network requests itself; `frame-ancestors 'self'` allows only the Dashboard to embed it.

### 3.2 The hosting page and forwarding

```text
/dashboard/plugins/{name}?scope=...            (a core Dashboard page; requires sign-in)
  -> renders <iframe sandbox="allow-scripts allow-forms allow-downloads" src="/dashboard/plugins/{name}/ui/{entry}">
  -> after the iframe loads, sends {type: "powercontext.context", scopeId, locale, theme}
  -> on {type: "powercontext.request", id, method, path, query, body} from the iframe
     -> accepts messages only from that iframe (checks event.source)
     -> path must match a route this plugin declares
     -> fetch /dashboard/plugins/{name}/api/scopes/{scopeId}{path} (same origin, with the session cookie)
     -> replies {type: "powercontext.response", id, status, body} (binary responses come back as a Blob)
```

- **Server-side forwarding**: `/dashboard/plugins/{name}/api/...` is a server-side Dashboard route. It requires a
  Dashboard session and a `Sec-Fetch-Site` of `same-origin` (or, when that header is missing, an `Origin` equal to
  Server's). Following the Dashboard's existing approach, it calls `/v1/plugins/{name}/api/...` as the identity of the
  session.
- **Session isolation**: the Dashboard session cookie is `HttpOnly` and `SameSite=Strict` with path `/dashboard`. The
  plugin page is in an opaque origin, so its scripts cannot read the cookie; its direct requests to Server are cross-site,
  so browsers do not attach the session, and Server rejects forwarding requests with `Origin: null`.
- **Scope**: the hosting page forwards only to routes this plugin declares, with the Scope fixed to the one currently
  selected in the Dashboard. The plugin page cannot switch to a Scope the user has no access to, because core still checks
  the forwarded request against the caller's permissions.
- **Page client script**: core serves a small script at `/dashboard/plugins/_client.js` that wraps this message protocol,
  and plugin pages can reference it directly.

## 4. Execution constraints

- Plugin APIs run in the host process and share RFC #2549's concurrency limit, timeout termination, and restart backoff
  with other synchronous calls.
- The Server process reads a plugin API request body and checks its size, then passes it to the host process as bytes or
  as a model to validate; `request_model` validation runs in the host process and returns 422 on failure.
- The Server process reads static page files directly, without the host process. After hot reload replaces the
  directory, new requests read the new files immediately.

## 5. Persistence

No persisted data is added. The plugin set, versions, route tables, and page registrations exist only in each Server
process's memory and are rebuilt from the plugin directory when Server restarts. Core data written by plugin APIs is the
same as in RFC #2549; plugins keep their own data in their dedicated storage.

## 6. API changes

| Change | Content |
| --- | --- |
| Added | `/v1/plugins/{name}/api/scopes/{scope_id}/...`: routes plugins declare |
| Added | `GET /v1/plugins/{name}/openapi.json` |
| Added | `/dashboard/plugins/{name}`, `/dashboard/plugins/{name}/ui/...`, `/dashboard/plugins/{name}/api/...`, `/dashboard/plugins/_client.js` |
| Added | `powercontext.plugins`: `PluginApi`, `Route`, `ApiRequest`, `BinaryResponse`, `ApiError`, `Caller`, `PluginPages` |
| Changed | `SourcePlugin` and `ArtifactPlugin` gain the `api` and `pages` fields |
| Changed | Responses of `GET /v1/plugins` and `GET /v1/plugins/{name}` gain `declaration_digest`, `state`, `activated_at`, the route list, and page information |
| Added | Environment variables `POWERCONTEXT_SERVER_PLUGINS_RESCAN_SECONDS` (default 30; 0 turns hot reload off) and `POWERCONTEXT_SERVER_PLUGIN_API_MAX_BODY_BYTES` (default 32 MiB) |

`openapi/powercontext.yaml` describes only the changes to `/v1/plugins/{name}/openapi.json` and `/v1/plugins`, not the
routes plugins declare.

## 7. Observability

| Field | Meaning |
| --- | --- |
| `plugin_name` | Plugin name |
| `plugin_event` | `loaded`, `updated`, `degraded`, `removed`, `check_failed` |
| `declaration_digest` | The declaration digest now in effect |
| `plugin_route` | The plugin API route template (for example `GET /documents/{source_id}/original`), without actual parameter values |
| `plugin_method` | As in RFC #2549, with the new value `api` |

- Each load, update, degradation, and removal records one event; a failed check records only the known failure
  category, never the exception message.
- Metric labels may use `plugin_name`, `plugin_route`, and the response status class (2xx, 4xx, 5xx), never Scope IDs,
  path parameters, or query parameters.
- Logs exclude request bodies, response bodies, and file contents. New fields are registered in the allowlists per the
  developer guide and documented in both locales' observability docs.

# Drawbacks

- **Multi-instance deployments have an inconsistency window**: instances scan independently, so plugin versions can differ
  for up to one scan interval, and callers may see different results on different instances.
- **Scanning has an ongoing cost**: each Server process lists every file in the plugin directory periodically. A large
  plugin directory (for example, one that bundles dependencies) costs more.
- **Plugin APIs are outside the main contract**: plugin routes are not in `openapi/powercontext.yaml`, have no generated
  clients, and do not appear in MCP; plugins own their compatibility.
- **A failed update makes the plugin temporarily unavailable**: after a failed check the plugin is `degraded`, and
  synchronous calls and plugin APIs return 503 until a fixed version is scanned. The old version is not kept, because its
  code is no longer on disk.
- **Plugin pages are limited**: pages cannot make network requests themselves and can call only their own plugin's APIs
  through the hosting page; a page that needs external resources needs the plugin's API to proxy them.
- **Handlers are trusted code**: the host process isolates ordinary failures and is not a sandbox. Plugin APIs write core
  data as the caller, so a defect in plugin code can write wrong data in the user's name, the same as user changes in
  RFC #2549.

# Rationale and alternatives

| Option | Trade-off |
| --- | --- |
| File system watching (inotify and similar) | Faster, but behavior differs across platforms, is unreliable in containers and on network file systems, and reports half-written files; periodic scans with a settling check are simpler |
| A management API that triggers reloading | Affects only the instance receiving the request, so multi-instance deployments need fan-out; periodic scans cover every instance naturally. Whether to add a "rescan this instance now" API is an unresolved question |
| Persisted plugin set and registration generations (the old Artifact plugin framework) | Keeps instances strictly consistent, but brings back desired state, management phases, and generation fencing, the mechanisms RFC #2549 just removed |
| Re-importing plugin modules in the Server process | Python cannot reliably replace loaded modules: a plugin's sibling modules enter `sys.modules` under top-level names, and old objects stay referenced. Replacing child processes avoids this |
| Fencing old Workers with persisted generations | The Worker and the plugin directory are on the same machine, so comparing directory fingerprints is enough, with no database state |
| Keeping the old version when an update fails | Workers import from disk on every call and the old code has been replaced, so the old version cannot be kept for the host process alone; degrading with an explicit 503 is more predictable |
| Running plugin APIs in the Server process | Lowest latency, but a blocking or crashing plugin affects all of Server, and plugin code would be imported into the Server process again |
| Dispatching a Worker per plugin API request | Reuses the existing mechanism, but each request starts a new Python process, with latency measured in seconds |
| Plugins declaring global or `server.admin` routes | Plugins could offer administration features, but it would bypass the Scope authorization boundary; the first version allows only routes within a Scope |
| Plugin pages on the Dashboard's origin | Simplest to implement, but page scripts could call any Dashboard and Server API with the user's session, effectively impersonating the user |
| Plugin pages on a separate port or domain | Also isolates the origin, but deployers must configure extra ports, certificates, and reverse proxies; a sandboxed iframe needs no extra configuration |
| Server-side rendering of plugin pages by plugin code | Pages get data directly, but every page request enters the host process, and templates and escaping rules would need defining; static pages plus API calls are simpler |
| Exposing plugin APIs as MCP tools automatically | Convenient for Agents, but needs tool descriptions, parameter mapping, and budget constraints; discussed separately |

# Prior art

- **Chrome extensions**: extension pages run in the extension's own origin and interact with web pages and the browser
  through declared permissions and message passing, without using the web page's session. Plugin pages here follow the
  same isolation idea: an opaque origin, plus message forwarding that can call only the plugin's own APIs.
- **VS Code webviews**: an extension's UI runs in an iframe, talks to the extension through `postMessage`, and is expected
  to set a CSP. The plugin page message protocol is similar.
- **Dify plugin endpoints**: plugins can declare HTTP endpoints that Dify mounts and forwards to. Plugin APIs here are
  likewise mounted by the host, with Scope authorization added.
- **Graceful reload in process managers such as Gunicorn**: start new workers first, then let old ones finish their
  requests and exit. Host process replacement uses the same order.
- **Existing PowerContext foundations**: RFC #2549's host processes, active plugin record, user-change rules, and
  dedicated plugin storage; the Supervisor's bindings, lease fence, and backoff; and the Dashboard's session and
  server-side calling pattern.

# Unresolved questions

| Question | Notes |
| --- | --- |
| Immediate rescan | Whether to offer `POST /v1/plugins/rescan` (`server.admin`) so deployers can trigger a scan of this instance at once; in multi-instance deployments it affects only the instance receiving the request |
| Default scan interval | Whether 30 seconds is right; whether to cap files per scan for large plugin directories |
| Rate limiting | Whether plugin APIs get per-route or per-caller rate limits, or reuse Server's existing entry limits |
| MCP exposure | Whether and how plugin APIs become MCP tools for Agents |
| Plugin pages in Desktop | Whether the Desktop app embeds plugin pages, and how sandboxing and CSP behave in its WebView |
| Browser behavior | How `'self'` matches subresources under the CSP `sandbox` directive, and how `SameSite` applies to opaque-origin requests, need verifying in mainstream browsers before the final headers are fixed |
| Streaming binary requests | The first version reads the whole request body in the Server process before passing it to the host process; whether large files need streaming |

# Future possibilities

- Plugin APIs as MCP tools that Agents call directly.
- Plugin pages shown in the Desktop app.
- Plugins calling each other through declared APIs.
- Generated clients or SDK helpers for plugin APIs.
