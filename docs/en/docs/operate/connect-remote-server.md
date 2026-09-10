---
title: Connect to a remote Server
description: Configure client endpoints and explicitly opt in to unencrypted HTTP when needed.
---

# Connect to a remote Server

Remote deployments should use HTTPS. IPv4/IPv6 loopback HTTP (including `localhost`) remains allowed by default.
PowerContext-owned clients reject other HTTP URLs unless you explicitly opt in. Private IP addresses and VPN
addresses are still non-loopback: a private network does not itself encrypt HTTP.

## Guided setup

Use the same source/ref for the installed PowerContext tool and the integration. On the machine that runs your agent:

```bash
powercontext setup claude-code --source oceanbase/powercontext --ref master \
  --server-url http://192.0.2.10:8000
```

In an interactive terminal, setup explains the plaintext risk and asks for confirmation; pressing Enter declines.
Setup resolves the host URL environment variable and `POWERCONTEXT_CLIENT_SERVER_URL` before asking, so the same
confirmation appears when the address came from the environment rather than `--server-url`.

For automation, `--json` and non-TTY input never prompt. Supply an explicit decision:

```bash
powercontext setup claude-code --server-url http://192.0.2.10:8000 --allow-insecure-http --json
```

Replace `claude-code` with `codex`, `dsh`, `openclaw`, `opencode`, `pi`, `hermes`, or `workbuddy`.
The same options work with `setup select --host ...` for its catalog hosts; WorkBuddy uses its dedicated command.
A declined or missing consent fails before installation starts. A warning is written to stderr, leaving JSON stdout
parseable. Successful installation saves the endpoint and consent for new sessions; it does not save credentials.

## Settings and precedence

Setup writes `~/.config/powercontext/clients.json`; override this path with `POWERCONTEXT_CLIENT_CONFIG_FILE`.
The file has a `version: 1` and a `hosts` map. Each host stores only `server_url` and `allow_insecure_http`.
Saved consent is tied to that endpoint: changing the effective URL does not inherit permission from a different URL.

For setup, an explicit CLI value takes precedence over the host environment, then the common environment, then
saved settings. Runtime host-native settings are also supported where available; consult the integration guide
for its URL source. An explicit false overrides inherited true. Environment boolean values accept
`true/false`, `1/0`, `yes/no`, and `on/off`; invalid values fail closed.

| Host | Opt-in environment variable |
| --- | --- |
| All PowerContext clients | `POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP` |
| Codex | `POWERCONTEXT_CODEX_ALLOW_INSECURE_HTTP` |
| Claude Code | `POWERCONTEXT_CLAUDE_ALLOW_INSECURE_HTTP` |
| DSH, Pi, OpenCode | `POWERCONTEXT_DSH_ALLOW_INSECURE_HTTP`, `POWERCONTEXT_PI_ALLOW_INSECURE_HTTP`, `POWERCONTEXT_OPENCODE_ALLOW_INSECURE_HTTP` |
| OpenClaw, Hermes, WorkBuddy | `POWERCONTEXT_OPENCLAW_ALLOW_INSECURE_HTTP`, `POWERCONTEXT_HERMES_ALLOW_INSECURE_HTTP`, `POWERCONTEXT_WORKBUDDY_ALLOW_INSECURE_HTTP` |

For the generic content CLI, the flag is a top-level option:

```bash
powercontext --server-url http://192.0.2.10:8000 --allow-insecure-http ready
powercontext doctor --server-url http://192.0.2.10:8000 --allow-insecure-http
powercontext doctor claude-code --json
```

Doctor reports explicitly insecure transport as `degraded` (nonzero exit status), not as encrypted or silently
healthy. This warning alone does not mean installation or connectivity failed.

## MCP, hooks, and upgrade boundaries

Setup configures both the native MCP URL and hook endpoint for Codex, Claude Code, and WorkBuddy.
Codex hooks intentionally use the installed plugin's `.mcp.json`; rerun setup after a plugin update that replaces
that file. Do not change only a hook URL environment variable and assume the native MCP URL changed too.

DSH custom `cordis.patch.yml` layers can override setup-managed settings. Setup accepts the standard empty
profile patch, but stops before installation when custom overlays are present: align the endpoint/consent
manually or remove those overrides before rerunning setup. Doctor reports unsupported native composition
(including unsupported JSON5/includes) as unknown/failed instead of claiming a safe loopback connection.

MiniMax and the generic Agent Plugin delegate MCP transport to the host and have no PowerContext setup subcommand
or independent HTTP client. Their host may have its own restrictions; this flag cannot override host policy.
LangChain, LangGraph, Pydantic AI, and the Bub evaluation adapter expose client consent without adding a host installer.

## Security boundary

The opt-in allows plaintext HTTP only. It does not disable HTTPS certificate verification, add authentication,
change the Server bind address, enable the Dashboard, or bypass a host's own MCP policy. Tokens and conversation
content can be read or modified in transit. IP allowlists limit access but do not encrypt traffic.

Keep Server authentication enabled for remote access. The Server's own binding and Receiver enrollment safety
checks remain separate; the client opt-in is not a replacement for those settings.

An SSH tunnel is an alternative: run `ssh -N -L 18000:127.0.0.1:8000 your-server` on the agent machine, then configure
`http://127.0.0.1:18000` without the insecure opt-in. See [Deploy the Server](deploy-server.md).
