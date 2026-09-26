# Native boundary

## Renderer

Only the packaged main window receives the explicit `main` capability. The app manifest enumerates every typed connection, Scope and diagnostic command, so Tauri does not implicitly grant custom commands to every window. The handler also checks the native window label. No capability grants a remote origin, general HTTP, shell, filesystem, SQL, notification, opener or credential-read access.

Production CSP allows packaged scripts/styles/images and Tauri IPC only, with no remote frames, forms or network fetch. The navigation callback permits the packaged origin, and permits the exact Vite origin only in debug builds. Production builds use Tauri's `custom-protocol` feature. No CSP bypass, unsafe eval, remote image or HTML execution is enabled. React renders text, never `dangerouslySetInnerHTML`.

## Secrets and transport

The vault namespace is `com.powercontext.desktop.preview`. A native credential ID is an opaque bounded identifier; it is not an endpoint or a user-controlled vault path. Windows credentials use the OS store only. Unavailable storage returns a stable error; session-only storage is an explicit separate choice. Secret values have zeroizing storage and cannot be serialized or debug formatted. The renderer has no secret readback operation. The synthetic opt-in probe only touches its unique test entry.

The Rust-derived write protocol accepts only `secret` and an explicit `storage` choice (`persistent` or `session_only`). Its input cannot be serialized; the success receipt contains only the storage choice. Missing modes, unknown modes and extra fields are rejected. The native caller supplies the credential ID; the renderer cannot choose a vault address. A failed persistent write produces no success receipt or automatic fallback.

Native code creates credential IDs and binds them to an exact endpoint/trust configuration. Editing the address or trust invalidates the active context and prevents retaining its old credential. Profile storage is atomic and secret-free, with a bounded journal for deferred cleanup of owned vault entries.

Native reqwest uses Windows system trust through native-tls/Schannel, plus an optional connection-local PEM CA (maximum 64 KiB). Hostname/certificate validation remains enabled. Automatic proxy inheritance and redirects are disabled. HTTP requires loopback. Userinfo, query, fragment, control characters, backslashes and dot segments are rejected before URL normalization. UTF-8 and percent-encoded base paths are accepted only after rejecting encoded separators, dot segments, control characters and ambiguous double encoding. Public operation paths come from the generated OpenAPI manifest and preserve the base prefix.

Typed adapters separate liveness, readiness, identity and capabilities. Native context generations cancel old reads on connection, identity and Scope changes; a separate query generation cancels superseded Scope searches. Business operations require explicit qualified compatibility and actual Server authorization. The real-Server fixture exercises native memory adapters; product Memory commands remain unavailable until immutable write context and unknown-result handling are implemented. No automatic write retry or persistent queue exists.

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

Error responses are enum codes. Transport exception text, endpoint URLs, bodies and credentials are never returned or logged. No product logging, telemetry, crash uploader or persistent business cache is configured. The app does not read databases, start a Server or inspect process environments. Explicit local diagnostics use a pinned absolute executable, digest and fixed version check, then one of two native-defined commands. Each invocation has a combined 256 KiB output budget and a deadline. Windows helpers enter an owned kill-on-close Job before resuming; raw output is projected to allowlisted status fields and discarded. A local CLI pin does not authenticate its publisher or freeze its Python dependencies; the configured installation must be trusted.

Tests cover actual TLS fixtures and adversarial HTTP responses, shared loopback policy, mocked Tauri permission resolution with the real capability configuration, navigation rejection, unavailable vault behavior and the explicit session path. The opt-in Windows vault probe separately verifies persistence across processes. See the [validation guide](VALIDATION.md) for packaged checks, CI artifacts and remaining platform qualification.

Framework references: [Tauri capabilities](https://v2.tauri.app/security/capabilities/), [Windows installer options](https://v2.tauri.app/distribute/windows-installer/).
