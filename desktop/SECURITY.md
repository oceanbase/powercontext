# Native boundary

## Renderer

Only the packaged main window receives the explicit `main` capability. The app manifest enumerates every typed connection, Scope and diagnostic command, so Tauri does not implicitly grant custom commands to every window. The handler also checks the native window label. No capability grants a remote origin, general HTTP, shell, filesystem, SQL, notification, opener or credential-read access.

Production CSP allows packaged scripts/styles/images and Tauri IPC only, with no remote frames, forms or network fetch. The navigation callback permits the packaged origin, and permits the exact Vite origin only in debug builds. Production builds use Tauri's `custom-protocol` feature. No CSP bypass, unsafe eval, remote image or HTML execution is enabled. React renders text, never `dangerouslySetInnerHTML`.

## Secrets and transport

The vault namespace is `com.powercontext.desktop.preview`. A native credential ID is an opaque bounded identifier; it is not an endpoint or a user-controlled vault path. Windows credentials use Credential Manager; macOS credentials use Keychain. Unavailable storage returns a stable error; session-only storage is an explicit separate choice. Secret values have zeroizing storage and cannot be serialized or debug formatted. The renderer has no secret readback operation. The synthetic opt-in probe only touches its unique test entry.

The Rust-derived write protocol accepts only `secret` and an explicit `storage` choice (`persistent` or `session_only`). Its input cannot be serialized; the success receipt contains only the storage choice. Missing modes, unknown modes and extra fields are rejected. The native caller supplies the credential ID; the renderer cannot choose a vault address. A failed persistent write produces no success receipt or automatic fallback.

Native code creates credential IDs and binds them to an exact endpoint/trust configuration. Editing the address or trust invalidates the active context and prevents retaining its old credential. Profile storage is atomic and secret-free, with a bounded journal for deferred cleanup of owned vault entries.

Native reqwest uses Windows system trust through native-tls/Schannel, plus an optional connection-local PEM CA (maximum 64 KiB). Hostname/certificate validation remains enabled. Automatic proxy inheritance and redirects are disabled. HTTP requires loopback. Userinfo, query, fragment, control characters, backslashes and dot segments are rejected before URL normalization. UTF-8 and percent-encoded base paths are accepted only after rejecting encoded separators, dot segments, control characters and ambiguous double encoding. Public operation paths come from the generated OpenAPI manifest and preserve the base prefix.

Typed adapters separate liveness, readiness, identity and capabilities. Native context generations cancel old reads on connection, identity and Scope changes; a separate query generation cancels superseded Scope searches. Business operations require explicit qualified compatibility and actual Server authorization. Scope and Memory reads validate both deployment and Principal before dispatch and after the response. Observed identity loss clears the active context. Dispatched writes keep immutable endpoint/deployment/Principal/Scope metadata; failed post-write verification makes successful HTTP outcomes unknown without returning private content or automatically replaying. Late writes cannot clear a newer context. Qualified legacy Servers retain a null deployment identity and require discovery to remain 404. Identity observations do not supply an atomic Server-side conditional write fence or binary attestation. No automatic write retry or persistent queue exists.

## Budgets and error projection

| Boundary | Limit |
| --- | --- |
| Endpoint input | 2048 bytes |
| Bearer input | 1–2048 printable ASCII bytes |
| Additional CA PEM | 64 KiB |
| Connect timeout | 5 seconds |
| Complete request, including response body | 15 seconds |
| Response body | 1 MiB, checked both by declared size and streamed bytes |
| Concurrent reads per client | 4; excess fails as busy |
| Session observations | 512 terminal records shared across HTTP requests and native Memory/Scope actions; memory only |

Session observation commands require the same trusted main-window boundary and work independently of connection state. Rust projects only fixed operation/unit/outcome enums, elapsed milliseconds, UTC timestamps, counts, HTTP status and existing safe error codes. It never serializes connection or write state for export; content, queries, credentials, endpoints, private paths and target identities never enter the collector. The preview serializes only that projection. Retention/reset and process-lifetime loss metadata expose incomplete coverage; nonblocking recording cannot change business results or suppress cancellation. HTTP response success and native write certainty are separate measurements. Installer/Ops and local diagnostic output are outside collection. The [session observation contract](../docs/en/development/desktop-session-observations.md) defines the complete field allowlist and coverage.

Error responses are enum codes. Transport exception text, endpoint URLs, bodies and credentials are never returned or logged. No product logging, telemetry, crash uploader or persistent business cache is configured. The app does not read databases or inspect process environments. Authorized local service changes use the independent Ops boundary described below. Explicit local diagnostics verify a pinned absolute executable and digest, then run one of two native-defined diagnostic commands. Registration requires no version, and legacy version fields are ignored. Diagnostic responses must match the supported output schema. A subsequent fixed `--version` query collects bounded display metadata; failure, unsupported output or an unavailable version does not reject an otherwise valid diagnostic result. Each invocation has a combined 256 KiB output budget and a deadline. Windows helpers enter an owned kill-on-close Job before resuming. macOS runs a read-only snapshot of the digest-verified launcher in a private process group, which is terminated after completion, cancellation or timeout; raw output is projected to allowlisted status fields and discarded. Normal application exit closes diagnostic admission and waits for active worker process cleanup before Tauri exits. A local CLI pin does not authenticate its publisher or freeze its Python dependencies; the configured installation must be trusted.

