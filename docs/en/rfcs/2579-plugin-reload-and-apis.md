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
  plugins at runtime, with no Server restart. Every version that passes its check is copied into a read-only snapshot
  named by its content digest, and host processes and Workers import only from snapshots. Updating a plugin means new
  child processes use the new snapshot; no module is replaced inside a running Python process, and a call in progress
  never reads the new code.
- **Plugin-defined APIs**: a plugin can declare its own HTTP routes. Core mounts them in a separate group under
  `/v1/plugins/{name}/api/...`, performs authentication and the required authorization, and then hands the request to the
  plugin's host process. A plugin can choose to offer routes to Agents as MCP tools as well. The plugin's Artifacts remain
  readable and searchable through the generic Artifact APIs.
- **Plugin pages**: a plugin can ship static pages with its code, shown in the Dashboard in a sandboxed iframe. A page
  calls its own plugin's APIs through forwarding by the hosting page and never gets the user's session.

Deployers still only place plugin directories under `POWERCONTEXT_SERVER_PLUGINS_DIR`; core stores no plugin state in the
database and does not distribute plugin code.

This document describes the target design; it does not mean the mainline or a release already provides it. Interface
names and code examples illustrate the contract; the implementation determines the final syntax.

# Motivation

RFC #2549 defines the plugin interfaces with two limits:

- **Changes require a restart**: adding, updating, or removing a plugin restarts Server, and every API is briefly
  unavailable. When plugins change often, this hurts a shared central memory service.
- **Plugins are reachable only through existing APIs**: callers can use only core's existing Source and Artifact APIs.
  Many plugins need domain-specific operations, for example:
  - a document knowledge base needs to create knowledge bases, upload files, read original files, and run hybrid search
    across several knowledge bases;
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
2. Each call uses one version of the plugin code from start to finish: old and new code never mix, and results from old
   code are never committed under a new declaration.
3. Plugins can declare their own HTTP APIs; core handles routing, authentication, authorization, request size, and time
   limits, and the plugin writes only handlers.
4. Writes to core data through plugin APIs follow the same permission and provenance rules as user changes in RFC #2549.
5. Plugin pages never get the user's PowerContext session and cannot use it to call any Server API other than their own
   plugin's APIs.

# Guide-level explanation

## 1. Hot reload

Deployers work on the plugin directory directly:

| Action | Effect |
| --- | --- |
| Place a new plugin directory | Checked and loaded at the next scan; once the check passes, its Source types, family, APIs, and pages become available |
| Replace an existing plugin directory's contents | Checked again at the next scan; once it passes, new synchronous and background calls use the new version, and calls in progress finish on the old one |
| Remove a plugin directory | Unloaded at the next scan; its Source types, family, APIs, and pages become unavailable, and saved data is kept |

- **When changes apply**: each Server process scans every `POWERCONTEXT_SERVER_PLUGINS_RESCAN_SECONDS` (default 60
  seconds). Raise it when there are many plugins or large directories; 0 disables hot reload, which then behaves as in
  RFC #2549.
- **Replace as a whole**: a plugin directory is checked only after its contents stay the same across two consecutive
  scans. Deployers should write the new version to a temporary directory and rename it into place, so a scan never sees
  half-written files.
- **A failed update keeps the old version**: when a new version fails its check, the plugin keeps running on its current
  version and a check-failure event is recorded; the plugin switches once a fixed version is scanned. A newly placed
  plugin that fails its check is not loaded.
- **Install dependencies first**: plugin dependencies are installed in Server's Python environment, not in the plugin
  directory, so scans do not detect dependency changes. When a new version needs new dependencies, deployers install them
  before replacing the plugin directory.
- **Multiple instances**: each Server instance scans the plugin directory on its own machine. Deployers distribute the
  code to every instance; until every instance has scanned, instances may briefly run different versions.
- **Plugins upgrade their own data**: a plugin can provide `upgrade(ctx)` in its declaration. Core calls it once every
  time it loads a plugin version, and the plugin decides whether and how to upgrade. Core does not know where plugin data
  lives or what format it has, and does not check compatibility; if a new version cannot read or consume old data, that
  is the plugin's responsibility. A plugin can stay compatible with old formats in code, rewrite them once in `upgrade`,
  or both.

## 2. Plugin-defined APIs

A plugin adds `api` to its declaration to list its routes; handlers are methods on the object `create(ctx)` returns. The
document plugin below maps each knowledge base to a Scope: creating a knowledge base creates a Scope, uploaded files go
into plugin storage, and the Source records only their location and digest:

