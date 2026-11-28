---
title: Install with your Agent
description: Send one message to your coding Agent; it detects the environment, asks a few questions, and installs and checks PowerContext from your answers.
---

# Install with your Agent

Send this message to the coding Agent you use, such as Codex, OpenCode or Claude Code:

```text
Install PowerContext on this machine by following https://powercontext.oceanbase.io/en/llms.mdx/docs/get-started/agent-install/content.md. Ask me before deciding anything on my behalf, don't guess, and never read or show any of my keys.
```

The Agent detects the environment, asks you a few questions, then installs, configures, and walks you through the acceptance check. The rest of this page is written for the Agent that performs the installation.

## Rules

1. **Detect before asking.** Do not ask the user for facts you can detect on the system. Ask only the questions in [step 2](#2-ask-the-user); you can ask them all at once.
2. **Do not handle keys.** Never read, print or copy the contents of key files, such as `~/.codex/auth.json` or the API keys in `.env`; only check whether a file exists. When a key is needed, tell the user which line of which file to fill in, and let the user fill it in.
3. **Confirm before changing things.** Back up an existing configuration file and say what you will change before modifying it. Ask before registering a background service.
4. **Check every step and stop on failure.** Verify each step against its "Check". When something fails, stop and report the command and its output to the user. Do not work around the failure another way.
5. **Only the acceptance check counts.** A running service or a passing `doctor` only shows that installation and connections work, not that memory works. Do not tell the user the installation is complete before [step 4](#4-acceptance-check) passes.

## 1. Detect the environment

Run these and record the results; do not ask the user:

```bash
uname -s; uname -m
getconf GNU_LIBC_VERSION 2>/dev/null || sw_vers -productVersion 2>/dev/null
for agent in codex opencode claude zcode kimi pi openclaw hermes dsh; do command -v "$agent" >/dev/null && echo "agent: $agent"; done
test -f "$HOME/.codex/auth.json" && echo "codex login: present"
command -v powercontext >/dev/null && powercontext --version
```

From the results, decide:

- **Whether the platform can run the Gateway:** Linux x86_64 or arm64 with glibc 2.28 or later, or macOS 15 or later. Native Windows, musl systems such as Alpine, and other architectures can only use the [plugin path](#3b-plugin-path). On Windows, tell the user that the Gateway must be installed inside WSL2.
- **Installed Agents:** offer only the detected Agents in step 2.
- **Codex subscription:** `~/.codex/auth.json` exists when the Codex CLI is signed in. Only check that it exists; do not read it.
- **Existing installation:** when PowerContext is already installed, tell the user the version and ask whether to upgrade or keep it before continuing. When the user's Agent already has the PowerContext plugin, note it for step 3a.

## 2. Ask the user

Ask these questions at once, and state the default for each:

1. **Use it only on this computer, or deploy it for a team?** Default: only on this computer. For a team deployment, go to [step 3c](#3c-team-deployment) and skip the remaining questions.
2. **Which Agent should it connect to?** List the Agents detected in step 1 and choose the path from this table:

   | Condition | Path |
   | --- | --- |
   | The Agent is Codex or OpenCode, the platform can run the Gateway, and the Codex CLI is signed in | Recommend the [Gateway path](#3a-gateway-path) |
   | Any other Agent, or any condition in the previous row is not met | [Plugin path](#3b-plugin-path) |

   The Gateway path applies only to Agents that can use the OpenAI Responses API and send a conversation identifier with each request, which today means Codex and OpenCode. Other Agents get incomplete context injection or capture through the Gateway, so they use the plugin.

   When you recommend the Gateway path, explain the trade-off and let the user decide:
   - **Benefits:** the Gateway adds cost guidance to model requests, captures every turn in full, and generates memory after the prefix the Agent has already cached, so less input is paid for twice. Memory generation uses the Codex subscription, so no separate generation API is needed.
   - **Costs:** the Gateway is one more long-running process. Conversations from all projects go into one Scope. The plugin's MCP tools are not available, so the Agent cannot save memories or hand off work explicitly. Generation with a subscription does not support Topic Memory or Dream, and generation requests count against the subscription's usage limits.
3. **Which model generates memory?** The Gateway path uses the Codex subscription; do not ask. On the plugin path, the user can configure a model API (protocol, address, model name and API key), or choose basic memory, which generates no memory automatically and supports only explicit saves and searches.
4. **Which embedding model?** Subscriptions do not include an embedding model, so a separate API is needed.
   - When the user has an OpenAI-compatible embedding API, ask for the model name and output dimension, for example OpenAI's `text-embedding-3-small` with dimension 1536.
   - When the user does not want one, state the consequence: only full-text search remains, and longer prompts often recall nothing. Continue after the user confirms.

## 3a. Gateway path

In these steps, `<HOME>` is the absolute path of the user's home directory and `<model>` is a model the Codex subscription can use. Read the top-level `model` from `~/.codex/config.toml` as `<model>`; ask the user when the file or the key is missing.

### Install

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --gateway
```

Run the `export PATH=...` command that the installer prints at the end.

**Check:** `powercontext --version` and `pc-gateway --version` report the same version.

### When the plugin is already installed

On the Gateway path, the Gateway owns each turn's context and capture. The plugin cannot do the same at the same time; requests would be rejected with HTTP 503 `gate_paused`. Do not uninstall the plugin. Turn off its prompt-time recall and capture instead; the launch scripts below set both. Tell the user first:

- The plugin's MCP tools still work through the Gateway, but they write to the Scope the plugin binds, not the Scope the Gateway uses.
- Codex: earlier threads cannot continue through the Gateway; start a new thread. Old threads opened with plain `codex` are unaffected.
- OpenCode: earlier sessions can usually continue. A session in which the model called `pc_prepare_context` needs a new session.

### Configure and start the Server

```bash
mkdir -p "$HOME/powercontext"
```

Write the following to `<HOME>/powercontext/.env`, replace the values in angle brackets, then run `chmod 600 "$HOME/powercontext/.env"`:

```dotenv
POWERCONTEXT_SERVER_HTTP_PORT=17429
POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai-codex:<model>
POWERCONTEXT_SERVER_INFERENCE_PROTECTION='{"mode":"disabled","targets":{"approved-codex":{"endpoint":"https://chatgpt.com/backend-api/codex/responses","model":"<model>","protocol":"openai-responses","allow_streaming":true}}}'
POWERCONTEXT_SERVER_INFERENCE_PREFIX_EXTRACTION_GATEWAY_URL=http://127.0.0.1:8787
POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS=60
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=openai:<embedding-model>
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=<embedding-model>-<dimension>
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=<dimension>
OPENAI_API_KEY=
```

- Without an embedding model, delete the last four lines. For an embedding API other than OpenAI, rewrite the embedding lines as described in [Configure models](configure-models.md).
- Leave the `OPENAI_API_KEY=` line empty and ask the user to fill it in with an editor. Continue after the user confirms.
- Wrap the JSON value in single quotes; otherwise configuration parsing strips its double quotes.
- Inference protection must stay `disabled` with the `approved-codex` target. Codex subscription requests carry no output limit, and enabled protection withholds them.
- Core authentication is required even in this personal, loopback Gateway setup. Append the enforced access mode and generate a random Server token without printing it:

  ```bash
  printf 'POWERCONTEXT_SERVER_ACCESS_MODE=enforced\nPOWERCONTEXT_SERVER_AUTH_TOKEN=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> "$HOME/powercontext/.env"
  ```

Validate the configuration and register the personal service. The service starts at login: a systemd user service on Linux, a LaunchAgent on macOS.

```bash
cd "$HOME/powercontext"
powercontext config validate --env-file .env
powercontext service install --env-file .env
powercontext service status
export POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:17429
export POWERCONTEXT_CLIENT_API_TOKEN="$(sed -n 's/^POWERCONTEXT_SERVER_AUTH_TOKEN=//p' "$HOME/powercontext/.env")"
powercontext ready
powercontext scope create --title personal --summary "Personal coding context"
```

Keep `POWERCONTEXT_CLIENT_API_TOKEN` in the shell that launches the CLI and host plugin; Core authentication applies to them too, and the Gateway key does not replace their Core credential. The last command prints a line such as `scp_... personal (version 1)`. The leading `scp_...` is the Scope ID, written `<scope_id>` below.

**Check:** `service status` shows `server liveness: live`. In `ready`, `database`, `inference.generation` and `runtime` are `ready`, and so is `inference.embedding` when an embedding model is configured.

### Configure the Gateway

Write the following to `<HOME>/powercontext/gateway.env`:

```dotenv
PC_GATEWAY_CORE_BASE=http://127.0.0.1:17429
PC_GATEWAY_DEFAULT_SCOPE=<scope_id>
PC_GATEWAY_INJECTION=on
PC_GATEWAY_SEDIMENT=on
PC_GATEWAY_SUBSCRIPTION=codex
PC_GATEWAY_SUBSCRIPTION_CREDENTIALS=<HOME>/.codex/auth.json
PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES=approved-codex
```

The Gateway connects to the Server over HTTP, so it injects context only with `PC_GATEWAY_INJECTION=on`. Give it the Server token and generate a distinct client-facing key without displaying either value:

```bash
printf 'PC_GATEWAY_CORE_TOKEN=%s\n' "$(sed -n 's/^POWERCONTEXT_SERVER_AUTH_TOKEN=//p' "$HOME/powercontext/.env")" >> "$HOME/powercontext/gateway.env"
printf 'PC_GATEWAY_API_KEY=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> "$HOME/powercontext/gateway.env"
chmod 600 "$HOME/powercontext/gateway.env"
```

This personal flow reuses the Server token created above: `CORE_URL` is the value of `PC_GATEWAY_CORE_BASE`, and
`PC_GATEWAY_CORE_BASE` and `PC_GATEWAY_CORE_TOKEN` are in `gateway.env`; `ACCESS_ADMIN_TOKEN` is the
`POWERCONTEXT_SERVER_AUTH_TOKEN` in `.env`. The Server token is the
bootstrap administrator and has static Scope roles. Grant it `gateway.user` on `<scope_id>` before starting the
Gateway. A separate least-privilege Gateway identity instead needs `gateway.user`, `server.observer` on the Server
resource, and—because this setup enables capture—`scope.contributor` on the Scope. See [Gateway authentication and
target requirements](../operate/deploy-server.md#gateway-model-destination) for `/v1/access/me` and binding commands.

Create the launch script `<HOME>/powercontext/run-gateway.sh` and run `chmod 755` on it. It starts the Gateway after the Server is ready:

```sh
#!/bin/sh
set -a
. "$HOME/powercontext/gateway.env"
set +a
until curl -sf http://127.0.0.1:17429/health/ready >/dev/null; do sleep 2; done
exec "<absolute path of pc-gateway>" >>"$HOME/powercontext/gateway.log" 2>&1
```

Get the absolute path with `command -v pc-gateway`. Because prefix extraction is enabled above, also add the Gateway key to the Server callback headers and reload the Server configuration:

```bash
printf "POWERCONTEXT_SERVER_INFERENCE_PREFIX_EXTRACTION_GATEWAY_HEADERS='{\"Authorization\":\"Bearer %s\"}'\n" "$(sed -n 's/^PC_GATEWAY_API_KEY=//p' "$HOME/powercontext/gateway.env")" >> "$HOME/powercontext/.env"
powercontext service install --env-file "$HOME/powercontext/.env"
```

### Start the Gateway automatically

`powercontext service` manages only the Server, so register the Gateway separately. After the user agrees:

**Linux:** create `~/.config/systemd/user/powercontext-gateway.service`:

```ini
[Unit]
Description=PowerContext Gateway

[Service]
ExecStart=/bin/sh <HOME>/powercontext/run-gateway.sh
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now powercontext-gateway.service
```

**macOS:** create `~/Library/LaunchAgents/io.oceanbase.powercontext.gateway.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>io.oceanbase.powercontext.gateway</string>
  <key>ProgramArguments</key>
  <array><string>/bin/sh</string><string><HOME>/powercontext/run-gateway.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict>
</plist>
```

```bash
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/io.oceanbase.powercontext.gateway.plist
```

**Check:** `curl -s http://127.0.0.1:8787/healthz` responds. On failure, read `<HOME>/powercontext/gateway.log`.

### Connect Codex

Write the following to `~/.codex/powercontext-gateway.config.toml`. It is a separate file and leaves the user's existing Codex configuration unchanged:

```toml
model = "<model>"
model_provider = "powercontext-gateway"

[model_providers.powercontext-gateway]
name = "PowerContext gateway"
base_url = "http://127.0.0.1:8787/gateway/v1"
wire_api = "responses"
env_key = "PC_GATEWAY_KEY"
```

After the user agrees, create the launch script `~/.local/bin/codex-pc` and run `chmod 755` on it:

```sh
#!/bin/sh
# Codex through the PowerContext gateway; the gateway owns prompt-time context.
export PC_GATEWAY_KEY=local
export POWERCONTEXT_CODEX_PROMPT_RECALL=false
export POWERCONTEXT_CODEX_CAPTURE_PROMPTS=false
exec codex --profile powercontext-gateway "$@"
```

The Gateway listens only on this machine, so `PC_GATEWAY_KEY` can be any value, but it must be set. The last two settings matter only when the plugin is installed and are harmless otherwise.

### Connect OpenCode

Write the following to `<HOME>/powercontext/opencode-gateway.json`. OpenCode must use `@ai-sdk/openai`, which uses the Responses API. `@ai-sdk/openai-compatible` uses the Chat API, and streamed responses there are not captured:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {
    "powercontext-gateway": {
      "npm": "@ai-sdk/openai",
      "name": "PowerContext gateway",
      "options": { "baseURL": "http://127.0.0.1:8787/gateway/v1", "apiKey": "local" },
      "models": { "<model>": {} }
    }
  },
  "model": "powercontext-gateway/<model>"
}
```

After the user agrees, create the launch script `~/.local/bin/opencode-pc` and run `chmod 755` on it:

```sh
#!/bin/sh
# OpenCode through the PowerContext gateway; the gateway owns prompt-time context.
export OPENCODE_CONFIG="$HOME/powercontext/opencode-gateway.json"
export POWERCONTEXT_OPENCODE_PROMPT_RECALL=false
export POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS=false
exec opencode "$@"
```

## 3b. Plugin path

Use this for other Agents, or when the Gateway is not used. Follow the steps in the [Quickstart](https://powercontext.oceanbase.io/en/llms.mdx/docs/get-started/quickstart/content.md), with these notes:

- The install command is `curl -fsSL https://powercontext.oceanbase.io/install.sh | bash`.
- **The setup wizard is interactive, and an Agent cannot operate it.** Ask the user to run `powercontext config init --language en --output .env` in their own terminal. In Claude Code and Codex, the user can type `! powercontext config init --language en --output .env` to run it directly. Tell the user what to choose from the step 2 answers: SQLite for storage, "Only on this machine" for the usage scenario, full memory capabilities or basic memory according to question 3, and the Agent from question 2.
- The Quickstart registers the personal service with `powercontext service install`, so the Server starts at login.
- Replace `codex` in the Quickstart with the user's Agent. Host-specific steps are in the [integration guides](../integrations/index.md).

## 3c. Team deployment

Use this when an administrator deploys PowerContext for a team, including a first test environment. This step deploys on the current Linux host. Deploying in Kubernetes or Docker requires building your own image; ask the administrator to read [Deploy the Server](../operate/deploy-server.md) and [Deploy on Kubernetes](../operate/kubernetes.md), then stop.

### Confirm with the administrator

Ask these questions at once, and state the defaults:

1. **How do members reach it?**
   - **Reverse proxy or SSH forwarding** (default): the Server listens only on this machine. The administrator's existing Nginx, Caddy or similar provides HTTPS, or members use SSH port forwarding. The Server can be registered as a personal service and start automatically.
   - **Listen on a network address:** for a test environment on an internal network. Members use `http://<host address>:17429`, without HTTPS.
2. **Enable authentication?** Default: yes.
   - With authentication, the Server generates one token and every member shares that one identity, with the same permissions and visibility. Distinguishing users requires injecting an Authentication Provider in the deployment.
   - Without authentication is suitable only for a test environment on a trusted network. When authentication is enabled later, data written before then has no owner and cannot be modified until the administrator backfills it as described in [Migrate data written before enforcement](../operate/deploy-server.md#migrate-data-written-before-enforcement).
3. **Which storage?** Default: SQLite, with data on this host. With an existing OceanBase, ask for the connection URL.
4. **Which generation and embedding models?** A team deployment uses model APIs, not a personal subscription. Ask for each provider, base URL (optional for OpenAI), and model name, and for the embedding model's dimension.
5. **Which features?** Explain each row of this table and let the administrator decide:

| Feature | Default | When to enable | Requirements and costs |
| --- | --- | --- | --- |
| Gateway | Not deployed | Members' Agents can use the Responses API (such as Codex and OpenCode), and you want automatic capture, context injection and token savings | Members point their model address at the Gateway. The Gateway has one shared key and one default Scope today, so every member's conversations go into the same Scope. When deploying it, also ask for the upstream model's Responses address and model name |
| Context injection | On with the Gateway | You want the Gateway to add relevant memory to each turn automatically | Turning it off does not let an Agent plugin's prompt-time recall run alongside the Gateway: the Core rejects any context in a Gateway-forwarded request that the Gateway did not add |
| Capture | On with the Gateway | You want every turn to become a Source | The Source keeps the full conversation, including tool calls and their output |
| Cost guidance (Steering) | On | You want the model to avoid repeated reads and calls | Applies to the Responses API only. It can be replaced with the team's own instruction file of up to 4096 bytes |
| Prefix extraction | On with the Gateway | You want memory generation to reuse the prefix the Agent has already cached, so less input is paid for twice | Requires capture, with the Gateway and the Server on the same host. Extraction requests go to the Gateway's upstream account and are billed; when reuse is not cheaper, normal generation is used. See [Memory extraction over the cached prefix](../operate/deploy-server.md#memory-extraction-over-the-cached-prefix) |
| One-pass memory processing | Used with prefix extraction | You want extraction, reconciliation and re-checks done in one conversation | When off, candidates are processed one at a time. Over the prefix, the existing memories being compared are also sent to the Gateway's upstream model and account |
| Inference protection | `disabled` | You need privacy and compliance checks before and after every model call | In `enforcing` mode, requests without an output limit are withheld. Codex sends no output limit, so every Codex request through the Gateway is withheld. See [Inference privacy protection](../operate/inference-protection.md) |

### Install

With the Gateway:

```bash
curl -fsSL https://powercontext.oceanbase.io/install.sh | bash -s -- --gateway
```

Without the Gateway, drop `-s -- --gateway`. Run the `export PATH=...` command that the installer prints at the end.

### Write the Server configuration

```bash
mkdir -p "$HOME/powercontext"
```

Write this base configuration to `<HOME>/powercontext/.env`, append each group the administrator chose, then run `chmod 600 "$HOME/powercontext/.env"`:

```dotenv
POWERCONTEXT_SERVER_HTTP_PORT=17429
POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai:<generation-model>
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=openai:<embedding-model>
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=<embedding-model>-<dimension>
POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=<dimension>
POWERCONTEXT_SERVER_RUNTIME_MEMORY_SCHEDULE_SECONDS=60
POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS=300
POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS=900
OPENAI_API_KEY=
```

- For another OpenAI-compatible service, add `POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL` and `POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_BASE_URL`. For a different protocol, rewrite the lines as described in [Configure models](configure-models.md).
- Leave `OPENAI_API_KEY=` empty and ask the administrator to fill it in. Continue after the administrator confirms.

**When listening on a network address,** append:

```dotenv
POWERCONTEXT_SERVER_HTTP_HOST=0.0.0.0
```

Without authentication and without the Gateway, also append `POWERCONTEXT_SERVER_ALLOW_UNAUTHENTICATED_NON_LOOPBACK=true`.

**When deploying the Gateway, authentication is required even on loopback.** It is also required when the administrator otherwise chooses authenticated access. Append `POWERCONTEXT_SERVER_ACCESS_MODE=enforced`, and generate the token straight into the file without showing it; tell the administrator it is stored as `POWERCONTEXT_SERVER_AUTH_TOKEN` in `.env`:

```bash
printf 'POWERCONTEXT_SERVER_AUTH_TOKEN=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> "$HOME/powercontext/.env"
```

**With the Gateway,** append the following. `<upstream-endpoint>` is the upstream Responses address, such as `https://api.openai.com/v1/responses`, and `<upstream-model>` is the model members use. Set the inference protection mode the administrator chose, and wrap the JSON value in single quotes:

```dotenv
POWERCONTEXT_SERVER_INFERENCE_PROTECTION='{"mode":"disabled","targets":{"team-upstream":{"endpoint":"<upstream-endpoint>","model":"<upstream-model>","protocol":"openai-responses","allow_streaming":true}}}'
```

With prefix extraction, also append:

```dotenv
POWERCONTEXT_SERVER_INFERENCE_PREFIX_EXTRACTION_GATEWAY_URL=http://127.0.0.1:8787
```

Without one-pass, append `POWERCONTEXT_SERVER_RUNTIME_ATOMIC_MEMORY_PROCESSING_MODE=per_candidate`.

**Check:** `powercontext config validate --env-file "$HOME/powercontext/.env"` passes.

### Start the Server

**Reverse proxy or SSH forwarding:** register the personal service, which starts at login:

```bash
cd "$HOME/powercontext"
powercontext service install --env-file .env
powercontext service status
```

**Listen on a network address:** the personal service accepts only loopback addresses, so register the Server separately. After the administrator agrees, create `~/.config/systemd/user/powercontext-server.service`:

```ini
[Unit]
Description=PowerContext Server

[Service]
WorkingDirectory=<HOME>/powercontext
ExecStart=<absolute path of powercontext> server run --env-file <HOME>/powercontext/.env
Restart=on-failure

[Install]
WantedBy=default.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now powercontext-server.service
loginctl enable-linger "$USER"
```

`enable-linger` keeps the services running after the administrator logs out. If the command fails, ask the administrator to run it with an account that has permission.

Then check the Server and create the Scope the team uses. When deploying the Gateway or using any authenticated setup, read the token from `.env` first without showing it; clients and plugins must use their own Core credentials, not the Gateway API key:

```bash
export POWERCONTEXT_CLIENT_SERVER_URL=http://127.0.0.1:17429
export POWERCONTEXT_CLIENT_API_TOKEN="$(sed -n 's/^POWERCONTEXT_SERVER_AUTH_TOKEN=//p' "$HOME/powercontext/.env")"
powercontext ready
powercontext scope create --title team --summary "Team context"
```

The last command prints a line such as `scp_... team (version 1)`. The leading `scp_...` is the Scope ID, written `<scope_id>` below.

**Check:** in `ready`, `database`, `inference.generation`, `inference.embedding` and `runtime` are `ready`.

### Configure the Gateway

Skip this section without the Gateway. Write the following to `<HOME>/powercontext/gateway.env`:

```dotenv
PC_GATEWAY_CORE_BASE=http://127.0.0.1:17429
PC_GATEWAY_DEFAULT_SCOPE=<scope_id>
PC_GATEWAY_INJECTION=on
PC_GATEWAY_SEDIMENT=on
PC_GATEWAY_UPSTREAM_PROFILE_RESPONSES=team-upstream
PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES=
```

- Leave `PC_GATEWAY_UPSTREAM_API_KEY_RESPONSES=` empty and ask the administrator to fill in the upstream API key. Start the Gateway only after the administrator confirms.
- To turn injection off, use `PC_GATEWAY_INJECTION=off`; to turn capture off, delete `PC_GATEWAY_SEDIMENT=on`. To turn Steering off, append `PC_GATEWAY_STEERING=off`; for team instructions, append `PC_GATEWAY_STEERING_FILE=<file path>`. To turn prefix extraction off, append `PC_GATEWAY_PREFIX_EXTRACTION=off`.
- The Gateway always needs the authenticated Server token to call Core. Copy it with the command below, without showing it; it must differ from `PC_GATEWAY_API_KEY`:

  ```bash
  printf 'PC_GATEWAY_CORE_TOKEN=%s\n' "$(sed -n 's/^POWERCONTEXT_SERVER_AUTH_TOKEN=//p' "$HOME/powercontext/.env")" >> "$HOME/powercontext/gateway.env"
  ```

- The Gateway requires a nonempty client-facing key on every listener, including loopback. Members use this key for model routes; it must differ from the Core token. Generate it without showing it:

  ```bash
  printf 'PC_GATEWAY_API_KEY=%s\n' "$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')" >> "$HOME/powercontext/gateway.env"
  ```

- When listening on a network address, also append `PC_GATEWAY_LISTEN=0.0.0.0:8787`.

In this flow, `CORE_URL` is `PC_GATEWAY_CORE_BASE` from `gateway.env`, `PC_GATEWAY_CORE_TOKEN` is also in that file,
and `ACCESS_ADMIN_TOKEN` is the Server token in `.env`; that token is the bootstrap administrator credential. If you
replace it with a dedicated Gateway identity, bind `gateway.user`, `server.observer` and (because capture is enabled)
`scope.contributor` as described in [Gateway authentication and target requirements](../operate/deploy-server.md#gateway-model-destination).

With prefix extraction enabled, set the callback header from the Gateway key and reload the Server:

```bash
printf "POWERCONTEXT_SERVER_INFERENCE_PREFIX_EXTRACTION_GATEWAY_HEADERS='{\"Authorization\":\"Bearer %s\"}'\n" "$(sed -n 's/^PC_GATEWAY_API_KEY=//p' "$HOME/powercontext/gateway.env")" >> "$HOME/powercontext/.env"
powercontext config validate --env-file "$HOME/powercontext/.env"
powercontext service install --env-file "$HOME/powercontext/.env"
```

This team flow also uses the bootstrap Server token as the Gateway credential. Grant it `gateway.user` on `<scope_id>`; a dedicated Gateway identity instead needs the grants described above. The Gateway API key only authenticates model-route requests, while Core clients and plugins need their own Core credentials.

Run `chmod 600 "$HOME/powercontext/gateway.env"`, then create `run-gateway.sh` and start the Gateway automatically as in [step 3a](#start-the-gateway-automatically).

**Check:** `curl -s http://127.0.0.1:8787/healthz` responds.

### Acceptance check

With the Gateway, simulate a member's Agent with one request that sends a conversation identifier and the Gateway key:

```bash
. "$HOME/powercontext/gateway.env"
curl -s http://127.0.0.1:8787/gateway/v1/responses \
  -H 'Content-Type: application/json' -H "Authorization: Bearer $PC_GATEWAY_API_KEY" \
  -H 'X-PowerContext-Conversation-ID: admin-check-1' \
  -d '{"model":"<upstream-model>","input":"Our team decided that every API response returns times in UTC. Repeat this convention."}'
```

Wait a minute or two, then search the team Scope for the decision:

```bash
curl -s http://127.0.0.1:17429/v1/memory/search -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $POWERCONTEXT_CLIENT_API_TOKEN" \
  -d '{"scope_id":"<scope_id>","query":"API response times UTC","limit":5}'
```

The `hits` in the result include the convention. The model rewrites memories, so check the meaning, not an exact string. Without the Gateway, save a memory explicitly with the `powercontext` CLI or MCP and search for it.

When done, tell the administrator:

- What members need to connect: the Server address, and the token in `.env` when authentication is enabled; with the Gateway, also the Gateway address and `PC_GATEWAY_API_KEY` from `gateway.env`. The addresses depend on the access method: the proxy's public HTTPS address with a reverse proxy, the member's forwarded local address with SSH forwarding, or `http://<host address>:17429` and `http://<host address>:8787/gateway/v1` when listening on a network address. How members connect is up to the administrator and is not covered here.
- For HTTPS, forward the reverse proxy to `127.0.0.1:17429` and `127.0.0.1:8787`.
- `.env` and `gateway.env` contain keys; do not commit them to Git.

## 4. Acceptance check

A team deployment uses the acceptance check in step 3c instead of this section.

Ask the user to start the Agent: on the Gateway path, with `codex-pc` or `opencode-pc` in a new session; on the plugin path, the chosen Agent directly. On the plugin path, Codex and Claude Code must trust the PowerContext hooks in `/hooks` in the first session.

1. Ask the user to send a message with a concrete decision, for example: "Our project decision: store all money amounts as integer cents, never as floats. Repeat this convention."
2. Wait a minute or two, then check that the decision became a memory:

   ```bash
   curl -s http://127.0.0.1:17429/v1/memory/search -H 'Content-Type: application/json' \
     -d '{"scope_id":"<scope_id>","query":"money amounts integer cents","limit":5}'
   ```

   The `hits` in the result include the convention. The model rewrites memories, so wording and punctuation change; check the meaning, not an exact string. On the plugin path, use `POWERCONTEXT_CLIENT_SERVER_URL` from `.env` as the address and the Scope the Agent binds; with authentication, add an `Authorization: Bearer` header.
3. Ask the user to open a **new session** and ask: "How are money amounts stored in this project?" The answer gives the integer-cents convention.
4. On the Gateway path, confirm that memory generation reused the Agent's cached prefix: look for `atomic_memory.prefixed_extraction` in the logs that `powercontext service status` names. Its presence shows the Server attempted prefix extraction. For short turns, the Server falls back to normal generation because reuse is not cheaper; that is expected.

After step 3 answers correctly, tell the user the installation is complete, and that:

- On the Gateway path, only Codex or OpenCode started with `codex-pc` or `opencode-pc` goes through PowerContext; plain `codex` and `opencode` are unaffected.
- The configuration is in `<HOME>/powercontext`. `.env` contains keys; do not commit it to Git.
- To use the same memory from other computers later, see [Connect to a remote Server](../operate/connect-remote-server.md).
