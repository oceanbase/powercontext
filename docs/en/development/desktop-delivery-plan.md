---
title: "Desktop delivery plan"
description: "Implementation phases, producer coordination and qualification evidence for the desktop control center."
---

# Desktop delivery plan

This proposed plan tracks implementation of [RFC 1455](../rfcs/1455-desktop-control-center.md) under
[#1428](https://github.com/oceanbase/powercontext/issues/1428). The RFC defines user behavior and architectural
contracts; this plan records phases, owners, measurements and acceptance evidence. Scheduling and tuning can evolve
without redefining those contracts. The entries below are requirements, not completed work or qualification results.
Accepting the RFC or shipping a connect-only preview does not close #1428.

Source facts use the RFC's baseline, upstream
[`62e4c821709c18b832c77363fdd428765bee6a96`](https://github.com/oceanbase/powercontext/commit/62e4c821709c18b832c77363fdd428765bee6a96),
checked on 2026-09-13. Recheck platform blockers and producer availability against the artifacts selected for delivery.

## Producer coordination

D1–D6 and D8 identify producer contracts in the RFC. D7 tracks staffing and release qualification in this plan.
Assign named maintainers and link producer work before the corresponding phase exits.

| ID | Producer / related work | Required contract | Gate |
| --- | --- | --- | --- |
| D1 | Server/API | `server-info`, deployment identity lifecycle, explicit compatibility profiles | Compatibility-dependent P1/P2 controls |
| D2 | Server/Memory | Authorized bounded entry listing and, before history UI, bounded change/history queries | Complete Memory browsing in P2 |
| D3 | Service/configuration, [RFC 1299](../rfcs/1299_local_server_availability_and_service_installation.md) | Noninteractive structured mutations, protected input, ownership and recovery | Managed service/configuration changes in P3 |
| D4 | Installer [#1406](https://github.com/oceanbase/powercontext/issues/1406), RFC [#1408](https://github.com/oceanbase/powercontext/pull/1408) | Verified bootstrap, plans, locks, durable operation/status and recovery | Managed install/update in P3 |
| D5 | Distribution [#1405](https://github.com/oceanbase/powercontext/issues/1405), RFC [#1410](https://github.com/oceanbase/powercontext/pull/1410) | Immutable host artifacts, compatibility and host-owned install adapters | Selected-host install in P3 |
| D6 | Delivery [#1419](https://github.com/oceanbase/powercontext/issues/1419) | Receiver association, envelope, durable inbox, exact references, deduplication and recovery | Delivery consumption in P4 |
| D7 | Desktop/release maintainers | Named owners, Windows/host qualification, supported versions and measured budgets | P0 exit and P5 release |
| D8 | Server/connector owners | Public connector discovery, health and administrative operations, if offered | Only corresponding connector controls |

## Platform, accessibility and budgets

| Platform | Proposed status | Qualification |
| --- | --- | --- |
| Windows 11 x64 + SQLite | First target; current project Windows support is experimental | Signed standard-user install, WebView2 absent/present, Credential Manager, Task Scheduler/login, notifications/activation, non-ASCII paths |
| macOS | Follow-up | Named architectures, Keychain, LaunchAgent, signing/notarization, WebView/notification behavior |
| Linux | Follow-up per distro/desktop | WebKitGTK/system libraries, Secret Service, systemd session, tray, package/activation |

Windows ARM and Windows embedded seekdb are excluded. Compiling a framework does not qualify a platform. P0 names
Desktop/Install/Server/Release owners and one maintained Agent Host/version, with observed Windows load and explicit
capture/recall. “Any maintained integration” cannot pass that gate; P4 names an actual sender/receiver pair.

At the source baseline, native Windows type checking exposes `Connection`/`PipeConnection` mismatches in processing
workers and POSIX-only `os.WNOHANG` references in tests. Resolve or correctly platform-scope these checks before Windows
qualification; passing a Linux-target type check does not establish Windows support.

P0 Go/No-Go evidence: UI without Python/Server, authenticated API read/write, credentials, signed standard-user package,
WebView2 bootstrap, independent service/login, installed notification cold activation. D4/D5 full bootstrap can remain
gated at P3; P0 records producer commitments and limits preview to connect-only. Native blockers must be solved or the
platform/scope reconsidered. Electron fallback addresses demonstrated shell/WebView/maintenance blockers, not installer gaps.

Maintain English/Chinese UI and docs, keyboard navigation, focus, screen-reader labels, IME-safe forms, high contrast,
non-color-only status, and usable confirmation/recovery at 800 × 600 and 200% zoom. Locale/theme changes retain identity.

Measure cold start, idle CPU/wakeups, desktop+WebView+Server memory, full installation/download size and list/search
latency. P0 records hardware, OS/WebView versions, datasets, repeat count and p50/p95; fixes numeric release budgets before
P2 expansion. Include empty/multi-page, model-free/configured cases. D7 owns the published budget; there is no current
performance claim. Transport/notification operating caps do not replace measurements.

## Delivery phases

| Phase | Deliverable | Exit requirement |
| --- | --- | --- |
| P0: architecture | Bundled client, narrow transport, credentials, installed Windows spike, UI reuse and measurements | D7 owners/host; security evidence; D1/D2 assigned; D3–D6 limits recorded |
| P1: connect-only preview | Existing local/remote connection, tested compatibility, service status, explicit Memory store/recall, Agent diagnostics | Qualified operations/identity; no unimplemented install claim |
| P2: management/access | Scope/assets/Sources, typed Review, exact shared resources/reports, import, covered Review notifications, diagnostics | D2 full Memory browsing; authorization/concurrency/family contracts |
| P3: managed installation | Clean-machine install, chosen host, service/config changes, migration/update/recovery/removal | D3/D4/D5, signed immutable artifacts and ownership acceptance |
| P4: delivery | Durable inbox, target association, resume, exact navigation and supported receiver actions | D6, named sender/receiver, bounded consumer and installed activation |
| P5: first qualified release | Complete #1428 journey on Windows 11 x64 | All applicable AC, compatibility/support matrix, published budgets |
| P6: more platforms | Qualified macOS/Linux packages | Repeat installed acceptance per advertised environment |

P3/P4 progress independently when dependencies exist; P2 authorization does not wait for delivery. Reuse existing
tracking issues. Keep producer contracts and consumers in focused PRs. A blocked mandatory AC cannot be marked
inapplicable to close #1428: full closure needs managed local setup, authorized remote use, durable Handoff delivery,
Review/Handoff notifications, recovery, accessibility and data-preserving removal on one platform.

## Acceptance and verification

Owners: Desktop owns packaged UI/native behavior; Server public semantics; Install installer/service/configuration/
distribution; Delivery D6; Release signed-platform qualification. These are responsibilities, not named staffing;
D7 binds maintainers before P0 exit. Each record identifies versions, environment, fixture, result and owner; one smoke
test cannot stand for every scenario in a row.

| ID | Phase / owner | Required observable behavior | Evidence entry |
| --- | --- | --- | --- |
| AC-01 | P3/P5 · Install + Desktop | Clean machine: verified runtime/host, model-free Memory store/fts recall | Installed first use |
| AC-02 | P1/P3 · Install | Stale/foreign/occupied states distinguished, unknown ownership preserved | Service JSON/native lifecycle |
| AC-03 | P3/P5 · Desktop + Install | Close/Quit/restart/login preserves independent service/work and chosen startup | Installed lifecycle/recovery |
| AC-04 | P3/P5 · Install + Release | Interrupted update/signature/readiness failure has durable component status and compatible recovery | Installer fault/restart |
| AC-05 | P1/P2 · Server + Desktop | Missing/old/unknown handshake, missing feature, changed identity: no guessed support/retarget | D1 connection fixtures |
| AC-06 | P1 · Desktop | Loopback/base path/plaintext/TLS/redirect/proxy limits are accurate, no credential forwarding | Shared vectors/native transport |
| AC-07 | P1/P2 · Desktop | Profile/endpoint/token/Principal changes isolate responses, cursors, drafts and mutation targets | Concurrent packaged interactions |
| AC-08 | P2 · Server + Desktop | Exact Handoff grant works without Scope listing; latest/adjacent/broad/evidence leakage denied | Access plus desktop journey |
| AC-09 | P2 · Server + Desktop | Filter before paging/counts, no unsafe fallback, Review/publication authorized | Access/mutation contracts |
| AC-10 | P2 · Server + Desktop | Stale version/citation, duplicate submit, lost response: no silent approval/replay | Mutation recovery scenarios |
| AC-11 | P4 · Delivery + Desktop | Offline arrival, cursor expiry, revocation/cancellation recover exact authorized inbox | D6 and receiver pair |
| AC-12 | P0/P4 · Desktop + Release | Installed hints, permission denial, bursts, Quit and stale activation safely navigate/fall back | Native/cold activation |
| AC-13 | P2 · Server + Desktop | Same/renamed/changed text, BOM/newlines, invalid/oversize and ambiguous import follow identity/limits | Import/handle fixtures |
| AC-14 | P0/P2 · Desktop | Malicious content, fake generation/window/path/link cannot execute, leak secrets or mutate unexpectedly | Packaged capability/CSP |
| AC-15 | P0/P5 · Desktop + Release | Secret canaries absent from logs, URLs, notifications, exports, renderer storage and telemetry | Output inspection |
| AC-16 | P3/P5 · Install | Removal preserves data, user edits and referenced independent consumers | Installed removal |
| AC-17 | P2/P5 · Desktop | Both locales, keyboard/IME/reader/contrast/small window/200% zoom complete supported actions | Accessibility journey |
| AC-18 | P0/P5 · Release | Exact signed artifacts measured on reference machine meet published budget at P5 | Benchmark/support record |
| AC-19 | P2 · Server + Desktop | Large/changing Memory fully traversable under D2 or honestly limited before D2 | Large-Scope fixtures |
| AC-20 | P2 · Server + Desktop | Profile Review, Topic Memory and unknown family retain typed/read-only boundaries | Family/Review fixtures |
| AC-21 | P1/P3 · Server + Install + Desktop | Static/injected Provider, generic 401/503, partial rotation have true identity/error/recovery | Auth/configuration journey |
| AC-22 | P2 · Desktop | Backlog, multi-page, changed/partial coverage obey budgets without fabricated counts/history | Polling behavior |
| AC-23 | P3 · Install + Release | Updater exit and stable/preview sharing preserve producer recovery and single management owner | Packaged update/channel |
| AC-24 | P0/P2 · Desktop | No Python/Server: setup works; shared assets do not drift; management uses public API only | Desktop build/Dashboard regression |
| AC-25 | P3 · Server + Install | Maintenance migration resumes same ID, verifies before traffic, no incompatible rollback | Migration/install recovery |
| AC-26 | P4 · Delivery + Desktop | Multiple devices/targets and unenrolled Agent do not impersonate or convert hints into acceptance | Target association |

Implementation PRs run `make check` and relevant behavior tests; changed contracts additionally run `make api-generate`
and `make contract-test`. Reuse Server/access/transport/migration/native service tests. Shared UI needs Dashboard
regression plus desktop behavior; packaging needs real installed tests. Docs run `make docs-test` (Fumadocs), verify
titles/navigation/links and matching bilingual phase/dependency/AC IDs. A mock or docs build cannot qualify native behavior.

## Implementation tuning and measurement

Select and record values during implementation; the RFC does not prescribe initial operating defaults. Distinguish
existing Server limits from client choices, and keep observable guarantees unchanged when tuning.

| Choice | Evidence required before enabling the feature |
| --- | --- |
| Polling interval, jitter, concurrency and request rate | Attention latency versus idle CPU, wakeups and Server load; limits also hold during manual retry |
| Candidate page size and traversal expiry/recovery | Respect API limits; empty, multi-page, large backlog and concurrently changing lists; later pages progress or coverage is explicitly limited |
| Failure backoff | Disconnect/reconnect, authentication rejection and Server retry delay behavior |
| Notification metadata capacity and retention | Bounded storage, deduplication, safe expiry/eviction and cold activation without changing Server read/delivery state |
| Transport timeouts and decoded response/export limits | Slow/large responses, cancellation and streaming; operation-specific recovery rather than blind mutation replay |
| Import file/content limits | Bounded reads and Unicode handling; explain limits before confirmation and reject oversize content without truncation |

Record the selected values, reference environment, datasets and measurements in the implementation/release record.
Use behavior and regression tests for incomplete counts, changed coverage, backlog progress and private notifications;
do not freeze a polling interval or internal call count unless it is itself a published external budget.

## Coordination decisions

These questions have proposed defaults and explicit gates. Missing dependencies remain delivery prerequisites, not
claims of implemented functionality or a reason to withhold this design from review.

| Practical question | Proposed default | Decision gate |
| --- | --- | --- |
| Which OS first, and who maintains/releases it? | Windows 11 x64 + SQLite; name Desktop/Install/Server/Release owners and one Agent Host/version | Platform at RFC acceptance; staffing/host before P0 exit |
| How much UI do we share? | Assets/conventions/translations/components; independent client management entry, no mandatory Dashboard rewrite | RFC acceptance |
| Can we ship before installation/delivery are ready? | Connect-only preview, then management; independent P3/P4; #1428 remains open until complete | RFC acceptance |
| Which remote deployments work initially? | Direct HTTPS, operator-issued Bearer; no proxy/SSO/plaintext opt-in | RFC acceptance; expand only through qualified adapters |
| Which Server versions work, and how do restore/clone affect identity? | D1 explicit versions/lifecycle; qualify 1.0.0 as legacy candidate | P0 contract agreement before dependent controls |
| Who supplies installation/delivery and when? | D3–D6 producers own schemas/recovery; no private desktop replacement | Before P3/P4 commitments |
| How fast and small must the complete product be? | Measure named hardware, publish numeric budgets including Python/WebView | P0 exit before P2 expansion |
