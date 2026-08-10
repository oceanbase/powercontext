# PowerContext for Bub

This package connects Bub to a running PowerContext Server through the public Python client. It adds three tools:

- `powercontext.remember` stores one durable decision, preference, constraint, or procedure.
- `powercontext.search` searches durable memory.
- `powercontext.context` prepares bounded context for a question.

Before each model call, the plugin also prepares relevant context and adds it as host-supplied historical evidence.
The plugin does not persist Bub conversation history. A new Bub session can observe an earlier session only through the
configured PowerContext scope.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `POWERCONTEXT_BUB_BASE_URL` | `http://127.0.0.1:8000` | PowerContext Server URL |
| `POWERCONTEXT_BUB_SCOPE_ID` | workspace-derived | Durable scope shared by Bub sessions |
| `POWERCONTEXT_BUB_TIMEOUT` | `10` | Client timeout in seconds |
| `POWERCONTEXT_BUB_MAX_BYTES` | `8000` | Maximum prepared-context size |

Install the package together with PowerContext and Bub:

```bash
uv pip install -e . -e integrations/bub
```
