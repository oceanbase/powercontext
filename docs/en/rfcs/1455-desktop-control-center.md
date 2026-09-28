---
title: "RFC 1455: Desktop Control Center"
description: "A Tauri desktop client with explicit API, identity, installation, and delivery boundaries."
---

- Proposal Name: `desktop_control_center`
- Start Date: 2026-09-04
- RFC PR: [oceanbase/powercontext#1455](https://github.com/oceanbase/powercontext/pull/1455)
- Tracking Issue: [oceanbase/powercontext#1428](https://github.com/oceanbase/powercontext/issues/1428)
- Status: Proposed

# Summary

Build **PowerContext Desktop** as a Tauri 2 application with a bundled React + TypeScript UI built by Vite.
It manages connections, Scopes, context assets, Review and supported local setup/Handoff flows through public
contracts. Rust owns constrained native capabilities and credential-bearing transport; the independent Python
Server owns domain behavior, authorization and persistence. Share suitable presentation resources with the personal
Dashboard while keeping a separate client management entry. Propose Windows 11 x64 with SQLite as the first target.

# Motivation

A user should be able to determine whether PowerContext is installed, whether an Agent uses the intended Scope, what
needs review, where a Handoff went, and how to recover from an update in one application. Native credentials, file
selection, service inspection, tray status and notifications justify the desktop. An independent Server must keep
serving Agents while the desktop is closed.

The product serves new personal users, users attaching to existing installations, and users of authenticated team
Servers. It is not a chat client, IDE, Agent runtime, orchestrator, database replica, or prerequisite for CLI/SDK/MCP use.
It does not execute downloaded Skills or automatically run Agent tasks.

The intended outcome is a complete installed-product journey on one qualified platform. A connect-only preview can
arrive before managed installation and delivery, with its limits visible. Accepting this design does not complete
[#1428](https://github.com/oceanbase/powercontext/issues/1428). Implementation phases, task owners, acceptance evidence
and tuning work are tracked in the [delivery plan](../development/desktop-delivery-plan.md).

# Guide-level explanation

The desktop is a control center for a Server. A **connection** selects a Server and credentials; the Server resolves
the **Principal** whose permissions apply. A **Scope** identifies the context being observed or changed. Selecting a
Scope for browsing does not change an Agent's binding. A **Candidate** is a proposal awaiting review, while a Handoff
**delivery** is a durable record for an authorized receiver. Opening either item does not approve or accept it.

## Connect to a Server

On a machine without PowerContext, choose **This computer**. When managed installation is supported, inspect and
approve the installer's plan: releases, components, selected Agent hosts, locations and recovery. Setup works before
Python is installed. A connect-only preview explains its limit and links to supported installation instructions.

If the Server already exists, save its endpoint and credentials as a connection. For example, keep separate
“Personal” and “Team” connections, with Team using HTTPS. Remote-only use needs no local Python. The overview shows
the resolved identity, authorization mode, service readiness and available features separately. A healthy Server
does not mean every feature is configured or that the current user may use it. An unsupported version or login
method produces an actionable explanation; the desktop does not guess compatibility or downgrade authentication.

## Choose a Scope and manage context

In Personal, select the exact Scope displayed as “Documentation” and explicitly save “Use British spelling in the
guide.” Recall it through full-text search in that same Scope; this path needs no model configuration. The result
opens its exact citation. A broader browsing selection does not silently become the destination of a write.

Importing a UTF-8 notes file first shows the connection, exact Scope and duplicate policy. Importing the same text
again in that Scope, even after renaming the file, reuses its capture identity. Changed text creates another Source.
“Source accepted” does not claim that Memory extraction has finished. If complete Memory browsing or history is
unavailable, the page says so and offers supported search/detail instead of presenting a truncated directory as complete.

Inspect the chosen Agent's binding separately. Browsing Documentation does not redirect that Agent's saves there.
“Installed,” “host loading observed,” and “capture/recall verified” remain separate states; an unobserved check is
unverified. Changing a binding is an explicit authorized action with its own exact target.

## Review a proposal and follow a Handoff

Open a pending Profile Candidate, inspect its typed proposal and evidence, then approve the version shown. If another
client revises it first, the desktop reports the conflict and loads the new state for a fresh decision. It never
approves the replacement automatically. Rejecting a Candidate requires a reason.

A shared Handoff opens the exact authorized revision, even when its containing Scope cannot be listed. Sharing and
delivery are distinct: without a supported delivery contract, the desktop offers authorized reports/shared items,
not a fabricated inbox. With delivery support, open the inbox record and inspect its target and state. Viewing it
does not accept work on an Agent's behalf; acceptance requires the supported receiver flow and its live observations.

## Understand notifications and incomplete results

Review monitoring covers the active connection's current exact Scope. For example, if only part of Documentation's
pending list has been scanned, show “Pending items observed; scan incomplete,” with its coverage and last refresh.
Any number is labelled as observed, not a complete current total. An unread page is not zero, and items can change
between pages. Completing a traversal alone does not establish a point-in-time total without Server support.

Initial monitoring presents a summary rather than one notification per historical item. Later hints are coalesced
and contain no private titles or bodies. Clicking a hint reopens its original context and checks permission again;
a removed or revoked item produces a safe unavailable state. Denying system notifications leaves an in-app view.

## Switch connections, recover and exit

Switching from Personal to Team clears Personal's private views and prompts before discarding unsaved input when
needed. A late Personal search result never appears in Team. An already submitted write still targets Personal;
a lost response is shown as uncertain until its supported recovery establishes the result. Disconnection does not
create an offline write queue, and reconnecting refreshes identity and authorization.

With a tray, closing the last window hides the desktop; **Quit** exits it. Without a tray, the window explains that
closing it exits. Neither action stops the independent Server or accepted durable work. Notifications stop on Quit;
reopening refreshes Server state. Desktop and Server login startup are separate choices.

Updates show the affected components, interruption and recovery plan before applying. After an interrupted update,
use the producer's durable status instead of assuming all components succeeded or repeating the operation blindly.
“Remove desktop,” “Remove local service” and “Delete data” are distinct actions; desktop/service removal preserves
business data by default. Diagnostics can be previewed and exported without private content or credentials.

## Screens and scope

| Screen | First-release behavior |
| --- | --- |
| Overview | Active connection/identity, readiness, local service facts, available capabilities, covered attention items and recovery |
| Projects/workstreams | Scope directory and organization; observation selection separate from exact write/binding targets |
| Memory/assets | Supported directory, search, exact detail, provenance/history; explicit Memory remember/revise/retire |
| Review | Typed Experience, Skill and Profile Candidate detail and approve/reject/revise with version checks |
| Handoff | Committed exact detail, authorized sharing, read-only reports, separately gated delivery inbox |
| Sources/integrations | Confirmed text import, Source discovery, declared Agent support and observed diagnostics |
| Settings/diagnostics | Connections, native credential references, locale, notification coverage, versions, updates and redacted export |

Read Topic Memory and Profile through supported contracts. Prompt editing, Dream administration, a connector
marketplace, a Handoff editor and arbitrary Skill execution are excluded. Unsupported types never gain generic editing
or approval. Existing processes can be connected to without being adopted; local controls require verified ownership.

# Reference-level explanation

The connection and switching examples depend on compatibility, native transport and identity isolation (§§4–7).
Scope, import and Review behavior use the public API and typed family rules (§§3, 8, 11–12). Handoff and incomplete
notification coverage have separate authority and recovery (§§9–10). Closing, updating and removing the desktop
preserve producer ownership and durable Server state (§§6, 13–15). These are architectural contracts, independent of
the implementation schedule.

## 1. Baseline and producer dependencies

The source baseline is upstream `master` at
[`62e4c821709c18b832c77363fdd428765bee6a96`](https://github.com/oceanbase/powercontext/commit/62e4c821709c18b832c77363fdd428765bee6a96),
checked on 2026-09-13. PowerContext 1.0.0 is published; source-baseline capabilities are not automatically available in
every release. Each desktop release pins and qualifies its supported Server and Agent artifacts.

| Existing surface | Reusable implementation | Remaining boundary |
| --- | --- | --- |
| Public API | Scope/Source discovery, generic Artifact/revision reads, Memory, Review, exact Handoff and access APIs | No compatibility handshake or durable delivery inbox; Memory entry/history paging missing |
| Personal Dashboard | Opt-in static-token viewer; Jinja2/HTMX/Tabler/Surreal and Python ASGI API transport | Not a shared management SPA; injected team Provider deployments keep it disabled |
| Authorization | RFC 1396 and [#1398](https://github.com/oceanbase/powercontext/pull/1398) implemented; Principal, checks, resources, bindings and audit | Static Bearer is one shared service identity; desktop identity transports need qualification |
| Personal service | Install, status JSON, uninstall, Windows login-start choice | Install/uninstall lack structured result options; no public start/stop/restart |
| Configuration/Agents | Guided configuration, protected environment, URL-bound host credentials, structured diagnostics | Interactive wizard is not a desktop machine protocol |
| Assets/processing | Profile, Topic Memory, Prompt, tags, Dream and processing supervisor | Family-specific write and maintenance migration rules remain authoritative |
| Distribution | Versioned Python releases and maintained integration manifest | Unified installer remains dependent work; Windows support is currently experimental |

The following are requirements on the responsible producers, **not implemented interfaces**. Each producer defines its
schema and semantics before a desktop feature relies on them. An unavailable dependency disables only that feature;
staffing, delivery gates and qualification evidence belong to the delivery plan.

| ID | Producer / related work | Required contract |
| --- | --- | --- |
| D1 | Server/API | `server-info`, deployment identity lifecycle, explicit compatibility profiles |
| D2 | Server/Memory | Authorized bounded entry listing and, before history UI, bounded change/history queries |
| D3 | Service/configuration, [RFC 1299](1299_local_server_availability_and_service_installation.md) | Noninteractive structured mutations, protected input, ownership and recovery |
| D4 | Installer [#1406](https://github.com/oceanbase/powercontext/issues/1406), RFC [#1408](https://github.com/oceanbase/powercontext/pull/1408) | Verified bootstrap, plans, locks, durable operation/status and recovery |
| D5 | Distribution [#1405](https://github.com/oceanbase/powercontext/issues/1405), RFC [#1410](https://github.com/oceanbase/powercontext/pull/1410) | Immutable host artifacts, compatibility and host-owned install adapters |
| D6 | Delivery [#1419](https://github.com/oceanbase/powercontext/issues/1419) | Receiver association, envelope, durable inbox, exact references, deduplication and recovery |
| D8 | Server/connector owners | Public connector discovery, health and administrative operations, if offered |

D4/D5 RFCs and D6 tracking work remain open at this baseline. Existing authorization need not wait for D6: authorize
each consuming operation independently. Scope integration bindings and Access role bindings are separately named concepts.

## 2. Components and UI sharing

```text
Bundled desktop UI -> typed IPC -> Rust host -> public HTTP API -> independent Python Server
                                    |
                                    +-> OS credentials, tray, notifications, file handles
                                    +-> installer/service/configuration machine interfaces
OS service manager -> independent Python Server -> persistence and durable processing
Installer          -> verified runtime/Agent artifacts and installation journal
Personal Dashboard -> its own server-rendered pages -> public API authorization
```

Add `desktop/`, with `desktop/src-tauri/` for the native host and `desktop/ui/` for a **React + TypeScript + Vite** client.
React organizes pages, reusable components and interactive state for configuration forms, Review, installation progress
and connection switching. TypeScript checks frontend and API/IPC types; public operation schemas remain derived from
OpenAPI. Vite provides the development server and builds static HTML/CSS/JavaScript bundled into the Tauri application.
The installed frontend needs no Node.js server, SSR or Next.js runtime; setup/recovery works before Python is installed.

Keep the desktop build independent of the documentation website. React does not migrate Jinja/HTMX pages automatically
or require a Dashboard rewrite. State and request handling must still enforce connection/identity isolation; adopting
React does not replace native validation or Server authorization. Domain rules stay on the Server; installation and
service logic stay with their existing owners. Evaluate frontend dependencies and rendering costs in the delivery plan.

Initially share brand assets, design conventions, translations and suitable display components. Shared code must have
one canonical source, deterministic build/copy and drift verification. The extraction PR identifies files and licenses.
Server templates/static resources remain under `src/powercontext/server/dashboard/` and in the Python wheel. Installing
Python or running the Dashboard must not require Node, Rust or a desktop build on the user's machine.

Do not copy runtime Jinja rendering, Python `DashboardAPI`, HTMX `/dashboard/*` navigation, cookie login or inline scripts
into the desktop. Use client rendering and typed API actions. Setup/recovery pages work without Python or a Server.
Sharing a future full Web management client requires coordination with [#1341](https://github.com/oceanbase/powercontext/issues/1341);
this RFC does not expand the personal Dashboard's read-oriented scope or enable it for team Providers.

## 3. Public operation matrix and bounded browsing

The desktop does not import Runtime objects, open databases, scrape HTML or consume private Dashboard endpoints.
Generate or validate operation IDs, schemas, path encoding and response types against `openapi/powercontext.yaml`.
Avoid separate hand-maintained Rust/JavaScript catalogs. Permissions below identify relevant checks, not the complete
Server policy:
compound evidence, target, publication and current-state checks still apply.

| UI behavior | Existing operation IDs | Authorization / consistency | Availability / boundary |
| --- | --- | --- | --- |
| Connection | `get_liveness`, `get_readiness`, `get_capabilities`, `get_access_principal` | Health is not identity; protected calls use current policy | D1 handshake |
| Scope directory | `list_scopes`, `get_scope`, `get_default_scope`, `resolve_scope_selection` | Authorized discovery, opaque cursor and actual selection semantics | Existing contract |
| Scope/binding changes | `create_scope`, `update_scope`, `set_scope_binding`, `clear_scope_binding`, `resolve_scope_binding` | Creation/admin checks, defined expected versions, exact target | No global binding-list promise |
| Memory | `remember_memory`, `search_memory`, `list_memory_entries`, `get_memory_entry`, `revise_memory_entry`, `retire_memory_entry`, `list_memory_changes` | Applicable Scope/resource checks; exact citation | D2 full browse/history |
| Source | `list_sources`, `get_source`, `capture_content_source` | Authorized Scope/source access, paging, immutable capture identity | Existing contract |
| Artifact browsing | `list_artifacts`, `get_artifact`, `get_artifact_revision`, `list_artifact_revisions` | Family policy, exact revision, ETag and opaque paging | Existing contract |
| Topic Memory detail/search | `get_topic_memory`, `search_topic_memory` | Dedicated selection/search limits and current family policy | Read only; no manual write/flush |
| Tags | `get_artifact_tags`, `replace_artifact_tags`, `get_memory_entry_tags`, `replace_memory_entry_tags`, `query_artifact_tags` | Supported taggable families, exact logical target and required tag-state `If-Match` | Tags do not change content revisions |
| Review | `list_artifact_candidates`, `get_artifact_candidate`, `approve_artifact_candidate`, `reject_artifact_candidate`, `revise_artifact_candidate` | Read/review and proposal/evidence checks; `expected_version` | List requires exact Scope |
| Skill lifecycle/package | `list_managed_skills`, `update_skill_lifecycle`, `get_skill_package_manifest`, `download_skill_package` | Resource checks, `expected_generation`, exact reviewed package | Never execute downloaded code |
| Publication | `publish_artifact`, `publish_remote_skill` | Share/target administration and publication policy; exact reference/generation | Qualified targets only |
| Sharing | `get_access_principal`, `check_access`, `list_access_resources` | Current Principal, safe filtering, exact share unit | Role-administration UI excluded |
| Handoff | `get_handoff_report`, `continue_handoff`, `acknowledge_handoff`, `record_task_outcome` | Report versus exact evidence/receipt rights; receiver observations | Existing reads; D6 for delivery/receiver flow |
| Statistics | `get_stats` | Authorized projection and supported selection; missing is not zero | Existing contract |
| Local management | Producer machine interfaces; `service status --json`, `doctor integrations --json` | Verified local ownership and protected configuration | Existing status; D3/D4/D5 mutations |

Memory entry listing currently returns the full collection without cursor/limit; changes also lack paging. Client
pagination or a response-size cap does not fix that. D2 specifies server-side limits/filters, stable order, cursor
expiry/snapshot behavior, concurrent revision handling and authorization. Artifact paging does not page entries inside
a Memory Artifact. Before D2, offer bounded search and exact detail; any small-dataset directory discloses its cap and
reports a limitation instead of truncating or inventing totals. History stays unavailable until bounded.

Do not enumerate assets from Candidate history or reinterpret every generic list as chronological. Scope binding
views resolve a known host binding, not an invented global registry. Keep actual search caps and modes visible.

## 4. Handshake and compatibility

Propose authenticated `GET /v1/server-info` under D1 with `schema_version`, `product`, persistent opaque `server_id`,
`package_version`, `api_contract_version` and `feature_contracts`. Protocol versions have explicit major/minor components:
major changes required semantics; minor adds compatible optional fields/features. The desktop accepts supported majors
and required minimum minors, ignores unknown optional fields and enables only its tested operation groups. Feature
names and exact OpenAPI types belong to D1; this RFC does not add the endpoint.

`server-info` describes deployment/protocol identity; `access/me` supplies Principal/mode/Provider/family access;
`capabilities` supplies runtime functions. Each operation requires desktop support, compatible contract, available
runtime capability and current authorization. Explain which condition fails without disabling independent functions.

| Result | Behavior |
| --- | --- |
| Supported handshake | Validate product/schema and operation-group compatibility |
| Handshake 404 | Only an explicitly selected, shipped and tested legacy profile; otherwise diagnostics only |
| 401 | Stop protected retries, request valid credentials; do not infer expiry |
| 403 | Explain denial; no anonymous downgrade or endpoint fallback |
| 503 / authentication unavailable | Show outage and bounded retry; preserve credential/identity selection |
| Unknown required major/product/feature | Block affected operations, explain compatible versions |
| Missing optional capability | Leave independent supported operations available |
| Changed Server identity | Invalidate pending contexts/selections and require explicit reconnect |

Legacy profiles record tested tag/commit, schema artifacts and operations. 1.0.0 is an initial qualification candidate,
not compatibility with all current-master capabilities. User-selected versions and successful probes do not attest
remote binary identity. No guessed support, untested mutation or automatic upgrade; do not reuse persistent notification
cursors across legacy reconnects.

The proposed `server_id` identifies a logical deployment: restart, supported upgrade and restoration of that deployment
preserve it; a clone intended as a different deployment gets a new ID before serving clients. Replicas share their
deployment ID. D1 defines persistence, backup/restore and clone provisioning. This is neither the configurable Access
`deployment_id` nor proof of trust. TLS, credentials and verified local ownership remain the trust basis. Handshake
metadata excludes paths, secrets and unauthorized inventories.

## 5. Transport, connection isolation and IPC

Profiles persist an opaque ID, label, normalized endpoint/base path, mode, credential reference, TLS settings and
observed compatibility. One window has one active profile; one instance per OS user/channel uses native current-user
IPC for activation, never another HTTP management listener.

Changing profile, endpoint, TLS trust, credential or observed Principal advances a native-owned generation. Cancel
reads, clear private views/cursors and volatile drafts after any required discard confirmation, and reject late results.
Submitted writes stay bound to their original endpoint, identity, Scope, reference and generation. Selection changes
never retarget them. Endpoint edits detach old credential references and require explicit new-target provisioning.
In the Personal-to-Team example, this generation check discards the late Personal result while retaining the original
target of any write already submitted there.

First-release remote transport is HTTPS-only. Loopback HTTP follows `tests/fixtures/transport_loopback_vectors.json`.
Current CLI/SDK non-loopback plaintext opt-in is intentionally unsupported on desktop; importing it explains this limit.
Reject URL userinfo/query/fragment, path-prefix escape and authenticated redirects. Validate TLS hostname/certificate;
custom CA trust is explicit and profile-bound, not a disable-verification option. Preserve supported API base paths and
encode each path segment through generated rules.

The first release uses direct API connections, without inheriting shell proxy variables or OS proxy credentials.
Proxy-required deployments are unsupported until an explicit profile-bound adapter is qualified; setup explains this.

| Bridge capability | Allowed data | Native enforcement |
| --- | --- | --- |
| Connection/credential | Profile selection, write-only replacement, safe facts | Trusted main/settings window; native generation; no secret read-back |
| API read | Allowlisted operation and typed parameters/result | Compatible contract, exact profile, cancellation and response limits |
| API mutation | Allowlisted operation, typed payload, expected version and explicit action context | Original profile/Scope/reference; no arbitrary URL/header authority |
| Import/package export | OS-selected file or one-use save handle, bounded progress | No renderer paths; byte/digest validation; no execution |
| Local management | Producer-approved plan or supported command parameters | Verified ownership, machine protocol and confirmation bound to that exact plan |
| Notification/diagnostic | Approved metadata, opaque navigation handle, redacted model | No raw errors, secrets, shell, database or unrestricted filesystem |

Restrict custom application commands as well as plugin permissions. Tauri's default treatment of `invoke_handler`
commands is not deny-all, and overlapping capabilities merge permissions. Explicitly list windows/commands and test
unauthorized window calls. No generic fetch, shell, process-kill, SQL or raw filesystem bridge.

Only trusted bundled documents receive capabilities. No privileged remote navigation/scripts. Strict CSP and safe
text/Markdown reject executable HTML and remote image loading. External HTTP(S) links open in the system browser only
on user action; other schemes need a qualified allowlist. Do not copy Dashboard inline scripts by weakening CSP.
Server text, imports and update notes are untrusted data.

Transport policies bound connect/read waits and decoded response size; stricter Server limits take precedence.
Operation-specific limits must explain unsupported or oversized results without silent truncation. Binary packages
stream in native code under a declared export limit with digest verification, not unbounded JSON/base64. Timeouts
and byte budgets are selected through implementation and measurement. Long mutations retain their own recovery
contracts; a timeout is not permission to replay. Errors return safe category/code/request ID, never arbitrary
response bodies or CLI stdout/stderr.

## 6. Service, installer and configuration contracts

Reuse the single per-user service: Task Scheduler on Windows, LaunchAgent on macOS, systemd user service on supported
Linux systems. No root/SYSTEM service, competing desktop supervisor or adoption of an unknown live process. Local
service configuration remains loopback-local.

Preserve independent `support`, `registration`, `definition`, `manager_ownership`, `manager`, `server_liveness`,
`endpoint`, `log_location`, and `recovery_action` facts. A nonzero status-command exit can contain valid unhealthy JSON.
Unknown/foreign ownership, stale environment identity and an occupied port get distinct supported recovery.

Current install/uninstall are human-facing; Windows install may prompt without its login-start choice. They are not
D3's machine protocol. Start/stop/restart controls wait for service-owner support; uninstall is not Stop. D3/D4 require:

- Versioned request/result, stable errors, a supported entry point and noninteractive execution. Configuration
  validate/apply reuses existing rules and service reconciliation of protected env-file identity.
- Protected file or inherited private input for secrets, never command arguments or ordinary output. Do not parse
  the wizard or duplicate environment and host-configuration merge rules in Rust.
- Non-mutating resolve/preflight, immutable component identities, paths, ownership, compatibility, service transitions
  and recovery. Revalidate before applying; confirmation is tied to the exact plan, not a later replacement.
- Producer operation ID, progress, cancellation boundaries, lock, durable journal and status lookup. Report
  unsupported/skipped/current/installed/stale/failed/uncertain per component; verify uncertainty before retry.
- Recovery after client exit, without inventing global atomic rollback across unrelated hosts.

CLI and desktop share producer locks. Installed does not mean host-loaded or healthy. Only a producer with durable
execution can continue installation after Quit. Otherwise keep the operation window and allow cancellation only at
safe boundaries; forced termination recovers through the journal. The steady-state Server always runs independently.

## 7. Credentials and authentication modes

Use Windows Credential Manager; qualify Keychain/Secret Service for later platforms. Preferences store opaque references.
If unavailable/locked, offer unlock or session-only use, not plaintext fallback. A separately supported encrypted vault
is possible; Stronghold is not itself an OS credential adapter. Tokens may transiently occupy trusted input/write-only
IPC; clear on submit/cancel. Never persist them in renderer storage, URLs, arguments, logs, exports, crashes or notifications.

| Mode | Behavior |
| --- | --- |
| Existing unauthenticated loopback | Explicitly show unprotected local access; do not silently reconfigure |
| Enforced static Bearer | Show shared service identity, not multiple team members |
| Enforced injected Provider | Support operator-issued Bearer accepted by that Provider; obtain identity and checks from Server |
| Unsupported login transport | Explain unsupported authentication; no embedded remote login, token scraping or anonymous fallback |

Browser SSO/OAuth, cookie sessions and interactive enterprise login are excluded initially. Qualify actual Provider
identity resolution; `multi_principal` alone does not prove a supported login flow. Each operation stays Server-authorized.
Generic `401 unauthorized` means valid credentials are needed, not necessarily expired. Show expiry only when a supported
contract supplies it; distinguish `403` denial and `503 authentication_unavailable`. Stop protected loops on rejection.

New managed local setup enables enforced authentication by default; unauthenticated attachment remains an explicit
existing-installation mode. The configuration producer generates the credential in a protected Server environment. After
explicit setup approval, a secure machine interface provisions the desktop OS-store entry and selected hosts through
existing URL-bound authorization adapters, without returning secrets through page data. Server/Agents must work without
the desktop or its unlocked vault.

Rotation is a producer plan: check ownership/consumers, stage protected configuration, reconcile the service, replace
desktop and selected-host credentials, then verify each consumer. Static Bearer has no assumed old/new overlap;
disclose interruption and partial failure. Resume through producer status, not one successful client reconnect.
Deleting a profile removes only its owned reference/unreferenced credential entry, not Server/Agent environment files.

## 8. Authorization, Scopes and assets

Use `access/me` and supported checks for UI explanations, then authorize each real request again. Agent names,
`receiver`, labels and renderer input do not establish identity. Filter before paging/counts; unsupported safe queries
fail without unrestricted fallback. Cache/cursor keys include endpoint, Principal/credential generation and query;
prechecks are not durable grants.

Scope IDs are opaque, not paths/branches/session IDs. Parent organization does not inherit permission, share Context
or publish Artifacts. Show only authorized ancestor information. `all/subtree/exact` are observation selections, not
universal list modes. Pages use supported API selections; writes/bindings use an explicitly displayed exact Scope.

| Type | First-release handling | Write boundary |
| --- | --- | --- |
| Memory | Search/detail/citations; D2-bounded directory/history | Dedicated remember/revise/retire, original citation and conflicts |
| Experience/Skill | Generic directory, typed exact detail, provenance/lifecycle | Reviewed Candidate flow and qualified publication |
| Profile | Authorized read and typed Profile Candidate Review | Preserve proposal/policy/evidence requirements; no generic Review bypass |
| Topic Memory | Generic published directory/revisions, supported dedicated detail/search | No manual generic create/replace/delete |
| Handoff | Committed exact revisions and separately authorized evidence | Continue, receipt and outcome retain distinct semantics |
| Prompt/Dream/unknown | No dedicated administration; safe supported metadata/detail only | No generic edit/approve/execute for unsupported types |

Tags follow the current taggable-family contract. Generic read does not imply generic write. Unknown Candidate proposals
retain type and identity but disable review until a typed display/validator exists; partial forms cannot discard unknown
fields. Distinguish Source pending, Candidate, committed Artifact, published package and retired entry.

Review sends `expected_version`; rejection requires the contract's nonblank reason, and revision preserves evidence
and omitted-field semantics. The Profile example submits the version actually shown to the user. Conflicts reload
state without silently approving a new version. Skill lifecycle uses
`expected_generation`, packages use exact reviewed references. An exact Handoff grant does not authorize latest/adjacent
revision, broad reports, unrelated searches or unauthorized evidence. Exact shared items remain reachable without
permission to enumerate their containing Scope.

## 9. Handoff discovery and receiver association

| View | Authority | Meaning |
| --- | --- | --- |
| Report | Existing report API | Authorized read-only selection projection |
| Shared with me | Access resource discovery | Exact accessible identities, not unread/delivery state |
| Delivery inbox | D6 | Durable delivery records and supported receiver recovery |

Prepared Handoff is not an enumerable durable inbox. Access paging, Review and remote Skill receiver APIs cannot
substitute for delivery. Before D6, only report/shared-resource views are available.

The desktop is an **observer/controller for targets the current Principal may manage**, not automatically an Agent
receiver. A locally installed Agent does not authorize enrollment or impersonation. D6 must define trusted
Principal-to-target association, enrollment/discovery, envelope version, immutable reference, deduplication ID,
list/resume cursors, expiry/cancellation, retryable/terminal states and safe diagnostics. It also defines whether read
state belongs to Principal or target; device-local notification deduplication never changes Server read state.
The desktop creates no second registry, envelope protocol or retry scheduler.

Opening an envelope rechecks permission and delivery state for its original exact reference. Missing/expired/cancelled/
revoked items reveal no cached body and cannot fall back to broader reports. Viewing, mark-read if supported, delivery,
access grants, accepted receipt and Task Outcome are separately named actions.

`accepted`, `needs_clarification` and `declined` preserve existing semantics. Acceptance needs actual receiver live-state,
capability, authorization and evidence observations; browsing cannot attest to another Agent's environment. Only offer
acknowledgement through a qualified flow supplying those checks. Use declared exact-item host launch mechanisms;
otherwise offer supported copy/open without tokens/bodies in URLs. Only qualified sender/receiver pairs and versions expose those actions.

## 10. Notifications and background limits

The incomplete-scan example uses Server Candidate state as authority; an implemented delivery inbox has its own
authority. Notifications are best-effort hints, not a queue or exactly-once guarantee. Initially monitor only the
active connection's **current exact Scope** for Review. All/subtree browsing does not subscribe every Scope. Preserve
the monitored Scope while hidden in the tray and show its coverage explicitly.

Polling must bound concurrency, request rate, response size and background work. Respect the Server's pagination
contract; the current Candidate list allows `limit` from 1 to 100 and defaults to 50. This is a Server limit, not a
chosen desktop page size. Its response contains `candidates` and `next_cursor`, with no total. A list cursor is not an
event cursor. Whole-system counts or event history require a separate Server contract.

Show coverage, refresh time and whether traversal is incomplete, complete or stale. A partial scan can report items
observed during that scan, never a complete total or zero for unread pages. Even a complete traversal can span
concurrent changes; without a snapshot/count contract, label its count as observed rather than an exact current total.
If a traversal becomes stale or its cursor expires, explain the limitation and refresh through supported semantics.
The scheduling policy must consider backlog progress as well as freshness; repeatedly restarting at the first page
must not silently imply coverage of later pages. Background limits do not prevent explicit paged Review navigation.

First activation or coverage change displays a summary, not a notification for every historical Candidate. Deduplicate
subsequent observations within their profile/Principal/Scope/Candidate ID/version context; a changed pending version
can produce a coalesced hint. Stop protected polling on authentication rejection; back off transient failures and
honor Server retry delays. Manual retry cannot create an uncontrolled background loop. Polling interval, page size,
jitter, traversal expiry and backoff values are implementation choices established through measurement.

D6 delivery uses a separate bounded consumer. Persist only opaque cursors and deduplication/navigation metadata with
bounded storage and retention. Expired or evicted handles fail safely; metadata expiry does not mark Server items as
read or erase durable deliveries. Set capacity and retention through implementation and validation. Identity changes
clear private metadata and dismiss posted notifications where possible; a remaining generic OS hint grants no access.

Use a generic “PowerContext has items needing attention” message with a local opaque navigation handle, not bodies,
sensitive titles, paths, tokens or raw errors, including on lock screens. Clicking deliberately restores the original
profile and reauthorizes the exact item; forged handles cannot silently switch credentials or mutate. Explain/request
permission; provide in-app fallback and suppress repetitive offline errors. Quit stops notifications; reopening
refreshes Server state. An existing hint activated after exit follows the same authorization rules.

## 11. Source import, connectors and Agent diagnostics

First support entered text or one selected UTF-8 file through `capture_content_source` (`POST /v1/sources/content`).
Do not mix its caller-stable identity with generic `create_source`, which allocates a new identity. Read an OS-selected
handle, not a renderer path; prevent replacement/link races, traversal and unrequested directory scans.

Use one platform-independent import policy:

1. Declare and enforce a bounded file-read budget before reading. Decode strict UTF-8, remove one leading BOM, preserve
   remaining Unicode code points, line endings and whitespace. Reject invalid encoding and blank content.
2. Enforce declared content and transport/Server limits, explaining them before confirmation. Reject oversize input;
   never truncate. Concrete client limits are chosen during implementation and measurement.
3. SHA-256 hash the submitted UTF-8 text. Set `source_id` to `desktop-text-v1:<lowercase-hex-digest>` and metadata to
   the constant `{"importer":"powercontext-desktop-text-v1"}`. No filename/path/time/device/user metadata enters the
   payload. Confirmation may display the local filename without uploading it.
4. Confirm endpoint, exact Scope, normalized size and duplicate policy. Identical submitted text within one Scope
   uses the same capture; changed text creates another Source. Renaming does not duplicate identical content.
   Different Scopes remain independent. This is snapshot import, not file synchronization.
5. Retry the same approved import with identical identity/content/metadata. A stored-payload conflict is an error,
   not permission to overwrite or silently choose another ID. Native recovery reads verify the submitted text.

There is no durable local content queue. Reselecting the same file after restart reproduces its identity for an
authorized inspection/retry. Remote imports transmit approved bytes, never treat local paths as remote paths. Capture
success does not claim extraction completion.

Source definitions/observations/checkpoints do not provide connector management. Before D8, show supported facts only,
not speculative start/retry controls. Accepted jobs, credentials and checkpoints belong to Server/connector workers;
incomplete crawls do not imply deletion. Desktop exit cannot cancel accepted durable work.

Show release-specific `integrations/capabilities.toml`, installer records and `doctor integrations --json` as distinct
facts: declared support, installed ownership/version, observed loading/connectivity/Scope/capture/recall, enrollment
only when actually provided. Metadata is not a live target registry. Host installation is opt-in; adapters own merging
and repair. Rust cannot rewrite all detected configurations or infer health by counting files/tools.

## 12. Concurrent writes, cancellation and unknown results

No persistent business cache or offline write queue. Offline remote views hide private content; unsent forms may stay
in memory as visibly unsaved input until policy or confirmed identity change discards them. Reconnect refreshes
compatibility, identity, authorization and resources. Local offline availability does not promise offline generation.

| Mutation | Guard | Recovery after lost response |
| --- | --- | --- |
| Candidate approve/reject/revise | ID and `expected_version` | Read Candidate/result; changed state does not prove this client caused it; never auto-approve newer versions |
| Memory remember/revise/retire | Supported expected revision/exact citation as applicable | Read exact/current entry and bounded history when available; uncertain attribution is not replay permission |
| Text import | Stable ID and identical complete payload | Read/compare exact Source or explicitly repeat the same idempotent capture under current authorization |
| Tag replacement | Exact logical target and required tag-state `If-Match` | Read current tag set; conflict needs a fresh user decision |
| Skill lifecycle/publication | Exact reference and required generation | Read lifecycle/target state; retain uncertainty if attribution is unavailable |
| Handoff receipt/outcome | Exact revision, receiver observations, accepted receipt identity | Producer's supported read/idempotency path; otherwise unknown and supported receiver recovery |
| Install/configuration/update | Approved plan and producer operation ID | Read durable status, verify uncertain components, resume at supported boundary |

Do not invent operation-status endpoints or idempotency keys. Disable duplicate submission while pending. Cancelling
a read may discard its response; cancelling a wait for a submitted mutation does not cancel that mutation. Retry
authority never transfers to another profile/Scope/revision. Preserve volatile intent on conflict without auto-applying it.

## 13. Distribution, updates and migration

Use signed per-user Windows packages. Managed local setup works without preinstalled Python/Node/Rust/Git/compiler;
D4 supplies verified versioned Python and D5 selected hosts. Current service requires real Python and adjacent
`pythonw.exe` on Windows. Frozen binaries need separate qualification; a Tauri sidecar does not own Server lifetime.

Immutable release plans record desktop/interpreter/runtime/API/features/Agent versions, OS/architecture and data
compatibility. Pin trusted publishers/sources outside renderer control. Distinguish OS signing, Tauri updater signature
and runtime/Agent manifest trust. A checksum next to an untrusted artifact is not authentication. Define key
rotation/revocation and release CI ownership before release; offline packages declare remaining network needs.

Stable is default. A preview channel can coexist as a connection client, but only one recorded channel manages a local
installation. Ownership transfer requires an explicit compatible producer plan. Both may connect to the single
per-user service; neither creates a competing registration/SQLite supervisor. Preferences and credential references
are channel-owned. Removing one channel preserves components still referenced by another channel or Agent.

Updates are explicit installer plans: check space/ownership and compatibility, stage/verify artifacts, disclose
interruption/data recovery, switch through supported service operations, then verify readiness and selected hosts.
No automatic remote update or global atomic rollback. Restore binaries/configuration only when data remains compatible.

Affected databases already require `server processing-migrate --action plan/apply/verify`: stop old workers and their
automatic restart, pause writes/triggers, resume with the same migration ID, verify `ready: true` before traffic.
Use a safe producer-owned maintenance workflow or explain manual maintenance; repeated restart is not migration.
Use backend-supported backup, not a live SQLite file copy. Irreversible changes require supported recovery or a clear
forward-repair plan and approval. Never run an incompatible old runtime against migrated data.

Desktop-only and runtime updates are separate. Windows Tauri updater exits the application before installing;
persist producer operation/checkpoint and recovery entry first. UI memory cannot own unfinished coordinated work.
If cross-exit recovery is unsupported, finish or safely defer the runtime operation before updating the desktop.
Failed download/signature leaves old usable state; partial success/uncertainty is reported per component.

## 14. Data ownership and removal

| Component | Owner | Default removal |
| --- | --- | --- |
| Desktop binary/UI | Desktop package/updater | Remove selected app/channel |
| Profiles/preferences/bounded notification metadata | Desktop user directory | Explicit reset/removal option |
| Desktop credentials | OS store | Only owned, unreferenced selected entries |
| Python/Agent artifacts/install journal | Installer/distribution | Preserve references; ownership-aware plans |
| Service registration/protected environment | Service/configuration | Preserve unless separately removing service |
| Memory/Source/Artifact/processing/database | Server persistence | Preserve on desktop/service uninstall |
| Host configuration | Host adapter/user | Revert recorded owned changes only; preserve unrelated/user edits |

Existing `POWERCONTEXT_HOME` and platform rules remain authoritative. Versioned app directories are not data directories.
Local diagnostics can show/open known local paths; remote profiles cannot browse Server filesystems. Separately name
Remove desktop, Remove local service and Delete data. The first automatic uninstaller does not delete business data.
Preserve unknown ownership; do not recursively delete arbitrary selected locations.

## 15. Diagnostics and privacy

Expose verified component/platform/status, contract versions, safe request IDs, bounded timing and normalized failure
codes. Ordinary logs/exports exclude bodies, prompts, prepared context, model output, credentials, Authorization,
raw environment, sensitive titles, URL queries and private paths. Showing a local path to its owner does not add it to
export. Normalize CLI/Server errors rather than attaching stdout/stderr.

Export is local, explicit and previewable. Crash reporting is off by default; content-free diagnostics never justify
memory dumps or raw requests. Bound log size/retention. Threats include malicious content/imports, forged activation/IPC,
wrong endpoints and tampered artifacts, not a promise against compromised OS, arbitrary same-user malware or privileged
administrators. The privacy guarantees apply to every observable output, including failures and diagnostic exports.

## 16. Platform and accessibility

Propose Windows 11 x64 with SQLite as the first qualified platform; Windows support at the source baseline remains
experimental. Windows ARM and Windows embedded seekdb are excluded. macOS/Linux follow only after their installed
behavior is qualified. Framework compilation alone does not establish platform support.

Maintain English/Chinese UI and docs, keyboard navigation, visible focus, screen-reader labels, IME-safe forms,
high contrast and non-color-only status. Supported actions, confirmations and recovery remain usable in small windows
and with enlarged text. Locale/theme changes preserve identity and current context. The delivery plan records tested
environments, viewport/zoom cases and measured performance budgets for the complete desktop, WebView and Server.

# Drawbacks

This adds Rust/native maintenance, a client UI/build, signing/updates and platform tests. Sharing presentation reduces
some duplication, not the need for client management flows. Python/WebView may dominate footprint. Independent runtime
updates require compatibility/data recovery; delivery remains another producer's dependency.

# Rationale and alternatives

| Consideration | Tauri 2 | Electron | Decision |
| --- | --- | --- | --- |
| Web presentation | System WebView | Bundled Chromium | Both support client UI; neither makes Jinja portable |
| Native boundary | Rust with application/plugin permissions | Main process and constrained preload/IPC | Prefer small Rust host, verify permissions |
| Footprint/rendering | System dependencies/engine differences | Larger engine, more consistent rendering | Measure complete installed product |
| Python/service/data | External runtime and migration | Same requirement | Neither replaces installer/service contracts |
| Maintenance | Rust/platform experience | Electron/JavaScript experience | Requires sustainable maintenance/release ownership |

Choose Tauri 2 subject to installed-platform validation. Electron is the fallback for demonstrated WebView/native
integration or sustainable maintenance blockers while retaining all API/ownership rules; do not maintain two
production shells. Web-only remains useful but lacks required native capabilities. Privileged remote pages,
desktop-owned Server children, Runtime rewrites
or a fully native duplicate presentation are not justified by this scope.

Without a desktop, existing CLI and Dashboard use remains available, but users must coordinate native credentials,
service setup, updates and notifications through separate tools.

# Prior art

The personal Dashboard provides server-rendered read-oriented presentation; it does not supply portable client-side
management flows. The accepted service design supplies independent runtime ownership, and the Scope/access/asset
contracts supply the same domain semantics used by CLI and other clients. This proposal composes those boundaries
with a constrained native host. Tauri and Electron provide shell mechanisms, not a replacement Server or installer.

Project contracts: [service](1299_local_server_availability_and_service_installation.md),
[Scopes](1345_scope_organization_and_agent_integration.md), [access](1396_handoff_access_control.md),
[Sources](1400_source_definition_and_observation_model.md), [base REST](1437_source_artifact_rest_api.md),
[Profiles](1485_profile_artifact.md), [processing](1515_artifact_processing_supervisor.md),
[family reads](1549_artifact_family_unification.md), [Skill lifecycle](1351_standard_skill_package_lifecycle.md),
[Dashboard](../development/dashboard.md), [processing migration](../docs/operate/artifact-processing-migration.md).

Qualify framework mechanisms in actual packages: [Tauri architecture](https://v2.tauri.app/concept/architecture/),
[capabilities](https://v2.tauri.app/security/capabilities/), [CSP](https://v2.tauri.app/security/csp/),
[Windows installer](https://v2.tauri.app/distribute/windows-installer/), [notifications](https://v2.tauri.app/plugin/notification/),
[updater](https://v2.tauri.app/plugin/updater/), [Stronghold](https://v2.tauri.app/plugin/stronghold/),
[Electron security](https://www.electronjs.org/docs/latest/tutorial/security).

# Unresolved questions

Before accepting this RFC, maintainers should agree on the first platform (proposed Windows 11 x64 + SQLite), the
Tauri/client boundary and presentation-only Dashboard sharing, direct HTTPS with operator-issued Bearer for remote
use, and whether a clearly limited connect-only preview is useful before managed installation and delivery.

Producer contracts still need separate agreement: D1 compatibility/version support and deployment restore/clone
identity; D3–D5 machine management, provisioning and recovery; D6 receiver association, durable inbox and read state.
These block the corresponding features, not unrelated authorized browsing. The desktop must not invent private
replacements while those contracts are unavailable. The delivery plan tracks owners, qualification and measurements.

Proxy/browser login, persistent offline writes and broader Agent execution are outside this proposal; supporting
them requires separate contract decisions rather than silently extending the first release.

# Future possibilities

After initial qualification, add macOS/Linux, proxy/browser identity adapters, explicitly chosen multi-Scope/profile
monitoring, more import formats and contracted connector administration. Persistent offline content/writes and broader
Agent execution introduce new consistency/security duties and require separate proposals.
