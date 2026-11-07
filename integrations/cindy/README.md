# Cindy integration

`experimental`

Connect [Cindy 0.1.115](https://github.com/makecindy/cindy/releases/tag/v0.1.115) to PowerContext
using the `powercontext-hooks` Ghost for scoped recall and optional automatic recall and capture.
A separately configured native MCP connection supplies the full Server tool catalog.

See the [English guide](../../docs/en/docs/integrations/cindy.md) or
[中文指南](../../docs/zh/docs/integrations/cindy.md) for connection settings, Scope selection, and hook behavior.

## Build the hook archive

```bash
uv sync --locked
pnpm --dir integrations/dsh/plugins/powercontext install --frozen-lockfile --config.auto-install-peers=false
pnpm --dir integrations/dsh/plugins/powercontext exec tsdown --config ../../../cindy/tsdown.config.ts
uv run python integrations/cindy/scripts/package.py
```

Import `dist/powercontext-cindy-hooks-0.1.0.cindy` into Cindy. The archive is standalone;
users do not need DSH or additional runtime packages.

The worker bundles the shared HTTP client, PreparedContext validation, and credential checks from the DSH
integration at build time. Plugin `resolve_scope`, `prepare_context`, and `search_memory` tools use the same
connection and Scope resolver as hooks, including when automatic memory is off. The official Cindy Node broker
owns worker startup and RPC forwarding; no additional bridge service is deployed.

## PowerContext behavior compared with DSH and Pi

| Contract | Cindy behavior |
| --- | --- |
| Scope ownership | Resolve on the Server, using an explicit ID or trusted Cindy session binding; no default fallback. DSH/Pi can also use workspace bindings and Server defaults. |
| Recall | Use the shared HTTP operation and PreparedContext validator; retain the Server's exact bounded content. |
| Injection | Cindy rewrites the visible, persisted user message; DSH/Pi have separate context carriers. Explicit plugin recall returns tool results. |
| Capture | Same Scope/session/event/original-text hash construction as DSH/Pi, with a Cindy prefix. Never capture this adapter's recalled history. |
| Acceptance | Source acceptance and position are distinct from processing completion and Memory production. Cindy does not flush within the three-second hook window. |
| Failures | Allow the original message on automatic failures; report uncertain capture without retrying it. Explicit reads return errors. |
| Native MCP | Separate configuration; unlike DSH's pre-tool guard, the Ghost cannot enforce Scope arguments on native MCP calls. |

For isolated evaluation, disable Cindy shared memory and the selected harness's native memory in Cindy settings,
then start a new session. Plugin settings do not change or delete built-in memory.

## Run behavior checks

```bash
node --test integrations/cindy/tests/*.test.cjs
uv run pytest tests/e2e/test_mcp_transport.py tests/e2e/test_mcp_full_profile.py -q
```

For Cindy provider and hook gateway acceptance:

```bash
git clone --depth 1 --branch v0.1.115 https://github.com/makecindy/cindy.git /tmp/cindy-validation
POWERCONTEXT_CINDY_SOURCE=/tmp/cindy-validation \
  uv run pytest tests/e2e/test_cindy_mcp_chain.py tests/e2e/test_cindy_hooks_chain.py -q
```

These tests use Cindy's provider and subscription gateway source with a real authenticated TCP Server.
They cover MCP discovery, Scope isolation, Memory readback, prompt capture, and rewrite verdicts.
The packaged worker runs with access limited to its installation directory to verify standalone operation.
The provider transport test covers the direct compatibility path, not Desktop's default MCP Apps facade.

## Run live Codex acceptance

Cindy has no standalone agent CLI in the pinned source. This opt-in check uses its upstream `maker-core`
`AppServerHost` and stdio transport to run an authenticated local Codex binary. It uses the existing Codex login
without copying credentials, disables native memory for the spawned process, and sends only synthetic test data.
An optional `POWERCONTEXT_CINDY_CODEX_MODEL` selects a model available to that login; otherwise Codex uses its default.

```bash
POWERCONTEXT_CINDY_SOURCE=/tmp/cindy-validation \
POWERCONTEXT_CINDY_CODEX_BINARY="$(command -v codex)" \
  uv run pytest tests/e2e/test_cindy_hooks_chain.py -k native-codex -q
```

The check verifies answers from automatic context and a model-initiated plugin recall call in independent
Scopes, then reads the original captured prompts from the real authenticated Server. Explicit reads with
automatic memory disabled must not add Sources. A `cindy-acceptance.json` receipt is written in the pytest case's
temporary directory. No live-model check runs in ordinary CI without the explicit binary setting.

Host credential/message bridges and root plugin-instance identity are simulated. This verifies the upstream
Codex runtime and packaged worker, not Electron installation, secret-store UI, or complete Desktop harness assembly.
