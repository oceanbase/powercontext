# DSH runtime acceptance

The [2026-09-04 acceptance record](acceptance/2026-09-04.md) includes build identity, automated results,
actual Web checks, a snapshot screenshot, and the outstanding live-model authentication blocker.

Run from the PowerContext checkout after `uv sync --frozen` and building the plugin:

```bash
pnpm --dir integrations/dsh/plugins/powercontext build
make dsh-runtime-test
```

Without GNU make:

```bash
pnpm --dir integrations/dsh/plugins/powercontext/tests/runtime install --frozen-lockfile
pnpm --dir integrations/dsh/plugins/powercontext test:e2e:runtime
```

The test package pins `@deepseek-ai/dsh-sdk-client@0.1.2-rc.1` and the resolved DSH host/dependency graph in its own
lockfile. Use Node 22.19+ (CI: 22.19.0; also tested on Windows with 24.14.1). Install peers in this isolated package;
the plugin's ordinary unit-test installation deliberately does not install its optional host peers.

`../config-runtime` separately pins `@deepseek-ai/dsh-app-boot@0.2.0-rc.2` for native configuration/compatibility
checks; install it with `pnpm --dir integrations/dsh/plugins/powercontext/tests/config-runtime install --frozen-lockfile`.
Its own dependency graph keeps the 0.2 API peers separate from the 0.1.2 SDK host.
`tests/test_dsh_transport.py` loads those real APIs through a synthetic carrier in disposable profiles; those
checks do not execute a 0.2 CLI installation or a Desktop host. They verify omitted incompatible third-party
layers, rejected incompatible PowerContext bundles, and explicit version exemptions. The SDK continues to use
its matched 0.1.2 runtime. Setup reads the selected installation's own configuration APIs, not this test copy.

Run the native configuration regressions without installing a DSH CLI:

```bash
pnpm --dir integrations/dsh/plugins/powercontext/tests/config-runtime install --frozen-lockfile
uv run pytest tests/test_dsh_transport.py --require-dsh-config-runtime
```

The Python 3.11–3.14 CI matrix installs this locked configuration package, resolves `DSH_TEST_CONFIG_BOOT`,
and requires it while running the unit suite. Missing APIs fail instead of silently skipping their tests;
tests that need the DSH CLI can still skip when that host is absent.
Native configuration regressions cover all supported group forms (`group: true`,
`@deepseek-ai/cordis-plugin-group`, and `cordis:group`) through setup and installed readback,
including duplicate instances, nested includes, disabled children and conditional activation.

Run `uv run pytest tests/test_dsh_transport.py --require-dsh-runtime` with `DSH_TEST_EXECUTABLE` selecting the
installed DSH CLI. The `dsh-package` CI job resolves both runtimes before running that command and fails if
either is unavailable.
The file also retains ordinary Python transport-policy tests that require no DSH installation.

These tests launch the real `dsh --profile sdk` subprocess and a real PowerContext Server with isolated homes.
Only the built distributable plugin files are installed. Host tool registration, pre-step processing, message
construction, model request assembly, and session persistence are not replaced. A loopback model fixture provides
scripted OpenAI-compatible streaming replies and Server inference decisions. A loopback proxy can inject HTTP
failures at individual PowerContext endpoints.

The setup scenario first runs `powercontext setup dsh --source <this checkout>` with the pinned DSH executable
and a clean DSH home. It verifies Web-profile registration through `powercontext doctor dsh --json`, then loads
that installed package's distributable files into the SDK profile. The Server and plugin use the same checkout.
A separate setup acceptance installs twice into an existing Web profile with a non-empty, unrelated model patch,
checks registration through the real CLI, and verifies the patch is preserved. Run it independently with
`node --test setup.test.mjs`; it does not require an SDK conversation or external model service.
The native include regression starts a minimal real DSH profile with a recording plugin. It confirms that
named groups and nested relative YAML/JSON includes, with include-local patches, load two endpoint configurations;
public setup then rejects those entries while preserving the saved endpoint and credentials.
Replacing the extra PowerContext entry with UI/model configuration allows setup and a subsequent real host
startup; include files retain their original bytes and the native host evaluates the model expression.
The Python configuration tests also cover installed readback after an injected installation change; that injection is
not a real native installation race.
A test-only loopback adapter invokes the real host command service because the pinned SDK protocol only exposes
prompts. It verifies `/pc doctor` with an environment URL overriding an unusable patch URL, preserves health when
Scope authentication fails, and confirms that Doctor makes no capture or flush requests.

