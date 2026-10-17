# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A Harbor agent for Kimi Code, with the local PowerContext Kimi Code plugin installed in the ON arm."""

from __future__ import annotations

import contextlib
import ipaddress
import json
import os
import shlex
from pathlib import Path
from typing import Any, override
from urllib.parse import urlsplit

from harbor.agents.installed.base import BaseInstalledAgent, with_prompt_template
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths

from .harbor_agent import REMOTE_SOURCE, clear_step_tests

# The Kimi Code and Node versions that PowerContext's Kimi Code acceptance workflow tests. Harbor 0.16's Kimi agent
# installs the earlier Python kimi-cli, which has another home, configuration, and plugin format.
KIMI_CODE_VERSION = "2.1.1"
KIMI_CODE_PACKAGE = "@moonshot-ai/kimi-code"
NODE_VERSION = "24.21.0"
REMOTE_PLUGIN = f"{REMOTE_SOURCE}/integrations/kimi-code/plugins/powercontext"
# The plugin's hook and Kimi Code's MCP connection read the Server token from here, without the Bearer scheme.
KIMI_TOKEN = "POWERCONTEXT_KIMI_TOKEN"  # noqa: S105 - a variable name
# The plugin reads its Scope only from its installed configuration, which the ON arm writes from this setting.
KIMI_SCOPE = "POWERCONTEXT_KIMI_SCOPE_ID"
KIMI_HOME = '"${KIMI_CODE_HOME:-$HOME/.kimi-code}"'
# Kimi Code keeps sessions, the files they edited, a search cache, and logs in its home, and nothing else clears them
# between the steps of a trial. A new session needs only the configuration and the installed plugin.
KIMI_HOME_KEPT = ("config.toml", "plugins")
# What `powercontext setup kimi-code` writes into the copy it installs; a test compares the two installations.
MEMORY_TOOLS = ("search_memory", "remember_memory", "get_artifact_revision")
OWNER_MARKER = ".powercontext-owned"
# The plugin accepts plain HTTP only on loopback, so the ON arm reaches a plain-HTTP Server through a relay that listens
# here. The port is one a workload is unlikely to use, as the relay holds it in the ON arm only.
RELAY_PORT = 47731
# Where Harbor keeps the agent's logs for each step in the container, and what the agent writes there.
AGENT_LOGS = EnvironmentPaths.agent_dir.as_posix()
OUTPUT_FILENAME = "kimi-code.txt"
SESSION_LOGS = "kimi-code-sessions"
# Kimi Code sizes compaction from this window and asks an OpenAI-compatible provider for up to 131,072 output tokens
# per request, which a model's window must also hold.
MAX_CONTEXT_SIZE = 262144
# The OpenAI-compatible providers the harness can configure, by the provider part of a `provider/model` name.
PROVIDERS = {"openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY")}
# Keep the pinned Kimi Code version for the whole run.
KIMI_ENV = {"KIMI_CODE_NO_AUTO_UPDATE": "1"}
# Run with Node as `node -e CONFIGURE_PLUGIN <home> <plugin> <source> <Server URL> <Scope> <use token>` in the copied
# plugin, as `powercontext setup kimi-code` configures the copy it installs.
CONFIGURE_PLUGIN = f"""
const fs = require('fs')
const path = require('path')
const [home, plugin, source, serverUrl, scopeId, useToken] = process.argv.slice(1)
const write = (file, value) => fs.writeFileSync(file, JSON.stringify(value, null, 2) + '\\n')
const useTokenValue = useToken === 'true'
write(path.join(plugin, 'powercontext.json'), {{
  server_url: serverUrl, scope_id: scopeId, capture_prompts: true, use_token: useTokenValue,
}})
const manifestPath = path.join(plugin, 'kimi.plugin.json')
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'))
manifest.mcpServers = {{ powercontext: {{
  url: serverUrl + '/mcp',
  ...(useTokenValue ? {{ bearerTokenEnvVar: '{KIMI_TOKEN}' }} : {{}}),
  enabledTools: {json.dumps(list(MEMORY_TOOLS))},
  startupTimeoutMs: 10000,
  toolTimeoutMs: 10000,
}} }}
write(manifestPath, manifest)
const skill = path.join(plugin, 'skills/powercontext-project-context/SKILL.md')
const guidance = fs.readFileSync(skill, 'utf8')
fs.writeFileSync(skill, guidance.replaceAll('__POWERCONTEXT_SCOPE_JSON__', () => JSON.stringify(scopeId)))
const now = new Date().toISOString()
write(path.join(home, 'plugins/installed.json'), {{ version: 1, plugins: [{{
  id: 'powercontext', root: plugin, source: 'local-path', enabled: true,
  installedAt: now, updatedAt: now, originalSource: source,
}}] }})
"""
# Run with Node as `node -e RELAY <listen port> <host> <port>`: relay each connection to the Server unchanged.
RELAY = (
    "const net = require('net'); const [listen, host, port] = process.argv.slice(1); "
    "net.createServer(client => { const server = net.connect(Number(port), host); "
    "const close = () => { client.destroy(); server.destroy() }; "
    "for (const socket of [client, server]) socket.on('error', close).on('close', close); "
    "client.pipe(server).pipe(client) }).listen(Number(listen), '127.0.0.1')"
)


