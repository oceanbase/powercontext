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

"""Early PowerContext slash-command registration for the Hermes integration.

The actual PowerContext memory provider is intentionally an exclusive Hermes
provider.  Hermes loads that provider when it creates an Agent, which is too
late for the TUI's initial slash-command registry.  This small standalone
companion registers the command during normal plugin discovery and forwards to
the active provider once an Agent exists.

Hermes v0.20.4 invokes plugin command handlers with raw arguments only.  The
gateway does not provide the caller's session, user, workspace, or scope to
the handler, so this companion only dispatches through Hermes' interactive CLI
reference.  Gateway invocations fail closed instead of selecting a provider
from another session.
"""

from __future__ import annotations

from typing import Any

_POWERCONTEXT_PROVIDER_NAME = "powercontext"
_SLASH_COMMAND_NAMES = ("pc", "powercontext")
_POWERCONTEXT_SUBCOMMANDS = (
    "status",
    "search",
    "list",
    "changes",
    "get",
    "remember",
    "revise",
    "retire",
    "flush",
    "stats",
    "handoff",
    "experience",
    "skill",
    "external-skills",
    "review",
    "scope",
    "trace",
    "call",
)
_NOT_INITIALIZED = (
    "PowerContext is not initialized for this Hermes session yet. "
    "Send a normal message first so Hermes can create the Agent, then retry /pc "
    "or /powercontext."
)


def _active_provider(context: Any) -> Any | None:
    """Return the provider for Hermes' current interactive CLI Agent.

    ``_cli_ref`` is set by the interactive CLI and is ``None`` in the gateway,
    where Hermes v0.20.4 does not expose the invoking session to plugin
    commands.  Do not fall back to a cached or process-global provider.
    """

    manager = getattr(context, "_manager", None)
    cli = getattr(manager, "_cli_ref", None)
    agent = getattr(cli, "agent", None)
    memory_manager = getattr(agent, "_memory_manager", None)
    if memory_manager is None:
        return None

    providers: Any = getattr(memory_manager, "providers", ())
    try:
        providers = providers() if callable(providers) else providers
        iterator = iter(providers)
    except TypeError:
        return None

    for provider in iterator:
        if str(getattr(provider, "name", "")).strip().lower() == _POWERCONTEXT_PROVIDER_NAME:
            return provider
    return None


def _handle_slash_command(context: Any, raw_args: str) -> str:
    provider = _active_provider(context)
    if provider is None:
        return _NOT_INITIALIZED

    handler = getattr(provider, "handle_slash_command", None)
    if not callable(handler):
        return "PowerContext does not expose its slash-command handler."

    try:
        result = handler(raw_args)
    except Exception as error:  # pragma: no cover - provider owns detailed errors
        return f"PowerContext slash command failed: {error}"
    return "" if result is None else str(result)


def _register_subcommands() -> None:
    """Expose PowerContext subcommands to Hermes' slash completer."""
    try:
        from hermes_cli.commands import SUBCOMMANDS  # ty: ignore[unresolved-import]
    except (ImportError, AttributeError):
        return
    for command_name in _SLASH_COMMAND_NAMES:
        SUBCOMMANDS[f"/{command_name}"] = list(_POWERCONTEXT_SUBCOMMANDS)


def register(ctx: Any) -> None:
    """Register both PowerContext aliases before Hermes creates the first Agent."""

    handler = lambda raw_args: _handle_slash_command(ctx, raw_args)
    for name in _SLASH_COMMAND_NAMES:
        ctx.register_command(
            name,
            handler,
            description="Inspect and manage PowerContext memory, handoffs, artifacts, and traces.",
            args_hint="status|search|list|changes|get|remember|revise|retire|flush|stats|handoff|experience|skill|external-skills|review|scope|trace|call ...",
        )
    _register_subcommands()


__all__ = ["register"]
