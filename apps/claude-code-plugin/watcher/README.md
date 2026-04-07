# Workspace file watcher (optional)

The poller lives in the same **native binary** as the Claude hooks (no Python).

From the plugin root. `POWERMEM_BASE_URL` defaults to `http://localhost:8000` if unset (optional `POWERMEM_API_KEY`):

```bash
sh hooks/run-hook.sh poll
```

Or invoke the binary for your OS directly, e.g. `hooks/bin/powermem-hook-linux-amd64 poll`.

Environment variables match the former Python script: `POWERMEM_WATCH_ROOT`, `POWERMEM_POLL_INTERVAL`, `POWERMEM_WATCH_SUFFIXES`, `POWERMEM_WATCH_IGNORE_DIRS`.

On **Windows** (PowerShell):

```powershell
.\hooks\bin\powermem-hook-windows-amd64.exe poll
```

(Use `arm64` on ARM64 Windows if you build that target.)