```python
from pydantic import BaseModel

from powercontext.plugins import ApiError, ApiRequest, BinaryResponse, PluginApi, Route, SourcePlugin


class NewKnowledgeBase(BaseModel):
    title: str
    summary: str
    idempotency_key: str


class KnowledgeBase(BaseModel):
    scope_id: str


class UploadResult(BaseModel):
    source_id: str


class Documents:
    def __init__(self, ctx) -> None:
        self.ctx = ctx

    async def create_knowledge_base(self, request: ApiRequest) -> KnowledgeBase:
        body: NewKnowledgeBase = request.body
        # Creates a Scope as the caller, with the same permission as POST /v1/scopes (server.admin)
        scope = await self.ctx.scopes.create(title=body.title, summary=body.summary,
                                             idempotency_key=body.idempotency_key)
        return KnowledgeBase(scope_id=scope.scope_id)

    async def upload(self, request: ApiRequest) -> UploadResult:
        # The original bytes go into plugin storage by content digest; the Source records only location and digest
        location, digest = await save_original(self.ctx.storage, request.scope_id, request.body)
        ref = await self.ctx.sources.submit(
            request.scope_id, "document-input",
            DocumentInput(location=location, sha256=digest, name=request.query["name"][0]),
        )
        return UploadResult(source_id=ref.source_id)

    async def original(self, request: ApiRequest) -> BinaryResponse:
        # Dedicated storage is not isolated by Scope: look up by (scope_id, source_id) and treat files of other
        # knowledge bases as missing
        found = await load_original(self.ctx.storage, request.scope_id, request.path_params["source_id"])
        if found is None:
            raise ApiError(404, "document_not_found", "document not found in this knowledge base")
        return BinaryResponse(content=found.content, content_type=found.media_type)


async def create(ctx) -> Documents:
    return Documents(ctx)


plugin = SourcePlugin(
    definitions=(DocumentInputDefinition(),),
    api=PluginApi(routes=(
        Route(method="POST", path="/knowledge-bases", handler="create_knowledge_base", access="authenticated",
              body="json", request_model=NewKnowledgeBase, response_model=KnowledgeBase),
        Route(method="POST", path="/knowledge-bases/{kb_id}/documents", handler="upload",
              access="scope.contribute", scope_param="kb_id", body="binary", response_model=UploadResult),
        Route(method="GET", path="/knowledge-bases/{kb_id}/documents/{source_id}/original", handler="original",
              access="scope.read", scope_param="kb_id"),
    )),
    create=create,
)
```

Callers use it like this:

```text
POST /v1/plugins/documents/api/knowledge-bases                                    (JSON body)
POST /v1/plugins/documents/api/knowledge-bases/{kb_id}/documents?name=sop.md      (body is the file bytes)
GET  /v1/plugins/documents/api/knowledge-bases/{kb_id}/documents/{source_id}/original
GET  /v1/plugins/documents/openapi.json                                            (OpenAPI description of the plugin's APIs)
```

- Plugin routes form a separate group under `/v1/plugins/{name}/api/`; the plugin decides the paths, and they never mix
  with core's routes.
- Core authenticates the caller first, then checks permissions according to the route's `access`:
  - `scope.read`, `scope.contribute`: `scope_param` names the path parameter that holds the Scope ID, and core checks the
    caller's permission on it before calling the plugin;
  - `authenticated`: the caller only needs to be signed in, and the handler checks any other permission with
    `ctx.require(...)`, for example checking each Scope when searching across several knowledge bases.
- Handlers can read and write core data under the same rules as user changes in RFC #2549: writes are made as the caller,
  and core checks permissions and records provenance.
- A plugin can create Scopes as the caller, with the same permission as `POST /v1/scopes`, which requires `server.admin`.
- Artifacts of a plugin family are still browsed, read, searched, and changed through the generic Artifact APIs; plugin
  APIs only add domain-specific operations.
- When a route declares `mcp=True`, core also offers it as an MCP tool, and Agents call it like a built-in tool, for
  example to search across knowledge bases.
- Plugin APIs are not part of `openapi/powercontext.yaml`, and generated clients do not include them; core
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
- A plugin page runs in an opaque origin: it cannot read the Dashboard cookie, and requests it sends to Server directly
  carry no user session. To call its own plugin's APIs, the page asks the Dashboard to forward the request through
  `postMessage`, and the Dashboard calls as the signed-in user.
- A page can link to, navigate to, or open external sites in a new window, for example handing search results to an
  external RAG service's page. Plugins are trusted code, so pages may reach external sites freely; core guarantees only
  that a page cannot borrow the user's PowerContext session.
- Pages can only be static files (HTML, JavaScript, CSS, images, fonts), which core serves directly from the plugin's
  current version snapshot without running plugin code.

Calls from a page:

```javascript
const client = powercontextPluginClient();            // a small script from core that wraps postMessage
const { scopeId } = await client.context();
const result = await client.request("GET", `/knowledge-bases/${scopeId}/documents/${sourceId}/original`);
```

# Reference-level explanation

## 1. Hot reload

### 1.1 Scanning and checks

- Each Server process computes a content digest for every direct subdirectory of the plugin directory at startup and
  every scan interval.
- A digest equal to the current version is skipped; a digest that differs from the previous scan waits, and a directory is
  checked only once its digest is the same for two consecutive scans and differs from the current version, so
  half-written directories are never checked.
- The check is RFC #2549's startup check, run on a version snapshot (see 1.3); the plugin is added, updated, or unloaded
  according to the result.

