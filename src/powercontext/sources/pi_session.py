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

"""Capture the raw visible events of one selected Pi session branch.

A Pi session archive is an append-only JSONL node graph: the selected leaf and
its ancestors form one history path. This module reads that path, keeps every
visible event verbatim as its own ``CAPTURED`` Source, and resumes incremental
collection from a durable checkpoint. Compaction summaries are evidence too, so
nothing that precedes them is dropped.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import quote

from pydantic import BaseModel, JsonValue, PrivateAttr, field_validator

from powercontext.limits import MAX_SOURCE_ID_LENGTH
from powercontext.sources import TEXT_EVIDENCE_PROJECTION_KEY, AdapterSourceDefinition, TextEvidence
from powercontext.sources.connectors import ConnectorRunCompletion, ConnectorRunSession, ConnectorRunStatus
from powercontext.sources.models import Source, SourceMaterialization

PI_EVENT_SOURCE_NAME = "pi-event"
PI_EVENT_SOURCE_VERSION = "1"
PI_SESSION_CONNECTOR_NAME = "pi-session-history"
PI_SESSION_CONNECTOR_VERSION = "1"
PI_SESSION_FORMAT_VERSION = 3
PI_EVENT_SOURCE_ID_PREFIX = "pi-event"
_PI_CHECKPOINT_VERSION = 1

_CONTROL_EVENT_TYPE = "message"


class PiSessionInput(BaseModel):
    """One offline Pi session archive and the branch selected from it."""

    content: bytes
    leaf_id: str | None = None


class PiEventInput(BaseModel):
    """One visible event with its complete original record."""

    session_id: str
    event_id: str
    parent_id: str | None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]

    @field_validator("session_id", "event_id", "event_type", "role", "content")
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("pi event fields must not be blank")  # noqa: TRY003
        return value


class PiEventSource(Source):
    """A captured session event that keeps its full body and identity."""

    source_id: str
    session_id: str
    event_id: str
    parent_id: str | None = None
    event_type: str
    role: str
    content: str
    record: dict[str, JsonValue]


def pi_event_source_id(session_id: str, event_id: str) -> str:
    """Build the stable identity of one session event."""

    _require_text("session_id", session_id)
    _require_text("event_id", event_id)
    source_id = f"{PI_EVENT_SOURCE_ID_PREFIX}:{quote(session_id, safe='')}:{quote(event_id, safe='')}"
    if len(source_id) > MAX_SOURCE_ID_LENGTH:
        raise ValueError(f"pi event identity must not exceed {MAX_SOURCE_ID_LENGTH} characters")  # noqa: TRY003
    return source_id


def parse_pi_session(value: PiSessionInput) -> tuple[PiEventInput, ...]:
    """Return the visible events of the selected branch, ordered root to leaf."""

    session_id, records = _parse_archive(value.content)
    order, parents = _index_nodes(records)
    leaf_id = value.leaf_id
    if leaf_id is None:
        if not order:
            raise ValueError("pi session archive contains no events")  # noqa: TRY003
        leaf_id = order[-1]
    elif leaf_id not in parents:
        raise ValueError(f"leaf {leaf_id!r} is not present in the session archive")  # noqa: TRY003

    chain = _ancestor_chain(leaf_id, parents)
    events: list[PiEventInput] = []
    for node_id in chain:
        record = parents[node_id][1]
        visible = _visible_text(record)
        if visible is None:
            continue
        role, body = visible
        events.append(
            PiEventInput(
                session_id=session_id,
                event_id=node_id,
                parent_id=parents[node_id][0],
                event_type=str(record.get("type")),
                role=role,
                content=f"{role}: {body}",
                record=record,
            )
        )
    return tuple(events)


class PiEventAdapter:
    """Resolve one visible event into its captured Source and read it back."""

    input_class = PiEventInput
    name = PI_EVENT_SOURCE_NAME
    source_class = PiEventSource

    async def resolve(self, value: PiEventInput, /) -> PiEventSource:
        source_id = pi_event_source_id(value.session_id, value.event_id)
        return PiEventSource(
            name=source_id,
            definition_version=PI_EVENT_SOURCE_VERSION,
            materialization=SourceMaterialization.CAPTURED,
            source_id=source_id,
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


class PiEventTextEvidenceProjection:
    """Expose the complete event body as standard textual evidence."""

    name = TEXT_EVIDENCE_PROJECTION_KEY.name
    version = TEXT_EVIDENCE_PROJECTION_KEY.version
    source_class = PiEventSource
    output_class: type[BaseModel] = TextEvidence

    def project(self, source: PiEventSource, /) -> TextEvidence:
        return TextEvidence(
            source_type=PI_EVENT_SOURCE_NAME,
            source_id=source.source_id,
            content=source.content,
            metadata={
                "session_id": source.session_id,
                "event_id": source.event_id,
                "parent_id": source.parent_id,
                "event_type": source.event_type,
                "role": source.role,
            },
        )


PI_EVENT_SOURCE_ADAPTER = PiEventAdapter()
PI_EVENT_SOURCE_DEFINITION = AdapterSourceDefinition(
    PI_EVENT_SOURCE_ADAPTER,
    version=PI_EVENT_SOURCE_VERSION,
    projections=(PiEventTextEvidenceProjection(),),
)


class PiSessionConnector:
    """Import one selected branch incrementally and resume after restarts."""

    name = PI_SESSION_CONNECTOR_NAME
    version = PI_SESSION_CONNECTOR_VERSION
    source_definitions = frozenset({PI_EVENT_SOURCE_NAME})

    def __init__(self, path: str | Path, leaf_id: str | None = None, /) -> None:
        self._path = Path(path)
        self._leaf_id = leaf_id
        self._imported: dict[str, str] = {}
        self._session_id: str | None = None

    @property
    def imported(self) -> Mapping[str, str]:
        return dict(self._imported)

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def run(self, session: ConnectorRunSession, /) -> ConnectorRunCompletion:
        events = parse_pi_session(PiSessionInput(content=self._path.read_bytes(), leaf_id=self._leaf_id))
        self._load_checkpoint(session.checkpoint)

        pending: list[PiEventInput] = []
        for event in events:
            digest = _record_digest(event.record)
            known = self._imported.get(event.event_id)
            if known is not None:
                if known != digest:
                    raise ValueError(f"pi event {event.event_id!r} was rewritten after it was imported")  # noqa: TRY003
                continue
            pending.append(event)

        for event in pending:
            await session.submit(event.event_id, PI_EVENT_SOURCE_NAME, event)
            self._imported[event.event_id] = _record_digest(event.record)

        checkpoint: JsonValue = session.checkpoint if not pending else self._checkpoint()
        return ConnectorRunCompletion(status=ConnectorRunStatus.COMPLETE, checkpoint=checkpoint)

    def _load_checkpoint(self, checkpoint: JsonValue | None) -> None:
        self._imported = {}
        self._session_id = None
        if checkpoint is None:
            return
        if not isinstance(checkpoint, dict):
            raise ValueError("pi connector checkpoint must be an object")  # noqa: TRY003
        version = checkpoint.get("version")
        if version != _PI_CHECKPOINT_VERSION:
            raise ValueError(f"unsupported pi connector checkpoint version {version!r}")  # noqa: TRY003
        session_id = checkpoint.get("session_id")
        if session_id is not None and not isinstance(session_id, str):
            raise ValueError("pi connector checkpoint session_id must be a string")  # noqa: TRY003
        events = checkpoint.get("events", {})
        if not isinstance(events, dict):
            raise ValueError("pi connector checkpoint events must be an object")  # noqa: TRY003
        imported: dict[str, str] = {}
        for event_id, digest in events.items():
            if not isinstance(event_id, str) or not isinstance(digest, str):
                raise ValueError("pi connector checkpoint entries must be strings")  # noqa: TRY003
            imported[event_id] = digest
        self._session_id = session_id
        self._imported = imported

    def _checkpoint(self) -> JsonValue:
        return {
            "version": _PI_CHECKPOINT_VERSION,
            "session_id": self._session_id,
            "events": dict(self._imported),
        }


def _parse_archive(content: bytes) -> tuple[str, list[dict[str, JsonValue]]]:
    if not isinstance(content, (bytes, bytearray, memoryview)):
        raise ValueError("pi session content must be bytes")  # noqa: TRY003
    try:
        text = bytes(content).decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("pi session content must be valid UTF-8") from error

    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("pi session archive must not be empty")  # noqa: TRY003

    records: list[dict[str, JsonValue]] = []
    for index, line in enumerate(lines):
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"pi session line {index + 1} is not valid JSON") from error
        if not isinstance(decoded, dict):
            raise ValueError(f"pi session line {index + 1} must be a JSON object")  # noqa: TRY003
        records.append(decoded)

    header = records[0]
    if header.get("type") != "session":
        raise ValueError("pi session archive must start with a session header")  # noqa: TRY003
    if header.get("version") != PI_SESSION_FORMAT_VERSION:
        raise ValueError("pi session header must declare version 3")  # noqa: TRY003
    session_id = header.get("id")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("pi session header must declare a non-empty id")  # noqa: TRY003
    return session_id, records[1:]


def _index_nodes(
    records: Sequence[dict[str, JsonValue]],
) -> tuple[list[str], dict[str, tuple[str | None, dict[str, JsonValue]]]]:
    order: list[str] = []
    parents: dict[str, tuple[str | None, dict[str, JsonValue]]] = {}
    for record in records:
        node_id = record.get("id")
        if not isinstance(node_id, str) or not node_id.strip():
            raise ValueError("every pi session event must declare a non-empty id")  # noqa: TRY003
        if node_id in parents:
            raise ValueError(f"duplicate pi session event id {node_id!r}")  # noqa: TRY003
        parent_id = record.get("parentId")
        if parent_id is not None:
            if not isinstance(parent_id, str):
                raise ValueError("pi session parentId must be a string or null")  # noqa: TRY003
            if parent_id not in parents:
                raise ValueError(f"pi session event {node_id!r} references unknown parent {parent_id!r}")  # noqa: TRY003
        parents[node_id] = (parent_id, record)
        order.append(node_id)
    return order, parents


def _ancestor_chain(leaf_id: str, parents: Mapping[str, tuple[str | None, dict[str, JsonValue]]]) -> list[str]:
    chain: list[str] = []
    seen: set[str] = set()
    current: str | None = leaf_id
    while current is not None:
        if current in seen:
            raise ValueError("pi session branch contains a cycle")  # noqa: TRY003
        seen.add(current)
        chain.append(current)
        current = parents[current][0]
    chain.reverse()
    return chain


def _visible_text(record: Mapping[str, JsonValue]) -> tuple[str, str] | None:
    event_type = record.get("type")
    if event_type == "message":
        message = record.get("message")
        if not isinstance(message, dict):
            return None
        role = message.get("role")
        if not isinstance(role, str) or not role:
            return None
        if role == "toolResult":
            body = _tool_result_body(message)
            return (role, body) if body else None
        if role == "bashExecution":
            body = _bash_body(message)
            return (role, body) if body else None
        body = _message_body(message.get("content"))
        return (role, body) if body else None
    if event_type in {"compaction", "branch_summary"}:
        summary = record.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            return None
        return (str(event_type), summary)
    if event_type == "custom_message":
        body = _message_body(record.get("content"))
        return ("custom", body) if body else None
    return None


def _message_body(content: JsonValue) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        elif block_type == "toolCall":
            parts.append(_tool_call_body(block))
    return "\n".join(parts)


def _tool_call_body(block: Mapping[str, JsonValue]) -> str:
    name = block.get("name")
    call_id = block.get("id")
    arguments = block.get("arguments")
    if arguments is None:
        arguments = {}
    return (
        f"[tool_call {name if isinstance(name, str) else ''} {call_id if isinstance(call_id, str) else ''}] "
        f"{json.dumps(arguments, ensure_ascii=False, sort_keys=True, separators=(',', ':'))}"
    )


def _tool_result_body(message: Mapping[str, JsonValue]) -> str:
    tool_name = message.get("toolName")
    call_id = message.get("toolCallId")
    text = _message_body(message.get("content"))
    return (
        f"[tool_result {tool_name if isinstance(tool_name, str) else ''} "
        f"{call_id if isinstance(call_id, str) else ''}] {text}"
    )


def _bash_body(message: Mapping[str, JsonValue]) -> str:
    command = message.get("command")
    output = message.get("output")
    command_text = command if isinstance(command, str) else ""
    output_text = output if isinstance(output, str) else ""
    if not command_text and not output_text:
        return ""
    return f"$ {command_text}\n{output_text}"


def _record_digest(record: Mapping[str, JsonValue]) -> str:
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _require_text(field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")  # noqa: TRY003


def _iter_lines() -> Iterator[str]:  # pragma: no cover - retained for debugging helpers
    yield from ()


__all__ = [
    "PI_EVENT_SOURCE_DEFINITION",
    "PI_EVENT_SOURCE_NAME",
    "PiEventInput",
    "PiEventSource",
    "PiSessionConnector",
    "PiSessionInput",
    "parse_pi_session",
    "pi_event_source_id",
]
