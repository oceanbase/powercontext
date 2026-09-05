- Proposal Name: `desktop_control_center`
- Start Date: 2026-09-04
- RFC PR: [oceanbase/powercontext#1455](https://github.com/oceanbase/powercontext/pull/1455)
- Tracking Issue: [oceanbase/powercontext#1428](https://github.com/oceanbase/powercontext/issues/1428)
- Status: Proposed

# Summary

Build a PowerContext desktop control center with **Tauri 2, a shared Web management interface, and the existing
independent Python Server**. The application helps users install and diagnose PowerContext, connect to a local or
remote Server, organize Scopes, inspect and manage Memory and reviewed assets, and act on Handoff and Review items.
Rust owns narrowly scoped desktop capabilities; the Server remains authoritative for domain behavior, persistence,
authorization, and durable work. Installation and native service management retain their existing owners.

The proposed first qualified platform is **Windows 11 x64 with the SQLite backend**. macOS and Linux follow the same
architecture but require their own installation, security, update, and usability acceptance. A personal preview may
ship earlier; completing #1428 additionally requires the authorization and durable Handoff delivery dependencies
described below. Merging this RFC does not close the tracking issue.

# Motivation

PowerContext already has a Python SDK, HTTP APIs, Agent integrations, a Server-owned Web UI, and native per-user service
management. A user still needs to understand several different installation, configuration, version, and diagnosis
surfaces to answer simple questions: Is my Server running? Is my Agent using the correct Scope? What needs review?
Where did a Handoff go? Can I update safely without losing data?

The desktop should make those questions answerable from one application. Its value comes from native installation
and lifecycle visibility, protected credential storage, notifications, and a consistent management interface. It
should preserve PowerContext's existing ability to serve multiple Agents independently of any open application window.

This proposal serves three users:

- A new individual user who wants a working local Server and one selected Agent integration without preparing Python
  or building source code.
- An existing CLI or Web user who wants convenient management of an existing deployment without an automatic
  relocation of data or replacement of configuration.
- A user of a team-operated Server who needs authenticated, authorized access to shared work and exact Handoff items.

It does not introduce a chat client, IDE, Agent Runtime, autonomous task orchestrator, new Memory engine, or desktop
database replica. It also does not make the desktop a mandatory dependency of the SDK, CLI, Server, or integrations.

# Guide-level explanation

## What users install and where it lives

Users install a native application called **PowerContext Desktop**. It has its own window, application icon, and,
where available, tray entry. The source lives in the PowerContext repository. Desktop releases contain a trusted local
UI and native host. A local setup also installs a separately versioned Python runtime environment through the unified
installer. A remote-only setup needs neither a local Python runtime nor a local PowerContext Server.

There are two connection choices:

| Choice | User experience | Ownership |
| --- | --- | --- |
| This computer | Set up or connect to this OS user's local Server; inspect its service and integrations | The existing service layer and OS service manager own the Server |
| Remote Server | Enter an HTTPS endpoint and credential; inspect the connected Server and permitted resources | The remote operator owns runtime installation, data, and service lifecycle |

Connection profiles remember endpoint settings and a reference to a protected credential. The window shows one active
connection and its current Scope selection. Local service controls are available only for a verified, locally managed
installation. Connecting to a remote Server never turns its configuration into a local service definition.

## First successful use

1. The application explains local and remote setup. For local setup it displays the release, components, data
   location, selected Agent hosts, and changes the installer proposes to make.
2. The user approves that concrete plan. The installer verifies immutable artifacts, installs the runtime and selected
   integrations, registers the per-user service, and reports component results. Failures retain a clear recovery path.
3. The user can start with a minimal configuration without a model. Explicit Memory storage and available full-text
   recall provide the first success path. Generation, extraction, vector retrieval, and model-dependent features show
   their actual capability requirements.
4. The user chooses or creates a Scope, explicitly stores a small Memory item, and recalls it from the same Scope.
   Source import is also available, but accepting a Source does not mean extraction has produced Memory.
5. The selected Agent integration is checked separately. The UI distinguishes installation from successful host
   loading and a real capture/recall check; it does not label an untested integration healthy.
6. The user can find pending Review items and, when supported, the Handoff inbox. A notification opens the exact
   authorized item after the application refreshes its current state.

An existing installation is discovered and inspected before any change is proposed. A reachable process with unknown
ownership can be connected to, but the desktop does not kill it, replace its environment, or take ownership of its port.

## Main screens

| Screen | Required behavior | Boundary |
| --- | --- | --- |
| Overview | Active connection, Server readiness, local service state, supported features, attention items, recovery actions | Readiness, installation, authentication, and authorization are separate states |
| Projects and workstreams | Present Scopes, organization, and `all` / `subtree` / `exact` observation selections; manage supported bindings | Project/workstream names are presentation labels for Scopes, not a second identity system |
| Memory and assets | List/search/read Memory; explicitly remember, revise, or retire through supported APIs; inspect Experience, Skills, provenance, and lifecycle | Preserve exact references and existing Review/publication rules |
| Review | Filter and inspect Candidate items; approve, reject, or revise with conflict feedback | Server-authorized actions on the displayed Candidate version |
| Handoff | Exact authorized Handoff detail, read-only reports, and the delivery inbox when available | Access discovery, delivery, viewing, acknowledgement, and task outcome remain distinct |
| Sources and connectors | Import selected content, inspect supported Source state, and show available connector health and recovery | Background ingestion belongs to Server/connector workers; absent management APIs are shown as unavailable |
| Agents and integrations | Select maintained distributions; show declared support, installed version, diagnostics, and permitted binding changes | Reuse distribution and installer contracts; do not rewrite host adapters |
| Settings and diagnostics | Connections, credentials, language, notification choices, data locations, versions, updates, and redacted diagnostics | Remote administration requires separately advertised and authorized APIs |

The first release does not promise a complete connector marketplace, a Handoff editor, execution of arbitrary Skills,
or every operation exposed by the SDK. Supported pages must still complete their stated user journey; unavailable
features cannot appear as working placeholder controls.

## Closing the window and working offline

Closing the last window hides the application when the tray is available. Explicit Quit exits the desktop. If the tray
is unavailable, the window explains its close behavior and offers a clear Quit action. Neither action unregisters or
terminates the independently managed Server. Starting the desktop at login and starting the Server at login are
separate settings.

Notifications require the desktop process to be running in this release. The Server retains durable work and the
Handoff inbox while the desktop is closed; opening the application refreshes authoritative state. An offline remote
connection shows its disconnection and does not queue domain writes. A local Server can still provide capabilities
that do not require an unavailable remote model or service.

# Reference-level explanation

## 1. Baseline and dependent work

The implementation baseline is upstream `master` at
[`f0f288abecaccb97e1fe97d991b87b808bbebfbd`](https://github.com/oceanbase/powercontext/commit/f0f288abecaccb97e1fe97d991b87b808bbebfbd),
checked on 2026-09-04. The following are implementation facts at that baseline, not claims about a released desktop:

| Existing surface | Reusable capability | Gap relevant to this RFC |
| --- | --- | --- |
| Public HTTP contract | Scopes and bindings, Memory, Source ingestion, Candidates, Skills, exact Handoff operations, statistics and reports | No desktop compatibility handshake or durable Handoff delivery inbox contract |
| Web UI | Jinja templates and JavaScript modules for Overview, Skills, Review, and Handoff Report | Some support routes are under `/dashboard`; desktop business access must use public APIs |
| Native service layer | `service install`, `service status --json`, `service uninstall`; independent per-user service registration | No existing public `service start/stop/restart --json` interface |
| Configuration | Minimal Server configuration validation without inference; model capabilities remain optional | Desktop onboarding and protected configuration editing are not implemented |
| Authentication | Optional deployment-wide static Bearer authentication | Resource-level Principal/role enforcement is not implemented at this baseline |
| Integration manifest and diagnostics | Version-specific capability declarations and structured integration checks | Neither is a live Handoff receiver registry |
| Released package | Published `0.1.0` remains distinct from development `master` | `0.1.0` does not include the native `service` command |

The installation documentation now distinguishes released and unreleased paths. The desktop must use a release whose
manifest explicitly supports the required runtime/service contracts, or clearly label a pinned prerelease. It must not
silently install moving `master`, combine unrelated integration/runtime revisions, or advertise `0.1.0` service support.

| Dependency | State at baseline | Required coordination |
| --- | --- | --- |
| [RFC 1299](1299_local_server_availability_and_service_installation.md) | Service architecture and implementation available on `master` | Preserve one service owner and structured status semantics |
| [RFC 1345](1345_scope_organization_and_agent_integration.md) | Scope model and integration contracts available | Reuse Scope identity, organization, bindings, and explicit publication |
| [RFC 1396](1396_handoff_access_control.md), implementation [#1398](https://github.com/oceanbase/powercontext/pull/1398) | RFC merged; implementation PR open | Team/resource-sharing acceptance requires Server-side enforcement and authorized discovery |
| [#1419](https://github.com/oceanbase/powercontext/issues/1419) | Handoff delivery tracking issue open | Owns receiver enrollment, envelopes, durable inbox, delivery states, retry, expiry, and recovery |
| [#1406](https://github.com/oceanbase/powercontext/issues/1406), RFC [#1408](https://github.com/oceanbase/powercontext/pull/1408) | Installation tracking issue and RFC PR open | Owns bootstrap, plans, component installation, version records, and recovery |
| [#1405](https://github.com/oceanbase/powercontext/issues/1405), RFC [#1410](https://github.com/oceanbase/powercontext/pull/1410) | Distribution tracking issue and RFC PR open | Owns canonical Agent distributions, target profiles, and host configuration rules |
| [RFC 1400](1400_source_definition_and_observation_model.md) | Source identity and observation design in the repository | Preserve Source semantics; connector management needs its own supported surface |

Open proposals supply coordination constraints, not implemented protocols. Their final contracts take precedence over
illustrative names in this RFC. A personal preview may use existing single-deployment functionality; it cannot claim
resource-isolated team sharing or reliable delivery before those dependencies pass acceptance.

## 2. Component ownership and repository layout

```text
Trusted Web UI (shared presentation and page behavior)
    browser adapter ---------------------> public Python Server HTTP API
    desktop adapter -> narrow Rust bridge -> public Python Server HTTP API
                          |
                          +-> OS credential store, tray, notifications, file picker
                          +-> unified installer and existing service interface

OS service manager -> independent Python Server -> domain persistence and durable workers
Unified installer  -> verified runtime/integration artifacts and installation records
```

| Component | Owns | Must not own |
| --- | --- | --- |
| Shared Web UI | Navigation, localized presentation, forms, supported user actions | Authorization decisions, domain persistence, background ingestion |
| Rust desktop host | Restricted OS integration, protected credential access, authenticated transport, bounded local preferences | Memory/Handoff semantics, database access, another installer or service supervisor |
| Python Server | Public APIs, Runtime capabilities, domain validation, authorization, persistence, durable processing | Dependence on an open desktop window |
| Installer and distribution layers | Artifact identity, bootstrap, install plan, host configuration, ownership records, upgrade recovery | Desktop-specific business rules |
| Existing service layer and OS manager | Per-user registration, service identity, status, lifecycle | A second desktop-managed daemon competing for the same endpoint |

Add `desktop/` to the existing repository, containing the Tauri host under `desktop/src-tauri/`, desktop entry assets,
packaging configuration, and desktop acceptance harnesses. The initial implementation extracts reusable presentation
and transport boundaries from the existing Web UI. Server-served templates/static resources remain under
`src/powercontext/server/` and continue to be included in Python wheels.

Desktop packaging may introduce a frontend build for this independently built application. It must not require Node,
Rust, or desktop dependencies to install the Python package or run the existing Server UI. Do not make a React/Vue
migration a prerequisite: existing HTML, CSS, and JavaScript modules are sufficient until a concrete requirement
justifies replacing them. Any generated desktop entry markup has a single shared source and is built before release;
the installed desktop does not need to run Jinja or contact a Server to render setup and recovery screens.

Tauri capabilities, plugins, dependencies, and lockfiles are reviewed and pinned. Desktop-only CI paths are separated
from normal Python development while shared UI/API changes retain their existing test gates.

## 3. Public API reuse and compatibility

The desktop is a public API client. It must not import Python Runtime objects, open the domain database, scrape rendered
HTML, or depend on private `/dashboard/*` support endpoints. Shared pages use transport adapters: browser requests use
the Web deployment's authentication flow, and desktop requests use the native bridge. Page code does not implement
its own credential storage or route construction.

The existing API supports most initial management operations:

| Area | Existing public surface | Desktop implementation requirement |
| --- | --- | --- |
| Health and capabilities | `/health/live`, `/health/ready`, `/v1/capabilities` | Keep process liveness, runtime readiness, and feature availability distinct |
| Scopes | `/v1/scopes/*`, `/v1/scope-bindings/*`, artifact publication APIs | Reuse exact identities and supported selection/binding operations |
| Memory | `/v1/memory/*` | Respect size limits, citations, revision conflicts, and advertised search modes |
| Review | `/v1/artifact-candidates/*` | Pass expected Candidate versions; show conflicts instead of overwriting |
| Skills and Experience | `/v1/skill/*`, `/v1/experience/*` | Preserve managed lifecycle, exact package references, and Review requirements |
| Handoff and work | `/v1/handoff/*`, `/v1/work/*`, `/v1/handoff-reports/get` | Reuse exact continuation, acknowledgement, outcomes, and read-only reports |
| Sources | `/v1/sources/content`, Source definitions, observations, connector checkpoints | Use supported ingestion contracts; checkpoint APIs are not a connector control plane |
| Statistics | `/v1/stats` | Use Server-authorized projections, not client-side aggregation of unrestricted records |

Missing public projections must be added to `openapi/powercontext.yaml` before their desktop consumers ship, then
generated with `make api-generate` and checked with `make contract-test`. The same public routes and enforcement are
available to other clients. This RFC adds no implementation endpoints by itself.

This proposal introduces an additive, authenticated **`GET /v1/server-info`** handshake. Its initial contract should
contain `schema_version`, `product`, a persistent opaque `server_id`, `package_version`, `api_contract_version`, and
versioned `feature_contracts`. These fields describe deployment identity and protocol compatibility; runtime provider
availability continues to come from `/v1/capabilities`. The route must exclude filesystem paths, credentials, user
inventories, and unauthorized resource metadata. It follows the Server's authentication policy and exposes only the
minimal connection metadata needed by an authenticated client.

The exact OpenAPI schema and compatibility identifiers are a Server-owned prerequisite. Each desktop release declares
which contract versions and optional features it understands; package-version string comparisons alone do not decide
compatibility. Unknown optional features are ignored. An incompatible required contract blocks affected operations
with an upgrade explanation. A legacy Server without this handshake remains identifiable as legacy/compatibility
unknown and receives only explicitly tested support; it must not acquire features based on guessed versions.

`server_id` is a correlation identifier, not proof of ownership or authentication. Credentials, validated TLS, and
verified local installation/service records establish connection trust. An unexpected identity change invalidates
pending actions and cached selections and requires the user to reconnect deliberately. A handshake must not trigger
an automatic runtime upgrade, credential transfer, or migration of the remote deployment.

## 4. Connection profiles, transport, and the native bridge

Profiles persist a local opaque profile ID, display name, normalized endpoint including any supported base path,
connection mode, credential reference, TLS trust configuration, and observed compatibility metadata. They contain no
domain records. Remote profiles cannot select a local executable or service environment.

The first release has one active connection per window and one desktop instance per OS user and release channel.
Additional launches activate the existing instance using OS-user-restricted native IPC. The desktop does not expose
an extra HTTP management listener. Profile changes increment a connection generation, cancel outstanding reads, clear
private views, and discard late responses from the previous generation. A submitted write stays associated with its
original endpoint, Principal, Scope, and exact item; switching profiles cannot retarget it.

Transport must apply the same loopback policy as the existing client, including the shared cases in
`tests/fixtures/transport_loopback_vectors.json`:

- Non-loopback endpoints require HTTPS and normal hostname/certificate validation. Loopback HTTP is permitted under
  the existing client policy; loopback reachability alone does not authenticate a Server.
- Reject endpoint user information, query strings, and fragments. Credentials are never embedded in URLs. Preserve a
  supported API base path without allowing operation paths to escape it.
- Reject redirects for authenticated API requests in the first release. Do not forward credentials to a different
  host, scheme, or port. A custom CA, if supported, is explicitly configured for one profile; there is no persistent
  "disable certificate verification" setting.
- Apply bounded connect/read deadlines, body sizes, pagination, and cancellation. Errors identify transport failure,
  certificate failure, authentication failure, denial, conflict, incompatibility, and service unavailability separately.
- Remote profiles begin with a credential. A successful public health response is insufficient evidence of authenticated
  management access. Multi-user use additionally requires the resource-authorization contract in section 7.

Rust injects the selected credential into requests. The WebView receives data and safe errors, not a credential-read
API. Bridge commands represent allowlisted public operation IDs and typed parameters, profile selection, write-only
credential replacement, bounded file selection/import, diagnostics, and supported installer/service operations.

There is no arbitrary `fetch(url)`, shell execution, raw filesystem, process-kill, or database bridge. The renderer
cannot choose an executable, command line, release source, destination path, or credential header. Native validation
checks the selected profile, operation, parameters, limits, and current action context independently of UI controls.
File operations use native-selected handles or constrained destinations, not arbitrary renderer-provided paths.

Only packaged local UI documents receive Tauri capabilities. Remote Server responses are treated as untrusted data;
remote HTML must not be loaded into a privileged window. Use a restrictive CSP without remote scripts or unrestricted
inline execution. Render text and supported Markdown inertly; imported content cannot start commands, fetch remote
images, navigate the privileged window, or invoke IPC through embedded markup. Opening an external HTTP(S) link is an
explicit user action in the system browser. Other URL schemes require a separately reviewed, allowlisted integration.

## 5. Local service lifecycle and installation control

The local Server retains RFC 1299's per-user identity: systemd user service on Linux, LaunchAgent on macOS, and Task
Scheduler on Windows. Desktop setup does not request root, SYSTEM, or a second machine-wide service. Service settings
remain loopback-local and come from the validated local installation environment.

The current structured status fields are preserved as separate facts:

| Field | Meaning for the desktop |
| --- | --- |
| `support` | Whether this platform/environment supports native registration |
| `registration` | Whether a registration exists and is valid |
| `definition` | Whether executable and environment identity are current |
| `manager_ownership` | Whether the loaded manager entry belongs to PowerContext |
| `manager` | Active/inactive/failed/unknown manager state |
| `server_liveness` | Endpoint live/unreachable/unknown |
| `endpoint`, `log_location`, `recovery_action` | Local inspection and recovery information, shown with appropriate redaction |

`service status --json` can return a valid unhealthy result with a nonzero exit code. Parse the documented result
before deciding that command execution failed. A live endpoint with foreign or unknown ownership is not a healthy
managed installation. Do not kill an occupied port, delete another registration, or replace an unknown executable.

Reuse `service install` reconciliation and `service uninstall` semantics through the service owner. If the product
needs explicit start, stop, or restart, those operations and their machine-readable results must first be added to
that owner; current CLI commands do not provide them. Until available, hide unsupported controls and offer supported
recovery. Never implement "Stop" by uninstalling the service.

The desktop consumes the installation plan, verified component results, and recovery semantics owned by #1406 and its
installation RFC. A versioned, non-interactive machine interface is a prerequisite for desktop-managed installation.
The desktop must not implement another installer engine or infer success from process exit alone. In particular,
#1408's proposed phases and structured output do not yet define public `plan/apply/status` commands or JSON schemas.

That interface needs to expose a reviewable plan, immutable component identities, affected locations, ownership and
compatibility checks, observable progress, cancellation boundaries, durable operation identity, component outcomes,
and recovery after an interrupted client. Resolve/preflight remain non-mutating. Revalidate a stale plan before
application. The installer owns concurrent-operation locking and its durable journal; multiple entrypoints must not
race the same installation.

Runtime and host components can succeed independently. An `uncertain` result requires verification before retry;
`installed` is not proof that a host loaded successfully. The desktop displays the producer's `unsupported`, `skipped`,
`installed`, `current`, `stale`, `failed`, or `uncertain` states without inventing a global atomic rollback across hosts.

Window closure can leave an installation in the background only if its owner supports durable execution and recovery.
Otherwise the application keeps the operation visible and offers cancellation only at safe boundaries. A forced exit
must be recoverable from the installer's records. Do not promise that an ordinary Tauri-spawned child survives Quit.
The steady-state Python Server is always managed independently by the existing OS service registration.

## 6. Credentials and local configuration

Store client credentials in an explicit native credential-store adapter: Windows Credential Manager for the initial
Windows target, macOS Keychain and Linux Secret Service when those platforms qualify. Desktop preferences store only
opaque references. An unavailable or locked backend requires unlock, session-only use, or a separately supported
encrypted-vault flow; there is no silent plaintext fallback. Tauri Stronghold can implement a vault, but it is not
itself the OS credential store and is not required for the first target.

A credential typed or pasted into a trusted setup form may exist transiently in its input and write-only IPC payload.
Clear it after submission, do not expose a read-back operation, and do not store it in WebView local/session storage,
URLs, command arguments, logs, diagnostics, crash reports, or notifications. Native transport redacts authorization
headers and sensitive request/response fields before producing observable errors. Tokens copied by users may also
exist in the OS clipboard; the application does not claim to protect against arbitrary software running as that user.

Server authentication/provider secrets and desktop client credentials have different lifecycles. The independent
Server must obtain its own configuration without requiring a running desktop or an unlocked desktop vault. Local
setup delegates validated configuration generation, restrictive file permissions, and environment identity handling
to the installer/service configuration owner. Never put credentials in a service command line. Configuration changes
that affect a registered environment require the service owner's reconcile procedure.

Discovery reads only known installation/service records and explicitly selected configuration files. Do not scan
unrelated home directories, import all ambient environment variables, or copy Server/provider credentials into UI
preferences. Sensitive configuration changes show their scope and required restart/reconcile action before application.
Removing a desktop profile removes its credential reference and offers deletion of that credential; it does not delete
credentials or environment files used by the independent Server or Agent hosts.

## 7. Authentication and resource authorization

The existing static Bearer middleware authenticates a deployment-wide trust boundary. It does not establish team roles
or resource-level sharing. A personal preview may connect to such a deployment with an explicit shared-trust mode.
Normal desktop-managed local installation should enable Server authentication, while attaching to an existing
unauthenticated loopback deployment presents its actual access policy without silently changing it.

Team-connected use requires the Server enforcement described in RFC 1396 and its implementation work. The Server
resolves the trusted Principal; renderer input, an Agent name, `receiver`, or a receiver's self-reported authorization
check cannot establish identity or grant access. A desktop cannot compensate for missing backend authorization by
hiding buttons or filtering a fully retrieved dataset.

Use the Server's current-Principal discovery and supported access checks to explain available actions. They are
advisory UI prechecks: every body read, exact continuation, acknowledgement, Review action, and mutation still passes
the Server's current authorization enforcement. In particular:

- Scope organization does not imply access inheritance or Context sharing.
- Candidate reads and Review mutations follow their distinct read/review permissions.
- A grant to one committed Handoff revision does not grant the latest Handoff, adjacent revisions, an entire Scope,
  a report, or unrestricted Memory search. Evidence follows the exact citation manifest and its authorization rules.
- Skill publication preserves both resource and publication permissions. A target identifier is an operation
  parameter, not a new authorization resource or proof of ownership.
- Collections, totals, and search results are authorized before repository query/pagination. An unavailable safe
  filtering path must fail explicitly; the desktop must not fall back to an unrestricted list and local filtering.

Cache entries, opaque list cursors, selections, and notification metadata are isolated by profile endpoint, current
Principal or credential generation, and query. Switching identity clears prior private state. Authorization-check
results are not durable permission grants. An expired credential stops protected requests and prompts reauthentication;
a denied action retains its distinct explanation. Neither condition triggers automatic credential reuse on another
profile or unbounded background retries.

## 8. Scope, asset, and Review behavior

Project and workstream views use existing opaque Scope IDs and organization. A repository path, branch, session ID,
Agent name, or display label is not a Scope identity. Parent organization does not create transitive Context references,
transfer ownership, or publish Artifacts. Cross-scope visibility and publication use their explicit existing APIs.

Observation selection (`all`, `subtree`, `exact`) is separate from the exact Scope used for a write or integration
binding. Forms display the destination Scope; actions capture it when submitted. Changing the global selector while
a request is in flight cannot redirect the mutation. Binding edits show the affected integration and its supported
selection semantics rather than assuming all hosts implement the same behavior.

Memory search uses supported Server search modes and limits. A missing embedding/generation capability disables only
the affected operation. The UI preserves Memory citations and exact Artifact references and labels pending Sources,
Candidates, committed Artifacts, and retired entries distinctly. It does not present accepted Source input as already
extracted Memory or a pending Candidate as a published Skill.

Review reuses Candidate expected-version checks. On a conflict the application reloads the authoritative Candidate
and explains the intervening change; it does not silently approve a newer version. Managed Skill lifecycle changes
preserve their generation checks, and package publication consumes reviewed exact packages. Downloading, inspecting,
or publishing a package does not authorize the desktop to execute its scripts.

## 9. Handoff discovery, delivery, and actions

Three views have different purposes:

| View | Authority | Meaning |
| --- | --- | --- |
| Handoff Report | Existing report API | Read-only projection of selected Scopes and their latest exact Handoff |
| Shared with me | RFC 1396 authorized resource discovery | Exact resource identities the current Principal may access |
| Handoff inbox | #1419 delivery contract | Durable delivery records for the receiver, including their supported state and recovery |

An access-list page cursor is not an incremental notification cursor. Granting access does not deliver a Handoff or
mark it unread. Candidate Review and remote Skill receiver/reconciliation APIs also cannot stand in for Handoff
delivery. The desktop does not define a second envelope, receiver registry, receipt protocol, or retry scheduler.

The #1419 owner must supply the delivery contract needed by all consumers: versioned envelopes and exact references,
trusted receiver association, durable listing and recovery, deduplication identity, supported pagination/event
cursor semantics, expiry, cancellation, and terminal/retryable states. The desktop consumes these as opaque identities
and supported operations, scoped to the current endpoint and Principal. Until implemented, the UI may provide reports
and authorized discovery, but must label the durable delivery inbox unavailable.

Opening an item resolves its original exact `ArtifactReference`, rechecks current authorization, and fetches current
delivery state. It does not substitute `latest`. A missing, expired, canceled, or revoked item explains that outcome
without exposing cached body content. Access to one exact revision must not open a broader Scope report as a fallback.

Existing exact Continue and Acknowledge operations remain authoritative. Receipt values such as `accepted`,
`needs_clarification`, and `declined` keep their current meanings. An accepted receipt requires the receiver's actual
live-state, capability, and authorization observations; simply viewing a desktop screen cannot attest to another
Agent's environment. The desktop offers acknowledgement only when a supported flow supplies the required checks.
Otherwise it routes the user to the integration that can perform them.

If a maintained Agent host supports exact-item launching, use its declared integration mechanism and pass only the
bounded exact selection it accepts. Otherwise provide a supported copy/open workflow without credentials or domain
bodies in a URL. The desktop does not invent a host deep link or run an Agent task itself. Local links and notification
activations are navigation requests only: validate their profile/item association and perform no automatic mutation.

Viewing, marking read where supported, successful delivery, granting access, a receiver's accepted receipt, and a
recorded Task Outcome are different actions. The UI names them separately and never advances one as a side effect of
another unless that transition is explicitly defined by the owning Server contract.

## 10. Notifications and background behavior

The Server inbox and Candidate state are authoritative. OS notifications are best-effort hints, not a durable queue
or an exactly-once delivery guarantee. Initially subscribe or poll only the active connection. Use an existing
supported incremental contract when available; otherwise bounded, backed-off polling is acceptable for Review state.
Polling a Candidate list provides current pending work, not a complete history of every intermediate transition.

Requirements for notification consumption are:

- Derive notification identity from the producer's stable item/event identity and exact revision where applicable.
  Persist only bounded deduplication metadata and opaque cursors; never persist Memory, Source, Handoff, Prompt, or
  Prepared Context bodies in the desktop notification store.
- Apply producer-defined resume, cursor-expiry, and gap-recovery semantics. If only current-state listing is available,
  refresh that state and present a summary; do not invent missed delivery events or reinterpret a pagination cursor.
- Isolate metadata by endpoint and Principal. Clear it on credential/identity changes; bound its retention and size.
  Treat a locally displayed notification as separate from a Server-side read or acknowledgement operation.
- Poll with jitter, backoff, request limits, and cancellation. Coalesce bursts and suppress repeated offline/auth errors.
  Stop protected background requests on credential expiry; offer one useful recovery indication.
- Use a generic message such as "PowerContext has items needing attention" by default. Notifications carry only
  approved bounded metadata and a local opaque navigation handle. Do not include content, credentials, private paths,
  sensitive titles, or unreviewed Server error text, including on the lock screen.
- On click, activate the application, restore the appropriate profile deliberately, and reauthorize the exact item.
  Stale or spoofed activation handles do not switch credentials silently or execute actions.

Request OS notification permission with an explanation at first use. Permission denial leaves in-app counts and
inbox access functional. A fully exited desktop receives no notifications in this release; the next start restores
current Server state. If tray support is unavailable, keep ordinary window navigation and Quit usable. Test real
installed notifications and cold activation, not just an in-process mock.

## 11. Sources, connectors, and integrations

First support explicit text entry and bounded UTF-8 text-file import through the native file picker. Show destination
connection, Scope, intended Source identity, and size before transmission. A selected file is read through a bounded
native handle; prevent path substitution, directory traversal, and following a changed link into a different file.
The Server's content limits and validation still apply. Do not silently scan a directory or the user's home.

For a remote connection, transfer approved bytes using the public content-ingestion contract. A local path is not
something a remote Server can open. Preserve Source identity, content digest, and provenance without unnecessarily
disclosing the full local path. Repeated imports obey the Source contract's identity/conflict rules; changing content
under an immutable identity is not treated as a successful duplicate.

RFC 1400's Source definitions, observations, and checkpoints do not define connector discovery, scheduling, provider
credentials, or plugin execution. First-release connector views are limited to supported Server metadata and actions.
Additional management APIs belong to the connector/Server owner and require public contracts before those controls
ship. Desktop closure cannot stop an accepted connector job; worker credentials and checkpoints cannot live only in
the desktop. A partial crawl must not be interpreted as deletion of unseen content.

For Agents, consume the maintained distribution model in #1405/#1410 and release-specific capability declarations.
The existing `integrations/capabilities.toml` is a repository version contract, not a live public HTTP capability API
or receiver directory. The UI separately displays:

1. What the selected distribution declares it supports on this host/platform/version.
2. What the installer records as installed and who owns it.
3. What structured diagnostics verify about loading, connectivity, Scope selection, capture, and recall.
4. Runtime or receiver enrollment state, only when its owner exposes that fact.

Use actual structured diagnostic interfaces, including `doctor integrations --json`, rather than parsing human text or
counting installed files/tools. Unsupported or unobserved checks remain explicit. Installation is opt-in per selected
host. Configuration merging, canonical package identity, hook behavior, and distribution repair belong to their
existing owners; the Rust host must not copy these rules or automatically rewrite every detected Agent configuration.

## 12. Offline operation, retry, and concurrent changes

The first release maintains no persistent local domain cache or offline write queue. An offline remote view hides
private content and displays connection state; optional unsent form input remains volatile and visibly unsaved.
Reconnection refreshes compatibility, identity, authorization, and selected resource state before enabling mutations.
An available local Server continues to support its own offline capabilities; the desktop does not promise offline
generation when its configured model requires the network.

Read retries are bounded and cancellable. Mutation retry follows the operation's public contract. If the Server
supports an idempotency key, reuse the same key for the same logical operation. A timeout after submission is an
unknown outcome, not proof of failure: verify authoritative state or offer inspection before retry. Do not replay a
Review approval, Handoff receipt, import, publication, or installation blindly. Actions with no safe verification or
idempotent retry path show that uncertainty and require a fresh, explicit decision.

Concurrent CLI, Agent, Web, or desktop mutations remain valid. Respect existing revision/version checks, show the
refreshed item on conflicts, and preserve user intent without silently applying it to a new revision. Pending UI
actions carry their original connection/identity generation and exact destination. Late results never populate a
different profile's screen.

## 13. Distribution, updates, and recovery

The initial Windows distribution uses a signed per-user installer. A local bootstrap must work without preinstalled
Python, Rust, Node, Git, or a compiler. The installer owner supplies a verified interpreter/runtime environment and
maintained integration artifacts for the selected OS and architecture. A remote-only installation omits that runtime.
Online and any offered offline packages declare their included components and remaining network requirements.

The current service implementation resolves a Python executable and requires an adjacent `pythonw.exe` on Windows.
Therefore a frozen Python executable is not a drop-in runtime replacement. Prefer the installer-owned versioned Python
environment. Any future frozen-runtime design needs explicit service compatibility work and platform qualification.
Tauri sidecar packaging may distribute a helper, but it does not transfer Server lifetime to Tauri's child processes.

The release plan distinguishes desktop UI/host version, Python runtime version, API contract version, integration
distribution versions, and persistent data compatibility. Resolve a human-friendly channel to an immutable manifest;
record exact artifact locators, digests, OS/architecture, and compatibility. Release trust requires a signed artifact
or manifest bound to a trusted publisher; a checksum downloaded from the same untrusted location alone is insufficient.
Keys and permitted update sources are pinned outside an arbitrary renderer or remote Server response.

Tauri's signed updater can update the desktop component. It does not coordinate Python environments, Agent
configurations, service registration, or database migrations. The unified installer owns that multi-component plan.
The desktop must never silently upgrade a remote Server or independently overwrite an installation shared with Agents.

An update follows these rules:

1. Resolve and display compatible immutable versions, affected components, downtime, data compatibility, and recovery.
   Check free space, ownership, credentials needed for verification, and concurrent installer activity.
2. Verify artifacts before staging them beside the existing version. Retain the last verified installation record.
   A download or signature failure leaves the running installation usable.
3. For a runtime switch, use the service owner's supported quiesce/switch/reconcile path. Its contract must specify
   handling of in-flight and durable work; the desktop does not kill a process after a guessed timeout.
4. Run readiness, compatibility, and selected integration verification before recording the new installation as
   healthy. Report component results individually when only part of the plan succeeds.
5. Roll back executable/configuration changes only where the installer declares rollback safe. Data migrations belong
   to the Server/runtime owner. An older runtime must not reopen an incompatible upgraded store. Before an irreversible
   migration, the plan needs a supported backup/restore or explicit forward-recovery path and user confirmation.
6. On interruption, reopen the durable operation record, verify uncertain components, and resume or repair through the
   owner. Never infer that an interrupted operation rolled back successfully.

Do not copy live database files as an improvised backup. Backup, quiescence, and restore must be consistent with the
actual persistence backend. Schema migrations and backup APIs absent from the owner block the corresponding automatic
upgrade path; they are not implemented inside Rust.

Stable is the default channel; prereleases require opt-in and clear labels. Channel changes do not bypass data or API
compatibility checks. The initial release offers update notification and explicit application, without unattended
runtime upgrades during active work.

## 14. Data locations and uninstall

| Data or artifact | Owner | Default removal behavior |
| --- | --- | --- |
| Desktop executable and packaged UI | Desktop package manager/updater | Removed with the application |
| Connection profiles, UI preferences, bounded notification metadata | Desktop, in a separate per-user application directory | May be removed with an explicit reset choice |
| Desktop credential entries | OS credential store | Remove only entries owned by the selected profile/application |
| Python environments, integration artifacts, installation records | Unified installer | Preserve while still referenced; remove through its ownership-aware plan |
| Service registration and protected Server environment | Existing service/configuration owner | Preserve unless service removal is explicitly requested |
| Memory, Sources, Artifacts, scheduler state, backend data | Server persistence owner | Preserve on application or service uninstall by default |
| Agent host configuration | Distribution/installer owner and the user | Revert only the owned, recorded changes; preserve unrelated edits |

The Server's `POWERCONTEXT_HOME` or existing platform data-directory rules remain authoritative. Versioned application
directories must not become domain data directories. A local diagnostics page may show resolved data/log locations;
remote connections cannot browse the Server's filesystem. Rust may open a known local location for the user but must
not read or modify domain database contents.

Offer separate, clearly named actions for removing the desktop, removing the local service, and deleting PowerContext
data. Data deletion is excluded from the first release's automatic uninstaller; a future UI for it requires an explicit
owner-supported workflow and confirmation of the exact local installation and data path. Uninstalling the desktop
must leave an independently installed Server and its Agents usable unless the user separately requests their removal.
Unknown ownership or user-modified files result in preservation and an explanation, not recursive deletion.

## 15. Diagnostics, privacy, and security scope

Diagnostics combine desktop version/platform, verified installation/component states, service facts, connection
failure category, supported contract versions, safe request IDs, and bounded timing/error codes. The default export
contains no Memory, Source, Handoff, Prompt, Prepared Context, model output, credential, authorization header, raw
environment dump, connection query, or private absolute path. Do not blindly include arbitrary Server error bodies
or full CLI stdout/stderr; normalize them through a redacted diagnostic model.

Export is local, explicit, and previewable. Users choose where to save the sanitized file. No automatic upload or
product telemetry is required. Crash reporting is disabled by default; an eventual opt-in mechanism must not transmit
memory dumps or raw request bodies under a claim of content-free diagnostics. Logs have bounded retention and size.

The threat model includes malicious Server content, imported files, forged notification/deep-link activation, a local
website attempting bridge access, wrong-endpoint credentials, and tampered release artifacts. Its defenses are
trusted local UI, limited IPC, per-profile transport, Server-side authorization, native secret storage, safe rendering,
and authenticated distribution. It does not promise protection from a compromised OS, arbitrary same-user malware,
or an administrator with access to the user's processes and data.

Security qualification must inspect actual packaged capabilities/CSP, dependency permissions, credential fallback
behavior, and artifact/update verification. Selecting Rust alone is not evidence that those boundaries are correct.

## 16. Platform, accessibility, and localization

| Platform | Proposed delivery status | Qualification concerns |
| --- | --- | --- |
| Windows 11 x64 | First qualified target, SQLite local runtime | Per-user signed installation, WebView2 availability/bootstrap, Credential Manager, Task Scheduler ownership, installed notifications, non-ASCII paths |
| macOS | Follow-up qualification | Architecture-specific runtime, Keychain, LaunchAgent, signing/notarization, WKWebView behavior, notification permission |
| Linux | Follow-up qualification with an explicit distro/desktop matrix | WebKitGTK/system libraries, Secret Service availability, systemd user session, tray differences, packaging and notification activation |

Windows support does not imply Windows ARM support, seekdb availability on Windows, or identical behavior across
all OS versions. A Tauri build succeeding on three targets is insufficient evidence of product support. Each advertised
OS/architecture must pass installed-package acceptance on its declared supported environment.

Maintain English and Chinese UI/documentation together. Provide keyboard navigation, visible focus, accessible names,
screen-reader semantics, IME-safe forms, high-contrast support, and readable status independent of color. Management
flows must remain usable at an 800 × 600 window and 200% zoom through scrolling or responsive layout, without hiding
confirmation or recovery actions. Preserve user's selected locale and OS theme preferences.

Before declaring the first target supported, measure cold launch, idle CPU/wakeups, total desktop-plus-runtime memory,
installer/download size, and list/search responsiveness on a documented reference machine. Freeze release budgets
after the architecture spike and before feature expansion. Include WebView2/runtime dependencies and the Python Server
in comparisons; do not advertise a small Rust executable as the total product footprint.

## 17. Delivery sequence and ownership gates

Use focused implementation PRs associated with this RFC and #1428. Reuse existing dependency tracking issues; do not
create a duplicate desktop tracking issue or merge the entire product as one change.

| Phase | Concrete deliverable | Exit gate |
| --- | --- | --- |
| P0: architecture spike and contracts | Trusted bundled shared page in Tauri; public API transport; credential adapter; installed Windows notification; connection/compatibility design; measured prototype | Confirm Windows feasibility and budgets, producer-owned installer/service interfaces, public API gaps, security boundary, and release owner |
| P1: personal preview | New and existing local setup, remote shared-trust connection, service status/recovery, model-optional first Memory flow, selected Agent diagnostics, explicit uninstall behavior | Real pinned artifacts and current supported service contracts; no claim of multi-user resource sharing or reliable Handoff delivery |
| P2: management parity | Scope/binding views, Memory/assets, Review, read-only reports, explicit Source import, supported connector state, bilingual accessibility, protected diagnostics | Public API authorization behavior and revision/conflict semantics preserved; dependency-limited controls accurately represented |
| P3: authorized collaboration | Current-Principal resource discovery, exact-item permissions, #1419 durable inbox/recovery, supported Handoff actions, bounded notifications | RFC 1396 implementation and #1419 contracts pass Server and desktop acceptance |
| P4: qualified first release | Signed installation/update artifacts, recovery, complete first-use journey, independent service lifetime, data-preserving uninstall | All applicable acceptance criteria below pass on Windows 11 x64; published compatibility/support matrix and operational ownership |
| P5: additional platforms | macOS and Linux packages using the same boundaries | Repeat installed-package acceptance for each advertised OS/architecture |

P0/P1 can proceed without waiting for every collaboration feature, but desktop-managed installation cannot be faked
with a bespoke bootstrap. If #1406's machine interface or Windows bootstrap is unavailable, the preview must be
explicitly connect-only and cannot claim the install acceptance criteria. A preview alone does not complete #1428.

For #1428 completion, the maintainers should require at least one qualified OS, the issue's complete local setup and
management journey, authorized remote access, reliable Server-backed Handoff inbox consumption, Review/Handoff
notifications, and the documented recovery and uninstall guarantees. Authorization and delivery producers retain their
own tests and release ownership; desktop acceptance validates the end-to-end composition.

## 18. Acceptance and validation

These are observable acceptance requirements, not demands to freeze internal function calls, module layouts, or UI
element IDs. Use public API contract tests and installed desktop workflows. Platform-specific tests are required only
for platforms claimed as supported; Server behavior should reuse existing tests where they already protect the contract.

| ID | Scenario | Required observable result |
| --- | --- | --- |
| AC-01 | Clean first install without Python/Node/Rust/Git; minimal configuration without a model | Verified local runtime/service and selected maintained integration install; explicit Memory store and full-text recall succeed; unsupported model actions explain requirements |
| AC-02 | Existing manual service, stale managed definition, occupied port, or foreign registration | Correctly distinguish states, preserve unknown ownership/data, and offer only supported repair |
| AC-03 | Close window, Quit, restart desktop, and restart the OS session | Independent service and accepted durable work survive desktop exit; login behavior matches service/desktop settings |
| AC-04 | Interrupt download/install/update or fail a signature/readiness check | Old usable state is preserved where possible; component results and uncertainty are explicit; supported resume/rollback respects data compatibility |
| AC-05 | Legacy runtime, incompatible API, mixed integration versions, or changed Server identity | Capability/compatibility limitation is explained; no guessed support, silent retargeting, or automatic remote upgrade |
| AC-06 | Loopback, non-loopback HTTP, invalid TLS, redirect, credential expiry, and authorization denial | Existing transport policy is preserved; no cross-endpoint credential forwarding; failure categories remain distinct |
| AC-07 | Switch profile/Principal while requests or notifications are pending | No previous identity's data, cursor, response, credential, or mutation target appears in the new connection |
| AC-08 | Principal can read only one exact Handoff revision | No latest/adjacent revision, unauthorized evidence, broader report, or Scope content leaks; action requests are reauthorized |
| AC-09 | Restricted lists and Review/publication permissions | Server filters before pagination/totals; unsafe filtering fails; hidden buttons cannot bypass enforcement; permission-specific actions behave correctly |
| AC-10 | Concurrent Candidate/asset update or ambiguous mutation timeout | Version conflict or unknown outcome is shown; no silent approval of a new revision and no blind duplicate mutation |
| AC-11 | Handoff arrives during disconnect, cursor expires, permission is revoked, or delivery is canceled | Producer-defined recovery restores authorized inbox state; original exact reference is retained; navigation cannot acknowledge automatically |
| AC-12 | Installed notification, denied permission, burst of items, full desktop exit, or stale activation handle | Bounded content-free hints, deduplication/coalescing, safe exact navigation, functional in-app fallback, and accurate background limitations |
| AC-13 | File import, repeated Source identity, changed bytes, partial connector crawl, or close during ingestion | Approved content reaches the selected Scope through public APIs; identity/conflicts are preserved; no silent broad import, deletion, or desktop-owned worker |
| AC-14 | Malicious HTML/Markdown, arbitrary IPC parameters, forged link, or wrong profile endpoint | No arbitrary execution/filesystem access, secret read-back, privileged remote navigation, or unintended mutation |
| AC-15 | Secret canaries in tokens, paths, provider configuration, errors, and domain bodies | No canaries in notifications, normal logs, diagnostic exports, URLs, persistent renderer storage, or release telemetry |
| AC-16 | Remove desktop, remove service, or encounter user-modified integration files | Data retained by default; independent components remain usable unless separately removed; unknown/unowned files preserved |
| AC-17 | English/Chinese, keyboard-only operation, IME, screen reader, high contrast, small window, 200% zoom | Setup, connection, review, notification navigation, recovery, and uninstall choices remain understandable and operable |
| AC-18 | Installed release on the reference machine and advertised OS/architecture | Signed artifacts and update path work; measured complete footprint and responsiveness satisfy agreed release budgets |

Before implementation PRs that change public contracts, run `make api-generate` and `make contract-test`; preserve
normal `make check`, relevant behavior tests, and strict documentation checks. Shared UI changes require Web and
desktop behavior coverage. Desktop packaging changes require an installed-package smoke test; service changes reuse
the service layer's native platform tests. Mocked transport tests alone cannot qualify installation or notifications.

# Drawbacks

This adds a maintained native application, a Rust toolchain, desktop JavaScript packaging, signed release operations,
and OS-specific testing to a Python project. Sharing UI code reduces duplicated domain presentation, but extracting
transport and build boundaries still costs work and can affect the existing Web UI.

Tauri uses different system WebViews across platforms. Rendering, accessibility, authentication integration, and native
notifications need platform validation. Python and optional storage/model dependencies can dominate package size and
resource use, reducing the practical footprint advantage of a smaller desktop host.

Independent runtime installation is operationally more complex than one executable. It is justified by the Server's
existing role serving Agents while the desktop is closed, but requires coordinated compatibility and recovery. The
complete collaboration product also depends on authorization and delivery work outside this RFC's implementation.

# Rationale and alternatives

## Tauri 2 versus Electron

| Consideration | Tauri 2 | Electron | Decision for PowerContext |
| --- | --- | --- | --- |
| Web UI reuse | HTML/CSS/JavaScript in the OS WebView | HTML/CSS/JavaScript in bundled Chromium | Both can reuse management UI; neither requires rewriting domain code |
| Native host | Rust host with explicitly granted capabilities/plugins | Node.js main process with restricted preload/IPC | A small Rust host fits the limited native duties and contributor preference |
| Distribution footprint | Reuses system WebView, with platform bootstrap dependencies | Ships Chromium and Node.js | Prefer Tauri, but measure the full Python/WebView/runtime distribution |
| Cross-platform rendering | WebView2, WKWebView, and WebKitGTK differences | More consistent bundled Chromium | Electron is advantageous if WebView differences defeat required accessibility or UI behavior |
| Python integration | External runtime or helper | External runtime or helper | Neither solves Python installation, service ownership, or domain schema migration |
| Secrets and updates | Requires explicit credential-store integration and component update design | Native encryption/update facilities still require policy and integration | Neither replaces OS-store qualification, authorization, or installer contracts |
| Team cost | Rust and native plugin expertise; platform qualification | JavaScript/TypeScript ecosystem and Electron expertise | Validate Rust maintenance and release ownership in P0 |

Choose **Tauri 2** because the product is a native control center around an existing Python Server and a relatively small
Web management surface. Its native host can stay narrow, and there is no requirement for Node.js plugins, a bundled
browser engine, or desktop-side AI execution. Rust is used for OS integration and constrained transport, not as a
performance justification for rewriting Python business logic.

Electron is the fallback if P0 finds a concrete blocker in the supported WebView's accessibility/rendering, required
native integrations, or sustainable Rust/platform maintenance. Such a switch should retain the same public API,
installer, service, and authorization boundaries. Do not run two production shells in parallel or assert a performance
winner without comparing installed end-to-end prototypes.

## Other alternatives

- **Web UI only:** remains supported and is the least expensive choice for remote management. It does not complete
  native installation/service diagnostics, OS credential handling, file integration, and installed notification flows.
- **Load a Server page directly in a privileged shell:** reduces initial UI extraction but couples setup to Server
  availability and places remote markup beside native privileges. Use packaged local UI instead.
- **Bundle Python as an application child process:** can help a prototype, but closing/updating the desktop must not
  interrupt Agents or durable work. Keep the existing independent service owner.
- **Rewrite Runtime/storage in Rust:** duplicates mature domain contracts and migration responsibilities without a
  desktop requirement that needs it. It is outside this proposal.
- **A fully native Rust widget UI:** loses existing Web presentation reuse and creates a second management interface.
  Reconsider only if a demonstrated requirement cannot be met by the shared Web interface.

# Prior art

The project-specific foundations are RFCs
[1299](1299_local_server_availability_and_service_installation.md),
[1345](1345_scope_organization_and_agent_integration.md),
[1396](1396_handoff_access_control.md),
[1400](1400_source_definition_and_observation_model.md),
[1351](1351_standard_skill_package_lifecycle.md), and the
[Server Web UI development guide](../development/server-web-ui.md). The dependency table above distinguishes
implemented surfaces from open installation, distribution, authorization, and delivery work.

The following official references inform framework and packaging choices; their facilities are not substitutes for
PowerContext's component contracts:

- [Tauri architecture](https://v2.tauri.app/concept/architecture/) and
  [WebView versions](https://v2.tauri.app/reference/webview-versions/) describe the host/UI model and platform engines.
- [Tauri capabilities](https://v2.tauri.app/security/capabilities/) and
  [CSP](https://v2.tauri.app/security/csp/) inform the restricted native bridge and trusted packaged UI.
- [Tauri sidecars](https://v2.tauri.app/develop/sidecar/),
  [updater](https://v2.tauri.app/plugin/updater/), and
  [notifications](https://v2.tauri.app/plugin/notification/) provide component mechanisms that need lifecycle and
  installed-package validation.
- [Tauri Windows distribution](https://v2.tauri.app/distribute/windows-installer/),
  [macOS signing](https://v2.tauri.app/distribute/sign/macos/), and
  [AppImage distribution](https://v2.tauri.app/distribute/appimage/) identify distinct OS delivery requirements.
- [Tauri Stronghold](https://v2.tauri.app/plugin/stronghold/) documents a vault facility; an OS credential-store adapter
  is an explicit separate choice in this proposal.
- [Electron documentation](https://www.electronjs.org/docs/latest/),
  [security guidance](https://www.electronjs.org/docs/latest/tutorial/security),
  [safeStorage](https://www.electronjs.org/docs/latest/api/safe-storage), and
  [autoUpdater](https://www.electronjs.org/docs/latest/api/auto-updater) support the alternative assessment.

# Unresolved questions

Resolve these cross-owner decisions during RFC review or the named gate, without delegating core business semantics
to the desktop:

1. **Before RFC acceptance:** confirm the first Windows 11 x64 target, Tauri maintenance/release owner, and the phased
   distinction between a personal preview and completion of #1428.
2. **Before desktop-managed installation:** agree the installer/service machine interfaces and Windows bootstrap
   schedule. #1406 asks for first-class shell and PowerShell bootstrap; the open #1408 proposal still leaves its engine
   and PowerShell timing unresolved. This RFC does not choose the engine's language or invent CLI flags for it.
3. **Before compatibility-dependent management:** settle the proposed `server-info` schema, contract-version policy,
   stable Server identity lifecycle, and support window with the Server owner.
4. **Before collaboration release:** agree the delivery/inbox contract and integration launch/receiver checks with
   #1419, and qualify authorization with RFC 1396's implementation. Do not treat access-list pagination as event replay.
5. **At P0 exit:** publish measured performance budgets, code-signing/update-key ownership, release CI environments,
   and the dependency/security maintenance policy. These are release prerequisites, not claims of present coverage.

A connector management control plane, additional authentication methods, advanced offline synchronization, and broad
Agent execution are separate designs. Their absence must not be hidden by implementing private desktop protocols.

# Future possibilities

After the first complete platform qualifies, add macOS/Linux packages, additional architectures, and background
notifications for explicitly enabled multiple profiles. Further work may add more Source import formats, supported
connector configuration, or system-browser authentication once their Server contracts exist.

An offline write queue, local domain cache, broader Agent actions, or cloud synchronization would introduce new
consistency and security obligations and require a separate proposal. None is necessary to adopt this RFC.