- The digest is computed from file contents, like the code fingerprint in RFC #2549. File sizes and modification times
  are not enough: a new version unpacked from an archive with normalized timestamps can have exactly the same sizes and
  modification times as the old one.
- Scan cost is proportional to the total bytes in the plugin directories. On a 2-core development machine, one digest
  over 14 MB in 676 files takes about 230 ms; plugin directories hold no dependencies and are usually much smaller. Each
  scan's duration is recorded as a metric, so deployers can tune the scan interval.
- The check child process computes a declaration digest over the serializable part of the declaration, kept in the active
  plugin record to tell whether the declaration changed.
- The first scan at startup does not wait for settling, matching RFC #2549's startup loading.

### 1.2 Switching

| Case | Handling |
| --- | --- |
| Added, check passes | Register Source types, the family (reads, search, merge registry), plugin APIs, and pages; register the binding when this process is the Supervisor leader |
| Added, check fails | Not loaded and a check failure is recorded; the plugin shows as `failed` in `GET /v1/plugins` |
| Updated, check passes, declaration unchanged | Switch to the new snapshot: replace the host process (see 1.4); background calls use the new snapshot from the next dispatch |
| Updated, check passes, declaration changed | Register again from the new declaration: added Source types, routes, and pages take effect and removed ones become unavailable; the binding's scheduling parameters follow the new declaration; switch to the new snapshot |
| Updated, check fails | Keep using the current snapshot and record the check failure; `GET /v1/plugins` shows the category of the latest check failure |
| Removed | Unregister Source types, the family, APIs, pages, and the binding; stop the host process once in-flight synchronous calls finish or time out |

- Renaming a family is removing the old family and adding a new one. Artifacts of the old family stay in core and are
  handled as when a plugin is disabled in RFC #2549.
- A removed Source type no longer accepts writes and returns 422; saved Sources are kept.
- Invocation intents left behind by a removed plugin are no longer processed, under the same rule as RFC #2549's
  execution constraints; a plugin placed back under the same name continues from its existing cursor and checkpoints.
- The Supervisor leader process registers bindings. When leadership moves, the new leader registers bindings from its own
  active plugin record.

### 1.3 Version snapshots

Deployers can change the plugin directory at any time, so code cannot be imported from it directly: after the directory
is replaced, a deferred import not yet executed in a running process reads the new files, and one call mixes old and new
code. Plugin code is therefore imported only from version snapshots:

- **Creation**: once the digest settles, core copies the plugin directory into a read-only snapshot under the data
  directory, named by its content digest, and runs the check on the snapshot. Dependencies are not in the plugin
  directory, so snapshots are usually small.
- **Use**: check child processes, host processes, and Workers put the snapshot directory on the import path, and
  `ctx.plugin_dir` points to the snapshot; page static files are also served from the current snapshot. The plugin
  directory itself is used only for scanning.
- **Replaces the code fingerprint check**: in RFC #2549, Workers and host processes check the code fingerprint before
  importing. With snapshots, the dispatch hands the snapshot path to the Worker, and since snapshots never change, no check
  is needed; with hot reload disabled, RFC #2549's check still applies.
- **Cleanup**: a snapshot that is neither the current version nor in use by a host process or Worker of this process is
  deleted at the next scan. At startup, Server creates snapshots only for the current plugin directories and deletes all
  others. Snapshots are derived local files that can be regenerated from the plugin directory and need no backup.

### 1.4 Host process replacement and background calls

- Once a new version passes its check, core starts a new host process from the new snapshot; when it is ready, new
  synchronous calls and plugin API requests go to it.
- The old process accepts no new requests and finishes in-flight ones on the old snapshot; it is terminated once a call
  exceeds its time limit.

- If the new host process fails to start, this is handled as a failed check: the old process and old snapshot stay in use.
- A Worker dispatch carries the snapshot path. Workers already dispatched finish on the old snapshot and commit under the
  old version's declaration, equivalent to "finished just before the update"; newly dispatched Workers use the new
  snapshot. The existing Supervisor lease fence still fences stale commits after a leadership change.
- Each call runs in exactly one snapshot, so synchronous calls, plugin APIs, and background calls never mix old and new
  code, and no persisted registration generation is needed.

### 1.5 Differences between processes

The active plugin record lives only in each process's memory and is not synchronized across processes:

- Until every instance has scanned, the same request can get different results on different instances (for example, one
  instance has loaded a new plugin and another has not).