class PowerContextKimiCodeAgent(BaseInstalledAgent):
    """Run Kimi Code in prompt mode, with the PowerContext Kimi Code plugin installed only in the ON arm.

    As on the other hosts, the OFF arm runs without the plugin. The ON arm configures the plugin as ``powercontext
    setup kimi-code`` does, with the arm's Scope and the Server URL as the agent container reaches it; the plugin reads
    the Server token from its environment, which only the ON arm receives. Each session starts without Kimi Code's
    earlier sessions, as sessions do on the other hosts, so neither arm can read an earlier session from Kimi Code's
    own files.
    """

    def __init__(
        self,
        *,
        server_url: str,
        powercontext: bool = True,
        reasoning_effort: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(version=KIMI_CODE_VERSION, **kwargs)
        if self.mcp_servers or self.skills_dir:
            raise ValueError("The Kimi Code agent does not register a task's MCP servers or Skills")  # noqa: TRY003
        self._config = kimi_config(self.model_name, reasoning_effort)
        self._provider_key = PROVIDERS[_provider(self.model_name)][1]
        self._powercontext = powercontext
        self._plugin_url, self._relay = plugin_endpoint(server_url)

    @staticmethod
    @override
    def name() -> str:
        return "kimi-code"

    @override
    def get_version_command(self) -> str | None:
        return ". ~/.nvm/nvm.sh; kimi --version"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        # The steps of Harbor's Node-based agents, with the Node version Kimi Code is tested on.
        await self.exec_as_root(
            environment,
            command="apt-get update && apt-get install -y curl",
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.2/install.sh | bash && "
                'export NVM_DIR="$HOME/.nvm" && '
                '\\. "$NVM_DIR/nvm.sh" || true && '
                "command -v nvm &>/dev/null || { echo 'Error: NVM failed to load' >&2; exit 1; } && "
                f"nvm install {NODE_VERSION} && "
                # The package's install script only renames a kimi-cli launcher that would shadow it.
                f"npm install -g --ignore-scripts {KIMI_CODE_PACKAGE}@{KIMI_CODE_VERSION} && "
                "kimi --version"
            ),
            env=KIMI_ENV,
        )
        await self.exec_as_agent(environment, command=write_config_command(self._config))
        if self._powercontext:
            await self.exec_as_agent(environment, command=install_plugin_command(self._plugin_url))

    @with_prompt_template
    @override
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        if not (key := os.environ.get(self._provider_key)):
            raise RuntimeError(f"Kimi Code's model needs {self._provider_key}")  # noqa: TRY003
        await self.exec_as_agent(environment, command=clear_sessions_command(), env=KIMI_ENV)
        await clear_step_tests(environment)
        if self._powercontext and self._relay is not None:
            await self.exec_as_agent(environment, command=start_relay_command(*self._relay))
        try:
            await self.exec_as_agent(
                environment, command=run_command(instruction), env={**KIMI_ENV, self._provider_key: key}
            )
        finally:
            # Kimi Code records each model request's usage in its session. Copying it after a timed-out session too
            # keeps the usage of the requests that session made.
            with contextlib.suppress(Exception):
                await self.exec_as_agent(environment, command=copy_session_logs_command())

    @override
    def populate_context_post_run(self, context: AgentContext) -> None:
        usage = session_usage(self.logs_dir / SESSION_LOGS)
        if usage is not None:
            context.n_input_tokens, context.n_cache_tokens, context.n_output_tokens = usage