A test observer subscribes to the real Cordis logger's public exporter API and writes only PowerContext diagnostics
to the isolated home. This verifies native logging without replacing the logger. Default host profiles need an
exporter with warning level enabled before these records appear in a terminal.

The scenarios cover:

- automatic Source capture, Server processing into Memory, fresh-session recall, model input, and durable snapshot metadata;
- clean CLI installation, standalone observation limits, and in-host Doctor configuration/route/authentication checks;
- Source idempotency, no duplicate snapshot injection, and matching section/content text;
- Scope business and route failures, authentication failure, unavailable Server, continued conversation, and a real named tool result;
- independent prepare/capture/flush failure, recovery, host restart, and configured Scope isolation.

The registered-entry unit tests in `../automatic-path.spec.ts` cover cancellation, deadlines, writer failures
(including rejected promises), downstream rejection/exception semantics, response validation, and diagnostic
redaction/cooldown. Existing Server e2e tests retain the no-cwd default Scope and direct command/tool checks.

## Real model and Web acceptance

The deterministic model fixture is CI evidence, not a real-model acceptance result. A separate opt-in command uses
a live DeepSeek-compatible endpoint for both the DSH conversation and PowerContext generation:

```bash
# Provide the key through the process environment; never commit it.
export DEEPSEEK_API_KEY=...
export DEEPSEEK_BASE_URL=https://api.deepseek.com
export DSH_REAL_MODEL=deepseek-v4-pro
pnpm --dir integrations/dsh/plugins/powercontext/tests/runtime test:real
```

A missing key is an error, not a skipped passing test. This run can incur model charges. It records no key in the
repository. It verifies generated Memory and a fresh session's snapshot/model reply rather than interpreting an
accepted Source response as successful Memory generation.

For Web acceptance, use a separate DSH home and workspace, install the same built plugin, disable telemetry, and
configure the test model through environment-backed credentials. Test the shipped Web profile through the browser:

1. Submit a stable, non-sensitive project fact; with test-only `flushOnCapture`, verify Source acceptance and subsequent
   Memory generation. Open another session and ask for the fact.
2. In 0.1.2-rc.1 Web, expand the completed turn's process details and **Context injection — powercontext-dsh** row;
   inspect the `PowerContext` snapshot. Compare it with the
   prepare response and the persisted `user/message` event. Reload the page and reopen the historical request.
3. Inject Scope/prepare/capture/flush faults, then restore the endpoint. Confirm conversation continues, logger output
   names the failed stage, existing prepared content survives capture/flush failure, and empty/failure results add no notice.
4. Cancel during a held Scope request. Confirm no prepare/capture/flush follows cancellation. Restart or reload the
   plugin and check that one ordinary prompt still produces one automatic capture and one snapshot at most.
5. Use an unrelated Scope, then exercise `pc_search`, `/pc doctor`, `/pc capabilities`, and bare `/pc`.
   Verify the existing direct-call behavior and absence of cross-Scope content.

Record package versions/commit, OS/Node/Python, model identity, sanitized configuration, per-scenario outcomes,
request/session evidence, and screenshots in the PR. Test the installed older DSH separately through a supported
profile; version 0.1.0-rc.6 has no built-in SDK profile. Missing real-model or Web evidence leaves acceptance incomplete.