- `GET /v1/plugins` and `GET /v1/plugins/{name}` return the view of the instance that handles the request. Responses add
  `version_digest` (the current snapshot's content digest), `declaration_digest`, `state` (`loaded`, `failed`),
  `activated_at`, and the category of the latest check failure, so deployers can compare instances.
- Background calls run only in the leader's Workers, on the leader's local snapshots; synchronous calls run on the
  instance that receives the request, on that instance's current snapshot.

### 1.6 Plugin data upgrades

```python
async def upgrade(ctx: PluginContext) -> None:
    """Called each time this version loads; decides whether existing data needs upgrading."""

plugin = ArtifactPlugin(..., create=create, upgrade=upgrade, upgrade_timeout_seconds=600)
```

- **When**: on every load of a plugin version, both at Server startup and when hot reload switches to a new version, not
  only on first install. The plugin records the version of its data itself (for example in its dedicated storage or in
  Artifact content) and compares it with the version in its code to decide what to do.
- **Where**: a new host process runs `upgrade(ctx)` before `create(ctx)` and reports ready only afterwards; plugins that
  declare `upgrade` start their host process as soon as they load. Until the new version is ready, core forwards no
  synchronous calls or API requests to it and dispatches no background calls with the new snapshot.
- **Failure**: if `upgrade` raises or exceeds `upgrade_timeout_seconds` (600 seconds by default), core treats the new
  version as failing its check: hot reload keeps the old version, and at Server startup the plugin is `failed`.
- **Context**: dedicated storage, models, and the logger. `ctx.reads(scope_id)` and `ctx.writer(scope_id)` act as the
  plugin itself, as in a Worker, without checking a caller's permissions, so the plugin can rewrite old-format Artifacts as
  new revisions. Core does not list the Scopes that hold plugin data; the plugin walks its own records. Earlier Artifact
  revisions are immutable, so code that reads history still has to understand old formats.
- **Repetition and concurrency**: every Server instance calls `upgrade` on every load, and core does not coordinate
  instances. `upgrade` must be safe to run repeatedly and on several instances at once; a plugin that needs mutual
  exclusion takes a lock in its dedicated storage.
- **Old and new versions side by side**: while instances finish scanning one by one, or while hot reload switches over,
  the old version may still read and write the same data. Plugins should first make changes both versions can use, and
  clean up in a later version once the old one no longer runs.
- **No downgrades**: core does not record plugin data versions and does not stop older code from loading; if older code
  cannot read newer data, that is the plugin's responsibility.
- **Source Definitions**: core registers Source Definitions by name and version, so old and new versions coexist. A new
  plugin version that still needs to read Sources of an old version keeps declaring the old Definition.

## 2. Plugin-defined APIs

### 2.1 Declaration

```python
class Route(BaseModel):
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str                                   # relative path starting with /, may contain {parameters}
    handler: str                                # method name on the object create(ctx) returns
    access: Literal["authenticated", "scope.read", "scope.contribute"]
    scope_param: str | None = None              # required for Scope access: the path parameter holding the Scope ID
    body: Literal["none", "json", "binary"] = "none"
    request_model: type[BaseModel] | None = None    # validates a json body and describes the API
    response_model: type[BaseModel] | None = None
    timeout_seconds: float = 30
    description: str | None = None              # API description and MCP tool description
    mcp: bool = False                           # also offer the route as an MCP tool

class PluginApi(BaseModel):
    routes: Sequence[Route]
```

- Both `SourcePlugin` and `ArtifactPlugin` can declare `api`. A plugin that declares `api` needs a host process.
- Routes are mounted at `/v1/plugins/{name}/api{path}`. `(method, path)` must be unique within a plugin; `handler` must
  exist and be an async method, which the check child process validates.
- When `access` is `scope.read` or `scope.contribute`, `scope_param` must be a parameter in `path`; core checks the
  caller's permission on that Scope and, once it passes, sets it as `ApiRequest.scope_id`. When `access` is
  `authenticated`, `scope_param` must not be set and `scope_id` is empty.
- Routes that write core data must declare `scope.contribute`. A route cannot declare `server.admin` itself; operations
  that need administrator permission are checked in the handler with `ctx.require("server.admin")`, or by core inside
  `ctx.scopes`.

### 2.2 Requests and responses

```python
class Caller(BaseModel):
    principal_id: str                           # caller identity; no credentials

class ApiRequest(BaseModel):
    scope_id: str | None                        # the Scope named by scope_param; None for authenticated routes
    route: str                                  # the path template from the declaration
    path_params: dict[str, str]
    query: dict[str, list[str]]
    body: BaseModel | bytes | None              # per body: a request_model instance, bytes, or None
    caller: Caller

class BinaryResponse(BaseModel):
    content: bytes
    content_type: str
    filename: str | None = None

class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None: ...   # status must be 4xx
```

- A handler returns a `response_model` instance (serialized as JSON with status 200), a `BinaryResponse`, or `None`
  (204).
- An `ApiError` returns its status, which must be 400–499 and cannot be 401 or 403 (core decides authentication and
  authorization); any other exception returns 500 with only the exception type logged.
- Request and response sizes are limited: 1 MiB for JSON request bodies, and `POWERCONTEXT_SERVER_PLUGIN_API_MAX_BODY_BYTES`
  (default 32 MiB) for binary request bodies and responses. Core returns 413 without calling the plugin when a limit is
  exceeded.
- Exceeding `timeout_seconds` returns 504; an unavailable host process returns 503; a missing plugin or route returns 404.
- Core provides no idempotency keys or request records for plugin APIs, and the plugin defines retry semantics; when it
  needs idempotency, the plugin requires callers to supply an idempotency key in the request.

### 2.3 What handlers can do

Handlers run in the host process; `create(ctx)` runs only once per host process, and each request carries its Scope (as
in RFC #2549). Besides the RFC #2549 context (dedicated storage, models, logger), plugin API calls provide:

| Capability | Description |
| --- | --- |
| `ctx.require(action, scope_id=None)` | Checks whether the caller has `scope.read`, `scope.contribute` (with `scope_id`), or `server.admin`, and raises otherwise, so the API returns 403. Used in `authenticated` routes and cross-Scope operations |
| `ctx.reads(scope_id)` | A read-only Artifact view of the given Scope, checked against the caller's permissions before reading; `reads(...).reads(family)` reads other families |
| `ctx.writer(scope_id)` | The Artifact plugin's writer, scoped to the given Scope and the plugin's family; usable only with `request.scope_id` of a `scope.contribute` route |
| `ctx.sources.submit(scope_id, definition, value)` | Submits one Source of a definition this Source plugin provides, with the same validation, `resolve`, and Projections as the Source write APIs, and returns a `SourceRef`; usable only with `request.scope_id` of a `scope.contribute` route |
| `ctx.scopes.create(*, title, summary, idempotency_key, parent_scope_id=None)` | Creates a Scope as the caller, reusing the implementation of `POST /v1/scopes`: the caller needs `server.admin`, becomes the administrator of the new Scope, and the same idempotency key returns the same Scope |
| `ctx.scopes.update(scope_id, *, expected_version, title=None, summary=None)` | Changes a Scope's title and summary as the caller, with the same permission as `PUT /v1/scopes/{scope_id}`; only for Scopes this plugin created |
| `ctx.scopes.list()` | Lists the Scopes this plugin created that the caller can see |

Write rules:

- Writes from plugin APIs are made as the caller, with the same permissions as the generic APIs: creating needs
  contribute permission on the Scope, and the caller owns the result; changing an existing Artifact needs the caller's
  write permission on it (`artifact.write`). Core checks each write operation as it runs and raises when the check
  fails, so the API returns 403.
- For every write that creates or changes content, core records a `lineage_only` Content Source under RFC #2549's
  user-change rules.
- A request opens at most one write transaction block, and the writes in it commit in one transaction, under RFC
  #2549's write transaction rules.
- Handlers cannot advance cursors or checkpoints, and cannot write another plugin's family.
- Dedicated storage is not isolated by Scope (see RFC #2549 reference 1.2). Handlers read and write their own data by
  `request.scope_id` and must not return data on another ID in the path alone; otherwise a caller with permission on one
  Scope could read another Scope's data.

Scopes created by plugins:

- At creation, core adds the external reference `{"kind": "powercontext.plugin", "value": "<plugin name>"}` to the Scope;
  `ctx.scopes.list()` and `ctx.scopes.update()` use it to decide ownership, and deployers can see which plugin created a
  Scope.
- The permission to create Scopes is not relaxed for plugins. Letting non-administrators create Scopes (for example,
  child Scopes under a parent) requires changing core's authorization model and is out of scope for this proposal.
- A plugin's business configuration (for example, a knowledge base's chunking strategy) is not written into the Scope;
  the plugin stores it in its own storage keyed by `scope_id`. The Scope is created first and the configuration written
  after it; if writing the configuration fails, the caller retries with the same idempotency key, gets the same Scope,
  and writes again.

### 2.4 MCP tools

Core generates MCP tools from routes that declare `mcp=True`, on the same MCP endpoint as Server's existing tools:

- Only routes whose `body` is `none` or `json` can opt in; binary routes cannot, and the check fails if they do.
- The tool name is `<plugin name>_<handler>` (with `-` replaced by `_`), and its description comes from `description`,
  which is required when opting in. Tool parameters combine path parameters, query parameters, and `request_model`; the
  parameter named by `scope_param` is the Scope ID.
- Calling the tool is the same as calling the route as the MCP session's identity, with the same authentication,
  `access` authorization, time limits, and error handling as HTTP calls.
- `GET` routes whose `access` is `scope.read` or `authenticated` are marked read-only, and others are marked as writes, so
  hosts can decide whether to ask the user for confirmation.
- When hot reload adds, updates, or removes a plugin, its tools are added or removed; MCP clients see the change the next
  time they list tools, and sessions core can notify receive a tool-list-changed notification.
- Only routes a plugin explicitly opts in become tools, so the number of tools does not crowd Agents' context.

### 2.5 API description

`GET /v1/plugins/{name}/openapi.json` (authentication required) returns the OpenAPI document core generates from the route
declarations: paths, methods, permission requirements, JSON Schemas of `request_model` and `response_model`, and the
shared error responses. Each plugin owns the compatibility of its APIs; for incompatible changes, the plugin can put a
version in its paths (for example, `/v2/knowledge-bases`).

## 3. Plugin pages

### 3.1 Declaration and static files

```python
class PluginPages(BaseModel):
    directory: str = "ui"                       # relative path inside the plugin directory
    entry: str = "index.html"
    title: dict[str, str]                       # navigation title per language; must include en
```

- Static files are served at `/dashboard/plugins/{name}/ui/{path}`, only from `directory` in the current snapshot;
  `..` and symbolic links pointing outside the directory are rejected.
- Only these types are allowed: `.html`, `.js`, `.css`, `.json`, `.svg`, `.png`, `.jpg`, `.webp`, `.woff2`; each file is at
  most 5 MiB.
- Static files hold only plugin code and assets, no user data, so they require no sign-in.
- The CSP in the response headers:
  - A `sandbox` directive makes the page run in an opaque origin even when opened directly, so it cannot read the
    Dashboard cookie. Its direct requests to Server are cross-site, so the browser does not send the `SameSite=Strict`
    session cookie, and the page has no API credentials.
  - Scripts can come only from the plugin snapshot, so the code a page runs matches the plugin version.
  - Links, forms, popups, and requests to external HTTPS sites are all allowed: plugins are trusted code and can send
    users to external services or call their public APIs. None of these carry the user's PowerContext session.
  - Only the Dashboard may embed the page.

### 3.2 Hosting page and forwarding

- **Hosting page**: `/dashboard/plugins/{name}` is a core Dashboard page that requires sign-in. It loads the plugin page
  in a sandboxed iframe and, after the iframe's first load, sends the page the current Scope, locale, and theme; it then
  accepts request messages only from that iframe, requires the path to match a route the plugin declared, forwards the
  request through a same-origin server-side call, and returns the result as a message (binary responses as a Blob).
- **Server-side forwarding**: `/dashboard/plugins/{name}/api/...` is a Dashboard server route. It requires a Dashboard
  session and a `Sec-Fetch-Site` of `same-origin` (checking that `Origin` matches Server when the header is missing), and
  follows the Dashboard's existing approach of calling `/v1/plugins/{name}/api/...` as the session's identity.
- **Session isolation**: the Dashboard session cookie is `HttpOnly`, `SameSite=Strict`, with path `/dashboard`. A plugin
  page is in an opaque origin, so its scripts cannot read the cookie, and Server does not accept forwarding requests with
  `Origin: null`.
- **Stop forwarding after navigation**: a sandboxed iframe can navigate itself to an external site, which CSP cannot
  prevent. The external page can also send `postMessage` to the hosting page and passes the `event.source` check. The
  hosting page therefore stops forwarding after the iframe's second load, so an external page cannot borrow the user's
  session. Plugin pages should be single-page applications; navigating between plugin pages also stops forwarding.
- **Scope**: the hosting page forwards only to routes the plugin declared; forwarded requests are still checked by core
  against the caller's permissions, so a plugin page cannot use forwarding to reach Scopes the caller cannot access.
- **Page client script**: core serves a small script at `/dashboard/plugins/_client.js` that wraps this message protocol,
  and plugin pages can reference it directly.

## 4. Execution constraints

- Plugin APIs run in host processes and share RFC #2549's concurrency limit, timeout termination, and restart backoff
  rules for synchronous calls.
- The Server process reads a plugin API request body and checks its size, then passes it to the host process as bytes or
  a validated model; `request_model` validation runs in the host process and returns 422 on failure.
- The Server process reads static page files directly from the current snapshot, without the host process. After a
  version switch, new requests read the new snapshot's files immediately.

## 5. Persistence

No database data is added. The plugin set, versions, route tables, and page registrations exist only in each Server
process's memory and are rebuilt from the plugin directory after a restart. Version snapshots are local files under the
data directory and can be regenerated from the plugin directory at any time. Core data written through plugin APIs is
the same as in RFC #2549; Scopes created by plugins are ordinary Scopes; plugins keep their own data in plugin storage.

## 6. API changes

| Change | Content |
| --- | --- |
| Added | `/v1/plugins/{name}/api/...`: routes plugins declare |
| Added | `GET /v1/plugins/{name}/openapi.json` |
| Added | `/dashboard/plugins/{name}`, `/dashboard/plugins/{name}/ui/...`, `/dashboard/plugins/{name}/api/...`, `/dashboard/plugins/_client.js` |
| Added | Routes a plugin declares with `mcp=True` appear as MCP tools on Server's MCP endpoint |
| Added | `powercontext.plugins`: `PluginApi`, `Route`, `ApiRequest`, `BinaryResponse`, `ApiError`, `Caller`, `PluginPages`; the context's `require`, `reads`, `writer`, `sources`, and `scopes` |
| Changed | `SourcePlugin` and `ArtifactPlugin` gain `api`, `pages`, `upgrade`, and `upgrade_timeout_seconds` fields |
| Changed | Responses of `GET /v1/plugins` and `GET /v1/plugins/{name}` add `version_digest`, `declaration_digest`, `state`, `activated_at`, the category of the latest check failure, the route list, and page information |
| Added | Environment variables `POWERCONTEXT_SERVER_PLUGINS_RESCAN_SECONDS` (default 60; 0 disables hot reload) and `POWERCONTEXT_SERVER_PLUGIN_API_MAX_BODY_BYTES` (default 32 MiB) |

`openapi/powercontext.yaml` describes only `/v1/plugins/{name}/openapi.json` and the `/v1/plugins` changes, not the
plugins' own routes.

## 7. Observability

| Field | Meaning |
| --- | --- |
| `plugin_name` | Plugin name |
| `plugin_event` | `loaded`, `updated`, `removed`, `check_failed`, `upgrade_failed` |
| `version_digest` | Content digest of the snapshot that took effect |
| `declaration_digest` | Declaration digest that took effect |
| `plugin_route` | Route template of a plugin API (for example, `GET /knowledge-bases/{kb_id}/documents/{source_id}/original`), without actual parameter values |
| `plugin_method` | From RFC #2549, adding `api` |
| `scan_ms` | Duration of one scan from start to finish, in milliseconds |

- Each load, update, removal, and check failure records one event; a check failure records only its known failure
  category, never the exception message.
- Metric labels may use `plugin_name`, `plugin_route`, and the response status class (2xx, 4xx, 5xx), never Scope IDs,
  path parameters, or query parameters.
- Logs exclude request bodies, response bodies, and file contents. New fields are registered in the allowlist per the
  developer guide and documented in both locales' observability docs.

# Drawbacks

- **Multi-instance inconsistency window**: instances scan independently, so plugin versions can differ for up to one scan
  interval, and callers can see different results on different instances.
- **Ongoing scan cost**: each Server process periodically reads the full contents of the plugin directories to compute
  digests, and the cost grows with their total bytes.
- **Snapshots use disk space**: each version keeps a copy of the plugin directory under the data directory, and old
  versions are cleaned up once unused.
- **Dependency changes are not detected**: plugin dependencies live in the shared Python environment, so deployers install
  dependency updates first; when dependencies and plugin code are incompatible, the check fails.
- **Plugin APIs are outside the main contract**: plugin routes are not in `openapi/powercontext.yaml` and have no generated
  clients, and the names and parameters of routes offered as MCP tools change with the plugin; plugins own their
  compatibility.
- **Plugin pages must be single-page applications**: forwarding stops after a page navigates, so plugin pages that span
  several documents need to be reopened.
- **Handlers are trusted code**: host processes isolate ordinary failures and are not a sandbox. Plugin APIs write core
  data as the caller, so a bug in plugin code can write wrong data in the user's name, as with RFC #2549's user changes.

# Rationale and alternatives

| Option | Trade-off |
| --- | --- |
| File system watching (inotify and similar) | Faster, but behavior differs across platforms, is unreliable in containers and on network file systems, and delivers events for half-written files; periodic scans with a settling check are simpler |
| Detecting changes by file size and modification time | No need to read file contents, but after unpacking an archive with normalized timestamps, different contents can have the same sizes and modification times, and the update is ignored forever |
| A management API that triggers reloads | Affects only the instance that receives the request and needs separate distribution across instances; periodic scans cover every instance naturally. Whether to add a "scan this instance now" API is an unresolved question |
| Persisting the plugin set and registration generations (old Artifact plugin framework) | Keeps instances strictly consistent, but brings back desired state, management phases, and generation fencing, the mechanisms RFC #2549 just removed |
| Re-importing plugin modules inside the Server process | Python cannot reliably replace loaded modules: a plugin's sibling modules enter `sys.modules` under top-level names, and old objects remain referenced. Replacing child processes avoids this |
| Child processes import from the plugin directory directly, fenced by fingerprints | No copying, but after the directory is replaced, deferred imports in running processes read the new files and one call mixes old and new code; a fingerprint check before commit cannot protect synchronous calls either |
| Importing from version snapshots (this proposal) | Each call is pinned to one immutable version, and a failed update can keep the old version; the cost is copying and cleaning up snapshots |
| Disabling the plugin when an update fails | Simple, but one bad deployment makes all of the plugin's synchronous calls and APIs unavailable; with snapshots, keeping the old version costs nothing extra |
| Plugin APIs in the Server process | Lowest latency, but a blocked or crashed plugin affects the whole Server and plugin code would be imported into the Server process again |
| Dispatching a Worker per plugin API request | Reuses existing machinery, but starts a new Python process for every request, with latency in seconds |
| Mounting every plugin route under a Scope | One authorization rule, but operations that belong to no Scope, such as creating a knowledge base, have nowhere to go; instead plugins choose their paths and declare Scope authorization with `scope_param` |
| Letting plugins declare `server.admin` routes | Plugins could provide administrative features, but this adds a second administrator authorization at the route layer; the first version checks it in handlers with `ctx.require` |
| Relaxing permissions when plugins create Scopes | Ordinary users could create knowledge bases, but this changes core's rule that administrators create Scopes and deserves its own proposal |
| Plugin pages on the Dashboard's origin | Simplest to implement, but page scripts could call any Dashboard or Server API with the user's session, which amounts to impersonating the user |
| Forbidding plugin pages from reaching external sites | Requires restricting requests, forms, popups, and navigation together, which CSP cannot do completely; plugins are trusted code whose server side can reach external sites anyway, so forbidding it for pages gains nothing |
| Plugin pages on a separate port or domain | Also isolates the origin, but deployers must configure extra ports, certificates, and reverse proxies; a sandboxed iframe needs no extra configuration |
| Server-side rendering of plugin pages by plugin code | Pages get data directly, but every page request enters the host process, and templates and escaping rules would need to be defined; static pages plus API calls are simpler |
| Making every plugin route an MCP tool automatically | Needs no extra declaration, but the number of tools grows with plugins and crowds Agents' context, and binary routes do not suit tools; instead routes opt in, and only JSON routes can |
| Core records each plugin's data version and passes the old version to the plugin (Moodle, Home Assistant) | Plugins need not track versions, but in those projects the host keeps the data being migrated. Here only the plugin knows where its dedicated storage is and what it holds, and the plugin defines its Artifact content format, so a version recorded by core would not reflect the actual state of that data |
| Core runs plugin-provided migrations in phases (Kong's up and teardown) | Handles rolling upgrades across instances, but requires core to understand plugin storage and provide a migration runner and commands. This proposal calls one hook and asks plugins to make compatible changes first and clean up after the old version stops |
| Plugins decide how to upgrade data in a hook (this proposal, as in Backstage and WordPress) | Core only defines when the hook runs and how failures are handled, and knows nothing about plugin data; the cost is that compatibility rests entirely with the plugin, with no fallback in core |
| No MCP tools for plugin APIs | Least to implement, but Agents are PowerContext's main users, and a plugin's domain operations (such as searching across knowledge bases) would be unavailable to them. Existing MCP tools are already generated from HTTP APIs, and plugin routes can reuse the same mechanism |

# Prior art

- **Dify plugin daemon**: each plugin package is unpacked into its own working directory named
  `<author-name-version>@<checksum>`, and its runtime starts from that directory; a new version uses a new directory and
  does not affect the old one. This proposal's version snapshots follow the same approach, while dependencies still share
  Server's Python environment.
- **Old Artifact plugin framework**: stores a content fingerprint of the plugin directory and verifies it again before a
  Worker loads the plugin. This proposal keeps the content-based digest.
- **Chrome extensions**: extension pages run in the extension's own origin and interact with web pages and the browser
  through declared permissions and message passing, without using the page's session. Plugin pages follow the same
  isolation idea: an opaque origin plus message forwarding that can only call the plugin's own APIs.
- **VS Code webviews**: extension UIs run in iframes, communicate with the extension through `postMessage`, and are
  expected to set a CSP. The plugin page message protocol is similar.
- **Dify plugin endpoints**: plugins can declare HTTP endpoints that Dify mounts and forwards to. This proposal's plugin
  APIs are likewise mounted by the host, with authentication and authorization done by core.
- **Plugin data upgrades**: Backstage backend plugins run their own database migrations during initialization, and the
  framework explicitly does not handle plugin schema migrations. WordPress activation hooks do not reliably run when a
  plugin updates, so plugins commonly store their own data version and compare it on every load. `upgrade` follows the
  same division of work and runs on every load. Kong splits plugin migrations into a non-destructive `up` and a cleanup
  `teardown` so old and new nodes can run side by side; this proposal states that as a requirement on plugins. Moodle and
  Home Assistant record versions in the host and pass the old version in, which suits data the host keeps.
- **Graceful reloads in process managers such as Gunicorn**: start new workers first, then let old workers finish
  existing requests and exit. Host process replacement follows the same order.
- **Existing PowerContext foundations**: RFC #2549's host processes, active plugin record, user-change rules, and
  dedicated plugin storage; the Supervisor's bindings, lease fence, and backoff; the Scope creation API and its
  idempotency key; the Dashboard's session and server-side calls.

# Unresolved questions

| Question | Notes |
| --- | --- |
| Immediate scans | Whether to provide `POST /v1/plugins/rescan` (`server.admin`) so deployers can trigger a scan of this instance now; with multiple instances it affects only the instance that receives it |
| Default scan interval | Whether 60 seconds fits; whether to limit the bytes read per scan for large plugin directories |
| API rate limits | Whether plugin APIs get per-route or per-caller rate limits, or keep Server's existing ingress limits |
| Plugin pages in Desktop | Whether the Desktop app embeds plugin pages, and how sandboxing and CSP behave in its WebView |
| Browser behavior verification | How `'self'` matches subresources under the CSP `sandbox` directive, and how `SameSite` applies to opaque-origin requests, need verification in major browsers before the final headers are fixed |
| Streaming binary requests | The first version reads the whole request body in the Server process before passing it to the host process; whether large files need streaming |

# Future possibilities

- Plugin pages shown in the Desktop app.
- Plugins calling each other through declared APIs.
- Generated clients or SDK helpers for plugin APIs.
