# DeepSeek Harness integration

`community`

`plugins/powercontext` contains the PowerContext plugin for DeepSeek Harness.

Install the PowerContext tool first, then configure the plugin from the same Git ref:

```bash
uv tool install --force "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext setup dsh --source oceanbase/powercontext --ref master
powercontext server run
```

A local checkout works the same way. The plugin directory must contain a built `lib/index.js`:

```bash
powercontext setup dsh --source .
```

The plugin is a client of the running Server:

- before each model step it asks the Runtime for one bounded context value and captures the current prompt as independent Source evidence;
- DSH's native MCP client exposes model-facing operations as `mcp__powercontext__<operation>`;
- automatic lifecycle hooks and `/pc` commands use the bounded HTTP client for capture, control and diagnostics.

The plugin does not register direct HTTP operation tools. Scope or transport failures leave ordinary conversation
running; optional MCP initialization waits at most five seconds before continuing in the background.

Automatic recall calls `POST /v1/context/prepare` once per turn. Explicit Memory writes use `remember_memory` and do not need a model. Prompt capture can be disabled with `POWERCONTEXT_DSH_CAPTURE_PROMPTS=false`.

For a Server using optional local bearer authentication, set `POWERCONTEXT_DSH_AUTHORIZATION` to the complete `Bearer ` header before starting `dsh web`.

Run the model-free call-through checks from a repository checkout:

```bash
make js-api-generate-check
make js-test
uv run python -m pytest tests/e2e/test_dsh_http_chain.py tests/test_js_operations.py tests/test_system_cli.py tests/test_dsh_cli.py -k dsh
```
