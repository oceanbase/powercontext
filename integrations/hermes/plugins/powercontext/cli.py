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

"""CLI commands for the PowerContext Hermes Memory Provider."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from .client import PowerContextError


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home  # ty: ignore[unresolved-import]

        return Path(get_hermes_home())
    except Exception:
        return Path.home() / ".hermes"


def _config_path(home: Path) -> Path:
    path_value = os.environ.get("POWERCONTEXT_HERMES_CONFIG", "").strip()
    return Path(path_value) if path_value else home / "powercontext" / "config.json"


def _load_config(home: Path) -> dict[str, Any]:
    path = _config_path(home)
    try:
        raw = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _provider(args: argparse.Namespace) -> Any:
    # Import the real provider only when a command is executed. Hermes imports
    # cli.py during argparse discovery with a synthetic package shell for user
    # providers; importing from `.` at module load time would therefore make
    # discovery fail before the command tree is registered.
    from plugins.memory import load_memory_provider  # ty: ignore[unresolved-import]

    home = _hermes_home()
    config = _load_config(home)
    if args.scope_id:
        config["scope_id"] = args.scope_id
    provider = load_memory_provider("powercontext")
    if provider is None:
        raise RuntimeError("PowerContext memory provider is not available")  # noqa: TRY003
    provider._config.update(config)
    provider.initialize(
        "cli",
        hermes_home=str(home),
        agent_identity=args.profile or os.environ.get("HERMES_PROFILE", "default"),
        user_id=args.user_id,
    )
    return provider


def _print_result(value: Any) -> None:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            print(value)
            return
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _add_common_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--scope-id", help="Override the PowerContext memory scope.")
    parser.add_argument("--profile", help="Hermes profile used in the default scope template.")
    parser.add_argument("--user-id", default="", help="User identifier used in the default scope template.")


def _add_citation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("family")
    parser.add_argument("artifact_id")
    parser.add_argument("revision", type=int)
    parser.add_argument("entry_id")
    parser.add_argument("entry_version_id")


def _citation_args(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "family": args.family,
        "artifact_id": args.artifact_id,
        "revision": args.revision,
        "entry_id": args.entry_id,
        "entry_version_id": args.entry_version_id,
    }


def cmd_status(args: argparse.Namespace) -> None:
    provider = _provider(args)
    try:
        print(f"Provider: {provider.name}")
        print(f"Scope: {provider._scope_id}")
        print(f"Server: {provider._client.base_url}")
        try:
            print(f"Liveness: {json.dumps(provider._client.get_liveness(), ensure_ascii=False)}")
            print(f"Readiness: {json.dumps(provider._client.get_readiness(), ensure_ascii=False)}")
        except PowerContextError as error:
            print(f"Server: unavailable ({error})")
    finally:
        provider.shutdown()


def cmd_search(args: argparse.Namespace) -> None:
    provider = _provider(args)
    try:
        _print_result(
            provider.handle_tool_call(
                "powercontext_search_memory",
                {"query": args.query, "limit": args.limit, "mode": args.mode},
            )
        )
    finally:
        provider.shutdown()


def cmd_remember(args: argparse.Namespace) -> None:
    provider = _provider(args)
    try:
        _print_result(
            provider.handle_tool_call(
                "powercontext_remember",
                {"kind": args.kind, "text": args.text, "reason": args.reason},
            )
        )
    finally:
        provider.shutdown()


def cmd_get(args: argparse.Namespace) -> None:
    provider = _provider(args)
    try:
        _print_result(provider.handle_tool_call("powercontext_get_memory", _citation_args(args)))
    finally:
        provider.shutdown()


def cmd_retire(args: argparse.Namespace) -> None:
    provider = _provider(args)
    try:
        payload = _citation_args(args)
        payload["reason"] = args.reason
        _print_result(provider.handle_tool_call("powercontext_retire_memory", payload))
    finally:
        provider.shutdown()


def cmd_flush(args: argparse.Namespace) -> None:
    provider = _provider(args)
    try:
        capabilities = provider._client.get_capabilities()
        if capabilities.get("memory_extraction") is False:
            print(
                "PowerContext memory extraction is disabled; configure "
                "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL and restart the server."
            )
            return
        _print_result(provider._client.flush_memory(provider._scope_id))
    except PowerContextError as error:
        print(f"PowerContext flush failed: {error}")
    finally:
        provider.shutdown()


def register_cli(subparser: argparse.ArgumentParser) -> None:
    """Register the ``hermes powercontext`` command tree."""
    commands = subparser.add_subparsers(dest="powercontext_command")

    status = commands.add_parser("status", help="Show provider scope and server health.")
    _add_common_options(status)
    status.set_defaults(func=cmd_status)

    search = commands.add_parser("search", help="Search PowerContext memories.")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=8)
    search.add_argument("--mode", choices=("auto", "fts", "vector", "hybrid"), default="auto")
    _add_common_options(search)
    search.set_defaults(func=cmd_search)

    remember = commands.add_parser("remember", help="Save an explicit PowerContext memory.")
    remember.add_argument("kind")
    remember.add_argument("text")
    remember.add_argument("--reason", default=None)
    _add_common_options(remember)
    remember.set_defaults(func=cmd_remember)

    get = commands.add_parser("get", help="Read one exact memory citation.")
    _add_citation_options(get)
    _add_common_options(get)
    get.set_defaults(func=cmd_get)

    retire = commands.add_parser("retire", help="Retire one exact memory citation.")
    _add_citation_options(retire)
    retire.add_argument("--reason", default=None)
    _add_common_options(retire)
    retire.set_defaults(func=cmd_retire)

    flush = commands.add_parser("flush", help="Run bounded Source-to-Memory processing.")
    _add_common_options(flush)
    flush.set_defaults(func=cmd_flush)
    subparser.set_defaults(func=powercontext_command)


def powercontext_command(args: argparse.Namespace) -> None:
    """Show a short usage hint when no PowerContext subcommand is supplied."""
    print("Usage: hermes powercontext {status,search,remember,get,retire,flush}")