def _provider(model_name: str | None) -> str:
    provider, separator, model = (model_name or "").partition("/")
    if not separator or not model or provider not in PROVIDERS:
        raise ValueError(  # noqa: TRY003
            f"The Kimi Code agent takes a model named provider/model from {', '.join(PROVIDERS)}, not {model_name!r}"
        )
    return provider


def kimi_config(model_name: str | None, reasoning_effort: str | None) -> str:
    """Return Kimi Code's ``config.toml`` for the run's model, the same in both arms."""

    provider = _provider(model_name)
    model = (model_name or "").partition("/")[2]
    base_url, key = PROVIDERS[provider]
    capabilities = ["thinking", "tool_use"] if reasoning_effort else ["tool_use"]
    lines = [
        'default_model = "harbor"',
        # No usage data goes to Moonshot, and no extra model request titles each session.
        "telemetry = false",
        "auto_session_title = false",
        "",
        f"[providers.{provider}]",
        'type = "openai"',
        f"base_url = {json.dumps(base_url)}",
        f"api_key_env = {json.dumps(key)}",
        "",
        "[models.harbor]",
        f"provider = {json.dumps(provider)}",
        f"model = {json.dumps(model)}",
        f"max_context_size = {MAX_CONTEXT_SIZE}",
        f"capabilities = {json.dumps(capabilities)}",
    ]
    if reasoning_effort:
        # Kimi Code takes the effort from those a model declares, and sends it as reasoning_effort.
        lines += [
            f"support_efforts = {json.dumps([reasoning_effort])}",
            "",
            "[thinking]",
            f"effort = {json.dumps(reasoning_effort)}",
        ]
    return "\n".join(lines) + "\n"


def plugin_endpoint(server_url: str) -> tuple[str, tuple[str, int] | None]:
    """Return the Server URL for the plugin, and the Server address a relay must forward to, if the plugin needs one.

    The plugin takes the Server as an origin, over HTTPS anywhere and over plain HTTP only on loopback, while a
    container reaches a Server on its host at an address such as ``host-gateway``. Another URL would leave the plugin
    inactive in every ON run, so it is rejected.
    """

    parsed = urlsplit(server_url)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise ValueError(f"The Kimi Code plugin takes the Server as an HTTP or HTTPS origin, not {server_url!r}")  # noqa: TRY003
    if parsed.scheme == "http" and not _loopback(parsed.hostname):
        return f"http://127.0.0.1:{RELAY_PORT}", (parsed.hostname or "", parsed.port or 80)
    return server_url.rstrip("/"), None


def _loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def write_config_command(config: str) -> str:
    return f'home={KIMI_HOME}; mkdir -p "$home" && printf %s {shlex.quote(config)} > "$home/config.toml"'


def install_plugin_command(server_url: str) -> str:
    """Install the mounted plugin where ``powercontext setup kimi-code`` does, configured for the arm's Scope.

    Kimi Code installs plugins only from its interactive interface, so the command writes the copy and the registry
    entry that setup writes.
    """

    return (
        ". ~/.nvm/nvm.sh; set -eu; "
        f'home={KIMI_HOME}; plugin="$home/plugins/managed/powercontext"; '
        'mkdir -p "$home/plugins/managed"; rm -rf "$plugin"; '
        f'cp -R {REMOTE_PLUGIN} "$plugin"; '
        f"printf 'powercontext-kimi-code-v1\\n' > \"$plugin/{OWNER_MARKER}\"; "
        # Setup enables the token when the variable is set, as it is in the ON arm against an authenticated Server.
        f'if [ -n "${{{KIMI_TOKEN}+set}}" ]; then use_token=true; else use_token=false; fi; '
        f'node -e {shlex.quote(CONFIGURE_PLUGIN)} "$home" "$plugin" {REMOTE_PLUGIN} {shlex.quote(server_url)} '
        f'"${{{KIMI_SCOPE}:?}}" "$use_token"'
    )


