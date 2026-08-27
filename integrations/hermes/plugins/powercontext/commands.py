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

"""Slash-command and tool adapters for the Hermes PowerContext provider."""

from __future__ import annotations

import json
import logging
import shlex
from typing import Any

from .client import PowerContextError
from .helpers import (
    DEFAULT_MAX_BYTES,
    DEFAULT_RETRIEVAL_LIMIT,
    as_int,
    citation_from_args,
    config_value,
)
from .operations import OPERATION_REQUIRED_FIELDS, OPERATION_TOOL_MAP
from .workstream import clear_scope, write_scope

try:
    from tools.registry import tool_error  # ty: ignore[unresolved-import]
except ImportError:  # pragma: no cover - test/standalone fallback.

    def tool_error(message: str) -> str:
        return json.dumps({"error": message}, ensure_ascii=False)


logger = logging.getLogger(__name__)

POWERCONTEXT_SUBCOMMANDS = (
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
    "workstream",
    "trace",
    "call",
)


def register_subcommands() -> None:
    """Expose PowerContext's first-level commands to Hermes autocomplete.

    Hermes v0.20.4 accepts ``args_hint`` for plugin commands but only builds
    its static ``SUBCOMMANDS`` table for built-in commands.  Updating that
    host-owned table is the compatibility bridge that makes ``/pc <space>``
    show the same candidate menu as built-in commands such as ``/skills``.
    """
    try:
        from hermes_cli.commands import SUBCOMMANDS  # ty: ignore[unresolved-import]
    except (ImportError, AttributeError):
        return
    for command_name in ("pc", "powercontext"):
        SUBCOMMANDS[f"/{command_name}"] = list(POWERCONTEXT_SUBCOMMANDS)


