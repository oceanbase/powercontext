# PowerContext Hermes Memory Provider

This plugin implements Hermes' `MemoryProvider` interface using the PowerContext
HTTP API. See [`integrations/hermes/README.md`](../../README.md) for setup,
configuration, scope isolation, and runtime behavior.

Requires Hermes Agent v0.20.4 or newer.

The plugin deliberately uses only the Python standard library for HTTP, so it
can be copied into Hermes without adding an HTTP client dependency. Its
provider configuration is read from `$HERMES_HOME/powercontext/config.json`.

To install or refresh the provider from a matching PowerContext release tag:

```bash
powercontext setup hermes --source oceanbase/powercontext --ref v0.0.2
powercontext doctor hermes
```

The setup command copies this directory to `$HERMES_HOME/plugins/powercontext`.
It requires the Hermes CLI to be installed and available on `PATH`.

The provider participates in Hermes' generic memory setup wizard. Run the
command below and select `PowerContext` from the provider list:

```bash
hermes memory setup
```

On Hermes v0.20.4, `hermes memory setup powercontext` only selects the provider
and does not open the provider configuration wizard.

Non-sensitive values are saved to `powercontext/config.json`; authorization is saved
through Hermes' `.env` secret store.

Automatic Source-to-Memory extraction is available only when the PowerContext
server reports `memory_extraction: true`. Configure a server generation model
with `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL` and its provider
credentials, then restart the server. Source capture and retrieval continue to
work when extraction is disabled.

Pre-compression capture is opt-in. Set `capture_pre_compress: true` in the
provider configuration, or set
`POWERCONTEXT_HERMES_CAPTURE_PRE_COMPRESS=1`. When enabled, only new user and
assistant turns are captured; system/tool messages are excluded and detected
secrets are redacted before sending them to PowerContext.

## CLI commands

After enabling the provider and restarting Hermes so it discovers the command
tree, use:

```bash
hermes powercontext --help
hermes powercontext status
hermes powercontext search "Python project management"
hermes powercontext remember preference "The user prefers uv"
hermes powercontext flush
```

Use `--scope-id` to inspect a specific scope:

```bash
hermes powercontext search "deployment decision" --scope-id hermes-smoke-test
```
