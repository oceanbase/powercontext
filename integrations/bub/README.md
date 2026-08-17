# PowerContext for Bub

This package connects Bub to a running PowerContext Server through the public Python client. It adds three tools:

- `powercontext.remember` stores one durable decision, preference, constraint, or procedure.
- `powercontext.search` searches durable memory.
- `powercontext.context` prepares bounded context for a question.

Before each model call, the plugin also prepares relevant context and adds it as host-supplied historical evidence.
Automatic trajectory capture is opt-in. When enabled, the plugin captures the initial task and completed LLM and tool
events as bounded Content Sources. It periodically flushes those Sources through the Memory pipeline so later model
steps in the same Bub run can recall earlier findings. Provider-hidden reasoning is never available to the hook and is
not captured.

## Configuration

The plugin uses Bub's Pydantic settings extension. Configuration can live in the `powercontext` section of Bub's
configuration file:

```yaml
powercontext:
  base_url: http://127.0.0.1:8000
  scope_id: project:example
  capture_events: true
  capture_checkpoint_every: 5
```

Environment variables use the `POWERCONTEXT_BUB_` prefix and take precedence over file values. Values are parsed and
validated by Pydantic before the plugin starts.

| Variable | Default | Purpose |
| --- | --- | --- |
| `POWERCONTEXT_BUB_BASE_URL` | `http://127.0.0.1:8000` | PowerContext Server URL |
| `POWERCONTEXT_BUB_SCOPE_ID` | workspace-derived | Durable scope shared by Bub sessions |
| `POWERCONTEXT_BUB_TIMEOUT` | `10` | Client timeout in seconds |
| `POWERCONTEXT_BUB_MAX_BYTES` | `8000` | Maximum prepared-context size |
| `POWERCONTEXT_BUB_CAPTURE_EVENTS` | `false` | Capture completed Bub events as Content Sources |
| `POWERCONTEXT_BUB_CAPTURE_CHECKPOINT_EVERY` | `5` | Flush Memory after this many captured events |
| `POWERCONTEXT_BUB_CAPTURE_MAX_BYTES` | `8192` | Maximum UTF-8 bytes stored for one captured event |
| `POWERCONTEXT_BUB_CAPTURE_LOG` | unset | Optional JSONL evidence path; records metadata but not event content |

Captured tool arguments redact values under credential-like keys. Known credential environment values are also
removed from serialized event content. Keep the PowerContext scope and optional capture log protected because normal
tool output can still contain sensitive project data.

Install the package together with PowerContext and Bub:

```bash
uv pip install -e . -e integrations/bub
```