Tests cover actual TLS fixtures and adversarial HTTP responses, shared loopback policy, mocked Tauri permission resolution with the real capability configuration, navigation rejection, unavailable vault behavior and the explicit session path. The opt-in Windows vault probe separately verifies persistence across processes. See the [validation guide](VALIDATION.md) for packaged checks, CI artifacts and remaining platform qualification.

Framework references: [Tauri capabilities](https://v2.tauri.app/security/capabilities/), [Windows installer options](https://v2.tauri.app/distribute/windows-installer/).

## Independent Ops boundary

Only the packaged main window may use the seven fixed Ops commands and the retained-reference reader. Inputs are typed
operation/options or native-issued approval/reference IDs; executable, binding paths, URLs, service names, and complete
plans cannot cross renderer IPC. The main window receives validated progress through `ops-progress`; no global
renderer event-emission permission is granted. Preflight retains the exact plan for ten minutes and apply/resume consume
the approval once against the same distribution and target binding.

Packaged `ops-provision.json` pins the independent bundle manifest and optional managed catalog. Every invocation
verifies the complete bundle inventory and contents, including safe runtime-local file aliases. Missing provision is
unavailable; a candidate manifest cannot authorize itself and system/Server Python is never a fallback. Immutable
digest-named private binding copies prevent an in-flight helper from observing a subsequently selected binding.
The fixed stable root is current-user `PowerContext/Ops` under local application data, independent of Desktop removal.
Native code writes private files atomically (0600/0700 on POSIX; current-user protected ACLs on Windows), rejects path
links/reparse points, and validates ownership/protection on existing control files. This same-user bridge is not a
privilege boundary against another process running as that user.

The native reference file stores operation IDs, binding fingerprints and the approved action/options before dispatch. It stores no outcome, raw plan, endpoint, path or credential. Legacy references without action metadata remain queryable but cannot authorize resume. Resume must match the retained action/options. Progress is correlated to the native approval ID as well as operation/attempt. Ops journals and locks
remain the sole recovery authority. Four ordinary and two reserved status/cancel helpers bound native concurrency;
timed-out children retain their slot until exit. Each invocation bounds JSONL messages to 64 KiB, aggregate output to
1 MiB and 128 messages, with 30-second passive-read, 180-second preflight and 30-minute mutation deadlines. Stderr is discarded. Invalid schema,
identity, sequence or enums never reach the renderer; valid structured nonzero exits preserve unhealthy outcomes.
Nonblocking drains finish at helper exit even when a descendant holds a pipe open.

Timeout/lost response/Quit retain the operation reference and report unknown. Normal Quit closes admission and waits
at most two seconds without signalling Ops, Server or manager processes. Pipe closure can produce a durable safe-boundary
progress failure; accepted work may continue. Cancellation calls Ops and does not imply rollback. Recovery reads the
existing operation, then requires a newly approved plan; no automatic replay or parallel outcome journal exists.

See [Local Server Ops](../docs/en/docs/operate/local-server-ops.md#desktop-native-bridge) for the exact optional Tauri
resource configuration, producer pins, stable paths and recovery API. The bridge does not ship production resources or
an installation wizard. Real service mutations, signing/notarization and clean-machine acceptance require separate
platform evidence.

## Explicit local connection handoff

`connect_local_server` accepts only the current context generation. It obtains the endpoint through trusted native Ops, requires an owned/current registration, active process, valid configuration, present runtime, responding service and installation identity. It matches the observed resource ID/digest to the pinned catalog and selects only its named bundled Desktop qualification for the current HTTP contract. Known qualification IDs also require the catalog source commit and `wheel-sha256:` reference to match the record's original Server source and qualification wheel; a matching API contract cannot reinterpret historical evidence. Candidate/unrecognized qualifications fail closed. The discovery package version, Principal, basic Memory operation set and exact public default Scope must pass before a local activation is published. Pending or failed qualification/Scope reads expose no active local context and cannot replace a newer context. Existing Bearer/CA profiles are not overwritten. Page opening and application reopen never invoke this command.

Submitted input is retained in window-local application state before native IPC, independently of the form lifetime. Automatic disconnection, rejected writes and pre-dispatch retry failure do not discard it; each attempt has its own identity. Successful receipts clear only their own input, confirmed navigation clears all retained copies, and late receipts cannot revive explicitly discarded copies. Recovery never persists or automatically replays content. Local handoff completion preserves newer navigation and editor intent.
