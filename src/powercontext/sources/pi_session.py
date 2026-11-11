"""Pi v3 session history parsing and incremental capture."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from powercontext.sources.definitions import AdapterSourceDefinition
from powercontext.sources.connectors import (
    ConnectorRunCompletion,
    ConnectorRunSession,
    ConnectorRunStatus,
    ConnectorSubmissionStatus,
)
from powercontext.sources.models import Source, SourceMaterialization
from powercontext.sources.projections import TEXT_EVIDENCE_PROJECTION_KEY, TextEvidence


class PiSessionInput(BaseModel):
    model_config = ConfigDict(frozen=True)
    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    model_config = ConfigDict(frozen=True)
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]


class PiEventSource(Source):
    materialization: SourceMaterialization = SourceMaterialization.CAPTURED
    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, Any]


def _compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _visible_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    pieces: list[str] = []
    for block in value:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and isinstance(block.get("text"), str):
            pieces.append(block["text"])
        elif block.get("type") == "toolCall":
            pieces.append(
                f"[tool_call {block.get('name', '')} {block.get('id', '')}] "
                f"{_compact_json(block.get('arguments', {}))}"
            )
    return "\n".join(piece for piece in pieces if piece != "")


def _event_body(record: dict[str, Any]) -> tuple[str, str] | None:
    kind = record.get("type")
    if kind == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        role = message.get("role")
        if not isinstance(role, str) or not role:
            return None
        if role == "bashExecution":
            command = message.get("command", "")
            output = message.get("output", "")
            body = f"$ {command}"
            if output:
                body += f"\n{output}"
            return role, body
        if role == "toolResult":
            body = f"[tool_result {message.get('toolName', '')} {message.get('toolCallId', '')}]"
            text = _visible_text(message.get("content", ""))
            return role, f"{body} {text}" if text else body
        body = _visible_text(message.get("content", ""))
        return (role, body) if body else None
    if kind in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        return (kind, summary) if isinstance(summary, str) and summary else None
    if kind == "custom_message":
        body = _visible_text(record.get("content", ""))
        return ("custom", body) if body else None
    return None


def _parse_archive(
    value: PiSessionInput,
) -> tuple[str, dict[str, dict[str, Any]], tuple[dict[str, Any], ...]]:
    try:
        decoded = value.content.decode("utf-8-sig")
    except (UnicodeDecodeError, AttributeError) as error:
        raise ValueError("Pi session must be UTF-8 JSONL") from error
    lines = [line for line in decoded.splitlines() if line.strip()]
    if not lines:
        raise ValueError("Pi session is empty")
    try:
        header = json.loads(lines[0])
    except (json.JSONDecodeError, TypeError) as error:
        raise ValueError("Invalid Pi session header JSON") from error
    if (
        not isinstance(header, dict)
        or header.get("type") != "session"
        or header.get("version") != 3
        or not isinstance(header.get("id"), str)
        or not header["id"].strip()
    ):
        raise ValueError("First record must be a valid Pi v3 session header")

    session_id = header["id"]
    nodes: dict[str, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    for number, line in enumerate(lines[1:], start=2):
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, TypeError) as error:
            raise ValueError(f"Invalid JSON on Pi session line {number}") from error
        if not isinstance(record, dict):
            raise ValueError(f"Pi session line {number} must be an object")
        event_id = record.get("id")
        if not isinstance(event_id, str) or not event_id.strip():
            raise ValueError(f"Pi session line {number} has no valid event id")
        if event_id in nodes:
            raise ValueError(f"Duplicate Pi event id: {event_id!r}")
        if "parentId" not in record:
            raise ValueError(f"Pi event {event_id!r} has no parentId")
        parent_id = record["parentId"]
        if parent_id is not None and not isinstance(parent_id, str):
            raise ValueError(f"Pi event {event_id!r} has invalid parentId")
        if parent_id is not None and parent_id not in nodes:
            raise ValueError(f"Pi event {event_id!r} references missing or later parent {parent_id!r}")
        nodes[event_id] = record
        ordered.append(record)

    if not ordered:
        raise ValueError("Pi session contains no event nodes")
    leaf = value.leaf_id if value.leaf_id is not None else ordered[-1]["id"]
    if leaf not in nodes:
        raise ValueError(f"Selected Pi leaf {leaf!r} does not exist")
    return session_id, nodes, tuple(ordered)


def parse_pi_session(value: PiSessionInput, /) -> tuple[PiEventInput, ...]:
    if not isinstance(value, PiSessionInput):
        raise ValueError("parse_pi_session expects PiSessionInput")
    session_id, nodes, ordered = _parse_archive(value)
    leaf_id = value.leaf_id if value.leaf_id is not None else ordered[-1]["id"]
    path: list[dict[str, Any]] = []
    current: str | None = leaf_id
    while current is not None:
        record = nodes[current]
        path.append(record)
        current = record["parentId"]
    path.reverse()

    result: list[PiEventInput] = []
    for record in path:
        visible = _event_body(record)
        if visible is None:
            continue
        role, body = visible
        result.append(PiEventInput(
            session_id=session_id,
            event_id=record["id"],
            parent_id=record["parentId"],
            event_type=record["type"],
            role=role,
            content=f"{role}: {body}",
            record=record,
        ))
    return tuple(result)


class PiEventTextEvidenceProjection:
    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type="pi-event",
            source_id=source.name,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


class PiEventSourceAdapter:
    input_class = PiEventInput
    name = "pi-event"
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        identity = hashlib.sha256(f"{value.session_id}\0{value.event_id}".encode()).hexdigest()
        return PiEventSource(
            name=f"pi-event:{identity}",
            definition_version="1",
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            event_id=value.event_id,
            parent_id=value.parent_id,
            event_type=value.event_type,
            role=value.role,
            content=value.content,
            record=value.record,
        )

    async def read(self, source: PiEventSource, /) -> PiEventInput:
        return PiEventInput(
            session_id=source.session_id,
            event_id=source.event_id,
            parent_id=source.parent_id,
            event_type=source.event_type,
            role=source.role,
            content=source.content,
            record=source.record,
        )


PI_EVENT_SOURCE_ADAPTER = PiEventSourceAdapter()
PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    adapter=PI_EVENT_SOURCE_ADAPTER,
    version="1",
    projections=(PiEventTextEvidenceProjection(),),
)


class PiSessionConnector:
    name = "pi-session-history"
    version = "1"
    source_definitions = frozenset({"pi-event"})

    def __init__(self, path: str | Path, leaf_id: str | None = None) -> None:
        self.path = Path(path)
        self.leaf_id = leaf_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        raw = self.path.read_bytes()
        parsed = PiSessionInput(content=raw, leaf_id=self.leaf_id)
        session_id, all_nodes, _ = _parse_archive(parsed)
        events = parse_pi_session(parsed)

        if session.checkpoint is None:
            imported: dict[str, str] = {}
        else:
            checkpoint = session.checkpoint
            if not isinstance(checkpoint, dict) or checkpoint.get("session_id") != session_id:
                raise ValueError("Invalid Pi connector checkpoint or changed session id")
            saved = checkpoint.get("events")
            if not isinstance(saved, dict):
                raise ValueError("Invalid Pi connector checkpoint event map")
            if not all(isinstance(k, str) and isinstance(v, str) for k, v in saved.items()):
                raise ValueError("Invalid Pi connector checkpoint entry")
            imported = dict(saved)

        # Check all previously imported IDs, including those on inactive branches.
        for event_id, old_digest in imported.items():
            record = all_nodes.get(event_id)
            if record is None:
                raise ValueError(f"Previously captured Pi event {event_id!r} disappeared")
            digest = hashlib.sha256(_compact_json(record).encode("utf-8")).hexdigest()
            if digest != old_digest:
                raise ValueError(f"Previously captured Pi event {event_id!r} was modified")

        next_imported = dict(imported)
        for event in events:
            digest = hashlib.sha256(_compact_json(event.record).encode("utf-8")).hexdigest()
            if event.event_id in imported:
                if imported[event.event_id] != digest:
                    raise ValueError(f"Previously captured Pi event {event.event_id!r} was modified")
                continue
            outcome = await session.submit(event.event_id, "pi-event", event)
            if outcome.status is ConnectorSubmissionStatus.ACCEPTED:
                next_imported[event.event_id] = digest

        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE,
            checkpoint={"session_id": session_id, "events": next_imported},
        )


__all__ = [
    "PI_EVENT_SOURCE_ADAPTER",
    "PI_EVENT_SOURCE_DEFINITION",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
]