def start_relay_command(host: str, port: int) -> str:
    """Start the relay to the Server unless it is already listening, and fail when it does not start."""

    listening = f"(exec 3<>/dev/tcp/127.0.0.1/{RELAY_PORT}) 2>/dev/null"
    return (
        ". ~/.nvm/nvm.sh; "
        f"if ! {listening}; then "
        f"setsid -f node -e {shlex.quote(RELAY)} {RELAY_PORT} {shlex.quote(host)} {port} >/dev/null 2>&1 </dev/null; "
        f"for _ in $(seq 50); do {listening} && break; sleep 0.1; done; "
        "fi; "
        f"{listening} || {{ echo 'The relay to the PowerContext Server did not start' >&2; exit 1; }}"
    )


def clear_sessions_command() -> str:
    """Remove everything an earlier session left in Kimi Code's home.

    Besides the sessions themselves, Kimi Code keeps the files a session edited, a search cache, and logs there, and the
    plugin keeps its observations. The command then asks Kimi Code for its sessions and fails if any remain, so sessions
    stored elsewhere stop the run instead of leaving an earlier session readable.
    """

    kept = " ".join(f"! -name {name}" for name in KIMI_HOME_KEPT)
    return (
        "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
        f"home={KIMI_HOME}; "
        'mkdir -p "$home" || exit 1; '
        # The trailing /. makes find descend into the directory when it is a symlink.
        f'find "$home"/. -mindepth 1 -maxdepth 1 {kept} -exec rm -rf {{}} + || exit 1; '
        "sessions=$(kimi session list --all --archived --json) || exit 1; "
        # Kimi Code prints an empty JSON list when it has no sessions.
        'case "$(printf %s "$sessions" | tr -d \'[:space:][]\')" in '
        "'') ;; "
        "*) echo 'Kimi Code still lists an earlier session after clearing its home' >&2; exit 1 ;; "
        "esac"
    )


def run_command(instruction: str) -> str:
    return (
        ". ~/.nvm/nvm.sh; "
        f"kimi -p {shlex.quote(instruction)} --output-format stream-json </dev/null 2>&1 "
        f"| stdbuf -oL tee {AGENT_LOGS}/{OUTPUT_FILENAME}"
    )


def copy_session_logs_command() -> str:
    """Copy the session's event logs, one per agent, to the agent logs that Harbor keeps for each step."""

    return (
        f'home={KIMI_HOME}; target={AGENT_LOGS}/{SESSION_LOGS}; mkdir -p "$target"; '
        'cd "$home/sessions" && find . -name wire.jsonl | while IFS= read -r log; do '
        'mkdir -p "$target/${log%/*}" && cp "$log" "$target/$log"; done'
    )


def session_usage(directory: Path) -> tuple[int, int, int] | None:
    """Sum the usage Kimi Code recorded for the session's model requests, over its main agent and any subagents.

    Kimi Code records each request once, in the event log of the agent that made it. Returns the input tokens,
    including cached ones, the cached input tokens, and the output tokens, or nothing when no request was recorded.
    """

    input_tokens = cached_tokens = output_tokens = 0
    recorded = False
    for log in sorted(directory.rglob("wire.jsonl")):
        # Kimi Code ends each record with LF and leaves U+2028 and the other separators unescaped inside a string.
        for line in log.read_text(encoding="utf-8", errors="replace").split("\n"):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict) or event.get("type") != "usage.record":
                continue
            usage = event.get("usage")
            if not isinstance(usage, dict):
                continue
            counts = {
                name: usage.get(name, 0) for name in ("inputOther", "inputCacheRead", "inputCacheCreation", "output")
            }
            if not all(isinstance(value, int) and not isinstance(value, bool) for value in counts.values()):
                continue
            recorded = True
            input_tokens += counts["inputOther"] + counts["inputCacheRead"] + counts["inputCacheCreation"]
            cached_tokens += counts["inputCacheRead"]
            output_tokens += counts["output"]
    return (input_tokens, cached_tokens, output_tokens) if recorded else None