def request_operation(provider: Any, operation: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if not provider._client or not provider._scope_id:
        raise PowerContextError("PowerContext is not initialized for this session")  # noqa: TRY003
    request_operation_method = getattr(provider._client, "request_operation", None)
    if not callable(request_operation_method):
        raise PowerContextError("the configured PowerContext client does not support this operation")  # noqa: TRY003

    operation_payload = dict(payload or {})
    operation_payload.pop("scope_id", None)
    missing = [
        field
        for field in OPERATION_REQUIRED_FIELDS.get(operation, ())
        if field not in operation_payload
        or operation_payload[field] is None
        or (isinstance(operation_payload[field], str) and not operation_payload[field].strip())
    ]
    if missing:
        raise ValueError(f"Missing required arguments: {', '.join(missing)}")  # noqa: TRY003

    if operation == "prepare_context":
        operation_payload.setdefault(
            "max_bytes",
            as_int(
                config_value(provider._config, "max_bytes", "POWERCONTEXT_HERMES_MAX_BYTES", DEFAULT_MAX_BYTES),
                DEFAULT_MAX_BYTES,
                minimum=512,
                maximum=32768,
            ),
        )
    elif operation == "capture_content_source":
        operation_payload.setdefault("metadata", {"origin": "hermes"})
    operation_payload["scope_id"] = provider._scope_id
    return request_operation_method(operation, operation_payload)


def parse_json_object(value: str, label: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ValueError(f"{label} must be a JSON object") from error  # noqa: TRY003
    if not isinstance(parsed, dict):
        raise TypeError(f"{label} must be a JSON object")  # noqa: TRY003
    return parsed


def _quoted_argument_end(value: str) -> int:
    """Return the end of the first shell-quoted argument in ``value``."""
    if not value or value[0] not in {"'", '"'}:
        raise ValueError("expected a quoted argument")  # noqa: TRY003
    quote = value[0]
    escaped = False
    for index, character in enumerate(value[1:], start=1):
        if quote == '"' and escaped:
            escaped = False
            continue
        if quote == '"' and character == "\\":
            escaped = True
            continue
        if character == quote:
            return index + 1
    raise ValueError("unterminated quoted JSON argument")  # noqa: TRY003


def _split_json_argument(raw_args: str, command: str, label: str) -> tuple[str, str]:
    """Extract one JSON object from a command and return it with its tail.

    ``shlex.split`` cannot be used on the complete command first because it
    treats the whitespace and quotes inside an unwrapped JSON object as shell
    syntax.  Decode the JSON prefix from the raw string, then tokenize only
    the arguments that follow it.  A shell-quoted JSON object remains accepted
    for compatibility with the previous command syntax.
    """
    text = raw_args.strip()
    tail = text[len(command) :].lstrip()
    if not tail:
        raise ValueError(f"{label} must be a JSON object")  # noqa: TRY003

    if tail[0] in {"'", '"'}:
        end = _quoted_argument_end(tail)
        tokens = shlex.split(tail[:end])
        if len(tokens) != 1:
            raise ValueError(f"{label} must be a JSON object")  # noqa: TRY003
        json_text = tokens[0]
    else:
        try:
            _parsed, end = json.JSONDecoder().raw_decode(tail)
        except json.JSONDecodeError as error:
            raise ValueError(f"{label} must be a JSON object") from error  # noqa: TRY003
        json_text = tail[:end]

    parse_json_object(json_text, label)
    return json_text, tail[end:].lstrip()


def workstream_command(provider: Any, args: list[str]) -> str:
    action = args[0].lower() if args else "status"
    if action == "status":
        return json.dumps(
            {
                "cwd": provider._workstream_cwd,
                "path": str(provider._workstream_path) if provider._workstream_path else None,
                "bound_scope_id": provider._workstream_bound_scope or None,
                "active_scope_id": provider._scope_id,
            },
            ensure_ascii=False,
            indent=2,
        )
    if action == "bind":
        if len(args) < 2 or not args[1].strip():
            return tool_error("Usage: /pc workstream bind SCOPE_ID")
        try:
            path = write_scope(provider._workstream_cwd, args[1])
        except (OSError, ValueError) as error:
            return tool_error(str(error))
        from .helpers import safe_scope

        provider._workstream_bound_scope = safe_scope(args[1])
        provider._switch_workstream_scope(provider._workstream_bound_scope)
        provider._record_trace_event("workstream_bound", scope_id=provider._scope_id, path=str(path))
        return json.dumps(
            {"status": "bound", "scope_id": provider._scope_id, "path": str(path)},
            ensure_ascii=False,
            indent=2,
        )
    if action == "clear":
        cleared = clear_scope(provider._workstream_cwd)
        provider._workstream_bound_scope = ""
        provider._switch_workstream_scope(provider._default_scope_id)
        provider._record_trace_event("workstream_cleared", cleared=cleared)
        return json.dumps({"status": "cleared" if cleared else "not_found"}, ensure_ascii=False, indent=2)
    return "Usage: /pc workstream {status|bind SCOPE_ID|clear}"


def operation_command(provider: Any, operation: str, args: list[str]) -> str:
    payload: dict[str, Any] = {}
    if args:
        payload = parse_json_object(args[0], "payload")
    result = request_operation(provider, operation, payload)
    return json.dumps(result, ensure_ascii=False, indent=2)


def memory_command(provider: Any, args: list[str]) -> str:  # noqa: C901
    action = args[0].lower() if args else "help"
    if action == "search":
        query = " ".join(args[1:]).strip()
        if not query:
            return tool_error("Usage: /pc search QUERY")
        return json.dumps(
            provider._client.search_memory(
                provider._scope_id,
                query[:8192],
                limit=DEFAULT_RETRIEVAL_LIMIT,
                mode="auto",
            ),
            ensure_ascii=False,
            indent=2,
        )
    if action == "list":
        return json.dumps(
            request_operation(provider, "list_memory_entries", {"include_inactive": "--inactive" in args[1:]}),
            ensure_ascii=False,
            indent=2,
        )
    if action == "changes":
        payload: dict[str, Any] = {}
        if len(args) >= 2:
            try:
                payload["since_revision"] = int(args[1])
            except ValueError as error:
                raise ValueError("since_revision must be an integer") from error  # noqa: TRY003
        return json.dumps(request_operation(provider, "list_memory_changes", payload), ensure_ascii=False, indent=2)
    if action == "get":
        if len(args) < 2:
            return tool_error("Usage: /pc get CITATION_JSON")
        return json.dumps(
            provider._client.get_memory_entry(provider._scope_id, parse_json_object(args[1], "citation")),
            ensure_ascii=False,
            indent=2,
        )
    if action == "remember":
        if len(args) < 3:
            return tool_error("Usage: /pc remember KIND TEXT [REASON]")
        result = provider._client.remember_memory(
            provider._scope_id,
            kind=args[1],
            text=args[2][:8192],
            reason=" ".join(args[3:]).strip() or None,
        )
        return json.dumps(result, ensure_ascii=False, indent=2)
    if action in {"revise", "retire"}:
        if len(args) < 2:
            return tool_error(f"Usage: /pc {action} CITATION_JSON ...")
        citation = parse_json_object(args[1], "citation")
        if action == "retire":
            result = provider._client.retire_memory_entry(
                provider._scope_id,
                citation,
                reason=" ".join(args[2:]).strip() or None,
            )
        else:
            if len(args) < 4:
                return tool_error("Usage: /pc revise CITATION_JSON KIND TEXT [REASON]")
            result = request_operation(
                provider,
                "revise_memory_entry",
                {
                    "citation": citation,
                    "kind": args[2],
                    "text": args[3][:8192],
                    "reason": " ".join(args[4:]).strip() or None,
                },
            )
        return json.dumps(result, ensure_ascii=False, indent=2)
    if action == "flush":
        return json.dumps(provider._client.flush_memory(provider._scope_id), ensure_ascii=False, indent=2)
    if action == "stats":
        payload = {"period": args[1]} if len(args) >= 2 else {}
        return json.dumps(request_operation(provider, "get_stats", payload), ensure_ascii=False, indent=2)
    return "Usage: /pc {search|list|changes|get|remember|revise|retire|flush|stats} ..."


def group_command(provider: Any, group: str, args: list[str]) -> str:
    operation_aliases = {
        "handoff": {
            "contract": "create_work_contract",
            "current": "handoff_current_work",
            "acknowledge": "acknowledge_handoff",
            "outcome": "record_task_outcome",
            "activate": "activate_handoff",
            "prepare": "prepare_handoff",
            "finalize": "finalize_handoff",
            "commit": "commit_handoff",
            "continue": "continue_handoff",
        },
        "experience": {
            "propose": "propose_experience",
            "generate": "generate_experience",
            "get": "get_experience",
        },
        "skill": {
            "propose": "propose_skill",
            "generate": "generate_skill",
            "get": "get_skill",
        },
        "external-skills": {
            "scan": "scan_external_skills",
            "list": "list_external_skills",
            "resolve": "resolve_external_skill",
            "import": "import_external_skill",
        },
        "review": {
            "list": "list_artifact_candidates",
            "get": "get_artifact_candidate",
            "approve": "approve_artifact_candidate",
            "reject": "reject_artifact_candidate",
            "revise": "revise_artifact_candidate",
        },
    }
    aliases = operation_aliases[group]
    action = args[0].lower() if args else ""
    if action not in aliases:
        return f"Usage: /pc {group} {{" + "|".join(aliases) + "}} PAYLOAD_JSON"
    operation = aliases[action]
    if operation in {"scan_external_skills", "list_artifact_candidates"} and not args[1:]:
        payload = {} if operation == "scan_external_skills" else {"status": "pending"}
        return json.dumps(request_operation(provider, operation, payload), ensure_ascii=False, indent=2)
    return operation_command(provider, operation, args[1:])


def status_command(provider: Any) -> str:
    result: dict[str, Any] = {
        "scope_id": provider._scope_id,
        "session_id": provider._session_id,
        "workstream_scope_id": provider._workstream_bound_scope or None,
    }
    if provider._client:
        for name, method_name in (("liveness", "get_liveness"), ("readiness", "get_readiness")):
            method = getattr(provider._client, method_name, None)
            if callable(method):
                try:
                    result[name] = method()
                except PowerContextError as error:
                    result[name] = {"error": str(error)}
    return json.dumps(result, ensure_ascii=False, indent=2)


def handle_slash_command(provider: Any, raw_args: str) -> str:  # noqa: C901
    """Handle the PowerContext ``/pc`` session command."""
    raw_parts = raw_args.strip().split(maxsplit=2)
    if len(raw_parts) == 3 and raw_parts[0].lower() in {
        "handoff",
        "experience",
        "skill",
        "external-skills",
        "review",
    }:
        try:
            return group_command(provider, raw_parts[0].lower(), [raw_parts[1], raw_parts[2]])
        except (PowerContextError, ValueError, TypeError) as error:
            logger.debug("PowerContext /pc command failed: %s", error)
            return tool_error(f"PowerContext operation failed: {error}")
    if len(raw_parts) == 3 and raw_parts[0].lower() == "call":
        try:
            return operation_command(provider, raw_parts[1], [raw_parts[2]])
        except (PowerContextError, ValueError, TypeError) as error:
            logger.debug("PowerContext /pc command failed: %s", error)
            return tool_error(f"PowerContext operation failed: {error}")
    try:
        command = raw_parts[0].lower() if raw_parts else ""
        if command in {"get", "revise", "retire"}:
            citation, remainder = _split_json_argument(raw_args, raw_parts[0], "citation")
            args = [command, citation, *shlex.split(remainder)]
        else:
            args = shlex.split(raw_args)
    except (ValueError, TypeError) as error:
        return tool_error(f"Invalid /pc arguments: {error}")
    if not args or args[0].lower() in {"help", "-h", "--help"}:
        return (
            "Usage: /pc {status|search|list|changes|get|remember|revise|retire|flush|stats|"
            "handoff|experience|skill|external-skills|review|workstream|trace|call} ...\n"
            "Advanced operations accept a JSON payload: /pc call OPERATION PAYLOAD_JSON\n"
            "Workstream binding: /pc workstream {status|bind SCOPE_ID|clear}"
        )
    command = args[0].lower()
    try:
        if command == "trace":
            return provider._trace_command(args[1:])
        if command == "status":
            return status_command(provider)
        if command in {"search", "list", "changes", "get", "remember", "revise", "retire", "flush", "stats"}:
            return memory_command(provider, args)
        if command == "workstream":
            return workstream_command(provider, args[1:])
        if command in {"handoff", "experience", "skill", "external-skills", "review"}:
            return group_command(provider, command, args[1:])
        if command == "call":
            if len(args) < 2:
                return tool_error("Usage: /pc call OPERATION [PAYLOAD_JSON]")
            return operation_command(provider, args[1], args[2:])
    except (PowerContextError, ValueError, TypeError) as error:
        logger.debug("PowerContext /pc command failed: %s", error)
        return tool_error(f"PowerContext operation failed: {error}")
    return tool_error(f"Unknown /pc command: {args[0]}")


def citation_properties() -> dict[str, Any]:
    return {
        "family": {"type": "string"},
        "artifact_id": {"type": "string"},
        "revision": {"type": "integer", "minimum": 1},
        "entry_id": {"type": "string"},
        "entry_version_id": {"type": "string"},
    }


def _operation_schema(
    name: str,
    description: str,
    properties: dict[str, Any] | None = None,
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "properties": properties or {},
            "required": list(required),
        },
    }


def get_tool_schemas() -> list[dict[str, Any]]:
    citation = citation_properties()
    schemas = [
        {
            "name": "powercontext_search_memory",
            "description": "Search relevant long-term memories stored in PowerContext.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural-language memory query."},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": DEFAULT_RETRIEVAL_LIMIT},
                    "mode": {"type": "string", "enum": ["auto", "fts", "vector", "hybrid"], "default": "auto"},
                },
                "required": ["query"],
            },
        },
        {
            "name": "powercontext_get_memory",
            "description": "Read one exact PowerContext memory entry from a search citation.",
            "parameters": {"type": "object", "properties": citation, "required": list(citation)},
        },
        {
            "name": "powercontext_remember",
            "description": "Save a durable memory to PowerContext when the user explicitly wants it remembered.",
            "parameters": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "description": "Memory kind, such as preference, decision, or fact."},
                    "text": {"type": "string", "description": "The durable memory text."},
                    "reason": {"type": "string", "description": "Why this memory should be retained."},
                },
                "required": ["kind", "text"],
            },
        },
        {
            "name": "powercontext_retire_memory",
            "description": "Retire an outdated or incorrect PowerContext memory entry without deleting its history.",
            "parameters": {
                "type": "object",
                "properties": {**citation, "reason": {"type": "string"}},
                "required": list(citation),
            },
        },
    ]

    json_object = {"type": "object", "additionalProperties": True}
    json_array = {"type": "array", "items": json_object}
    schemas.extend([
        _operation_schema(
            "powercontext_prepare_context",
            "Prepare bounded context for a query using the current PowerContext scope.",
            {"query": {"type": "string"}, "max_bytes": {"type": "integer", "minimum": 512, "maximum": 32768}},
            ("query",),
        ),
        _operation_schema(
            "powercontext_capture_source",
            "Capture a source explicitly into PowerContext. Do not include secrets.",
            {"source_id": {"type": "string"}, "content": {"type": "string"}, "metadata": json_object},
            ("source_id", "content"),
        ),
        _operation_schema(
            "powercontext_list_memory_entries",
            "List memory entries in the current scope; inactive entries are for audit only.",
            {"include_inactive": {"type": "boolean", "default": False}},
        ),
        _operation_schema(
            "powercontext_revise_memory_entry",
            "Revise one memory entry using its exact current citation.",
            {
                "citation": json_object,
                "kind": {"type": "string"},
                "text": {"type": "string"},
                "reason": {"type": "string"},
            },
            ("citation", "kind", "text"),
        ),
        _operation_schema(
            "powercontext_list_memory_changes",
            "List memory changes after an optional artifact revision.",
            {"since_revision": {"type": "integer", "minimum": 0}},
        ),
        _operation_schema(
            "powercontext_flush_memory", "Flush captured sources into durable memory when extraction is supported."
        ),
        _operation_schema(
            "powercontext_get_stats",
            "Read PowerContext usage and memory statistics for the current scope.",
            {"period": {"type": "string", "enum": ["today", "7d", "30d"]}},
        ),
        _operation_schema(
            "powercontext_create_work_contract",
            "Create a durable Work Contract for the current task.",
            {"source_id": {"type": "string"}, "contract": json_object},
            ("source_id", "contract"),
        ),
        _operation_schema(
            "powercontext_handoff_current_work",
            "Prepare a handoff record for the current work.",
            {"source_id": {"type": "string"}, "handoff": json_object},
            ("source_id", "handoff"),
        ),
        _operation_schema(
            "powercontext_acknowledge_handoff",
            "Record the receiving agent's acknowledgement of a handoff.",
            {
                "source_id": {"type": "string"},
                "receiver": {"type": "string"},
                "status": {"type": "string"},
                "selection": {"type": "string", "enum": ["prepared", "exact"]},
                "receiver_checks": json_object,
                "prepared": json_object,
                "revision": json_object,
                "message": {"type": "string"},
            },
            ("source_id", "receiver", "status", "selection"),
        ),
        _operation_schema(
            "powercontext_record_task_outcome",
            "Record a structured outcome for the current task.",
            {"source_id": {"type": "string"}, "outcome": json_object},
            ("source_id", "outcome"),
        ),
        _operation_schema(
            "powercontext_activate_handoff",
            "Activate a handoff at a source boundary.",
            {
                "boundary_source": json_object,
                "objective": {"type": "string"},
                "evidence": json_array,
                "max_bytes": {"type": "integer", "minimum": 512, "maximum": 32768},
            },
            ("boundary_source", "objective"),
        ),
        _operation_schema(
            "powercontext_prepare_handoff",
            "Prepare an inspectable handoff draft from exact evidence.",
            {
                "objective": {"type": "string"},
                "evidence": json_array,
                "max_bytes": {"type": "integer", "minimum": 512, "maximum": 32768},
            },
            ("objective", "evidence"),
        ),
        _operation_schema(
            "powercontext_finalize_handoff", "Finalize an inspected handoff draft.", {"draft": json_object}, ("draft",)
        ),
        _operation_schema(
            "powercontext_commit_handoff",
            "Commit a prepared handoff as a durable milestone.",
            {"handoff": json_object},
            ("handoff",),
        ),
        _operation_schema(
            "powercontext_continue_handoff",
            "Continue from a prepared or committed handoff.",
            {
                "selection": {"type": "string", "enum": ["prepared", "exact", "latest"]},
                "prepared": json_object,
                "revision": json_object,
            },
            ("selection",),
        ),
        _operation_schema(
            "powercontext_propose_experience",
            "Propose an Experience artifact candidate for later human review.",
            {
                "proposal": json_object,
                "source_refs": json_array,
                "artifact_refs": json_array,
                "target": json_object,
                "reason": {"type": "string"},
            },
            ("proposal", "source_refs", "artifact_refs"),
        ),
        _operation_schema(
            "powercontext_generate_experience",
            "Generate an Experience artifact candidate from exact references.",
            {
                "source_refs": json_array,
                "artifact_refs": json_array,
                "target": json_object,
                "reason": {"type": "string"},
            },
            ("source_refs", "artifact_refs"),
        ),
        _operation_schema(
            "powercontext_get_experience",
            "Read one Experience artifact by exact reference.",
            {"artifact": json_object},
            ("artifact",),
        ),
        _operation_schema(
            "powercontext_propose_skill",
            "Propose a Skill artifact candidate for later human review.",
            {
                "proposal": json_object,
                "source_refs": json_array,
                "artifact_refs": json_array,
                "target": json_object,
                "reason": {"type": "string"},
            },
            ("proposal", "source_refs", "artifact_refs"),
        ),
        _operation_schema(
            "powercontext_generate_skill",
            "Generate a Skill artifact candidate from exact references.",
            {
                "origin": {"type": "string", "enum": ["experience", "source", "usage"]},
                "source_refs": json_array,
                "artifact_refs": json_array,
                "target": json_object,
                "reason": {"type": "string"},
            },
            ("origin", "source_refs", "artifact_refs"),
        ),
        _operation_schema(
            "powercontext_get_skill",
            "Read one Skill artifact by exact reference.",
            {"artifact": json_object},
            ("artifact",),
        ),
        _operation_schema(
            "powercontext_scan_external_skills", "Scan configured external skill sources for available skills."
        ),
        _operation_schema(
            "powercontext_list_external_skills",
            "List discovered external skills.",
            {"include_unavailable": {"type": "boolean", "default": False}},
        ),
        _operation_schema(
            "powercontext_resolve_external_skill",
            "Resolve one external skill by id and fingerprint.",
            {"external_skill_id": {"type": "string"}, "fingerprint": {"type": "string"}},
            ("external_skill_id", "fingerprint"),
        ),
        _operation_schema(
            "powercontext_import_external_skill",
            "Import one verified external skill into the current scope.",
            {
                "external_skill_id": {"type": "string"},
                "fingerprint": {"type": "string"},
                "mode": {"type": "string", "enum": ["import", "fork"]},
                "reason": {"type": "string"},
            },
            ("external_skill_id", "fingerprint", "mode"),
        ),
        _operation_schema(
            "powercontext_list_artifact_candidates",
            "List Experience and Skill candidates awaiting review.",
            {
                "status": {"type": "string", "enum": ["pending", "approved", "rejected"]},
                "family": {"type": "string", "enum": ["experience", "skill"]},
                "cursor": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        ),
        _operation_schema(
            "powercontext_get_artifact_candidate",
            "Read one artifact candidate without changing its state.",
            {"candidate_id": {"type": "string"}},
            ("candidate_id",),
        ),
        _operation_schema(
            "powercontext_approve_artifact_candidate",
            "Approve an artifact candidate after explicit user review.",
            {"candidate_id": {"type": "string"}, "expected_version": {"type": "integer", "minimum": 1}},
            ("candidate_id", "expected_version"),
        ),
        _operation_schema(
            "powercontext_reject_artifact_candidate",
            "Reject an artifact candidate after explicit user review.",
            {
                "candidate_id": {"type": "string"},
                "expected_version": {"type": "integer", "minimum": 1},
                "reason": {"type": "string"},
            },
            ("candidate_id", "expected_version", "reason"),
        ),
        _operation_schema(
            "powercontext_revise_artifact_candidate",
            "Revise an artifact candidate while retaining its provenance.",
            {
                "candidate_id": {"type": "string"},
                "expected_version": {"type": "integer", "minimum": 1},
                "proposal": json_object,
                "source_refs": json_array,
                "artifact_refs": json_array,
                "target": json_object,
                "reason": {"type": "string"},
            },
            ("candidate_id", "expected_version", "proposal", "source_refs", "artifact_refs"),
        ),
    ])
    return schemas


def _search_memory_tool(provider: Any, args: dict[str, Any]) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return tool_error("query is required")
    limit = as_int(args.get("limit", DEFAULT_RETRIEVAL_LIMIT), DEFAULT_RETRIEVAL_LIMIT, minimum=1, maximum=50)
    mode = str(args.get("mode", "auto"))
    if mode not in {"auto", "fts", "vector", "hybrid"}:
        return tool_error("mode must be one of auto, fts, vector, hybrid")
    result = provider._client.search_memory(provider._scope_id, query[:8192], limit=limit, mode=mode)
    return json.dumps(result, ensure_ascii=False)


def _get_memory_tool(provider: Any, args: dict[str, Any]) -> str:
    citation = citation_from_args(args)
    return json.dumps(provider._client.get_memory_entry(provider._scope_id, citation), ensure_ascii=False)


def _remember_tool(provider: Any, args: dict[str, Any]) -> str:
    kind = str(args.get("kind", "")).strip()
    text = str(args.get("text", "")).strip()
    if not kind or not text:
        return tool_error("kind and text are required")
    result = provider._client.remember_memory(
        provider._scope_id,
        kind=kind[:128],
        text=text[:8192],
        reason=str(args.get("reason", "")).strip() or None,
    )
    return json.dumps(result, ensure_ascii=False)


def _retire_memory_tool(provider: Any, args: dict[str, Any]) -> str:
    citation = citation_from_args(args)
    result = provider._client.retire_memory_entry(
        provider._scope_id,
        citation,
        reason=str(args.get("reason", "")).strip() or None,
    )
    return json.dumps(result, ensure_ascii=False)


def _dispatch_tool_call(provider: Any, tool_name: str, args: dict[str, Any]) -> str:
    if tool_name == "powercontext_search_memory":
        return _search_memory_tool(provider, args)
    if tool_name == "powercontext_get_memory":
        return _get_memory_tool(provider, args)
    if tool_name == "powercontext_remember":
        return _remember_tool(provider, args)
    if tool_name in OPERATION_TOOL_MAP:
        result = request_operation(provider, OPERATION_TOOL_MAP[tool_name], args)
        return json.dumps(result, ensure_ascii=False)
    return _retire_memory_tool(provider, args)


def handle_tool_call(provider: Any, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
    if tool_name not in provider._tool_names:
        return tool_error(f"Unknown PowerContext tool: {tool_name}")
    if not provider._client or not provider._scope_id:
        return tool_error("PowerContext is not initialized for this session.")
    try:
        return _dispatch_tool_call(provider, tool_name, args)
    except (PowerContextError, ValueError, TypeError) as error:
        logger.debug("PowerContext tool %s failed: %s", tool_name, error)
        return tool_error(f"PowerContext operation failed: {error}")
