# OpenClaw

Connect [OpenClaw](https://github.com/openclaw/openclaw) to PowerMem through the `memory-powermem` plugin.

## Recommended setup

Install the OpenClaw plugin:

```bash
openclaw plugins install memory-powermem
```

By default, the plugin runs in CLI mode: it invokes `pmem`, stores data under `~/.openclaw/`, and uses the model that OpenClaw already injects. No separate PowerMem server is required for a single-user local setup.

## Manual setup

Use HTTP mode when you want OpenClaw to share a team PowerMem backend:

```bash
powermem-server --host 0.0.0.0 --port 8848
```

Then configure the plugin's `requestConfig.memory_db` to point at the server URL, for example `http://localhost:8848`.

## Verify

1. Start OpenClaw with the plugin enabled.
2. Ask OpenClaw to remember a probe such as `PowerMem OpenClaw probe: dragonfruit-zx9`.
3. Ask OpenClaw to recall `dragonfruit-zx9`.
4. Confirm the probe appears in the response.

## Troubleshooting

- If CLI mode fails, confirm `pmem` is available on `PATH`.
- If HTTP mode fails, confirm `http://localhost:8848/api/v1/system/health` is healthy.
- If recall returns nothing, confirm the same user/agent scope is used for write and search.

## Uninstall

Remove the OpenClaw plugin using OpenClaw's plugin management command. Do not delete `~/.openclaw/` memory data unless you explicitly want to remove stored memories.
