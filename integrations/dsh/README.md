# DeepSeek Harness integration

`plugins/powercontext` contains the PowerContext plugin for DeepSeek Harness.

Install the PowerContext tool first, then configure the plugin from the same Git ref:

```bash
uv tool install "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
powercontext setup dsh --source oceanbase/powercontext --ref master
powercontext server run
```

A local checkout works the same way. The plugin directory must contain a built `lib/index.js`:

```bash
powercontext setup dsh --source .
```

The plugin is a client of the running Server:

- before each model step it asks the Runtime for one bounded context value and captures the current prompt as independent Source evidence;
- named `pc_*` tools call the public `/v1/...` HTTP API;
- Server or transport failures do not block normal DeepSeek Harness work.

Automatic recall calls `POST /v1/context/prepare` once per turn. Explicit Memory writes use `remember_memory` and do not need a model. Prompt capture can be disabled with `POWERCONTEXT_DSH_CAPTURE_PROMPTS=false`.

For a Server using optional local bearer authentication, set `POWERCONTEXT_DSH_AUTHORIZATION` to the complete `Bearer ` header before starting `dsh web`.

Run the model-free call-through checks from a repository checkout:

```bash
make js-api-generate-check
make js-test
uv run python -m pytest tests/e2e/test_dsh_http_chain.py tests/test_js_operations.py tests/test_system_cli.py tests/test_dsh_cli.py -k dsh
```
