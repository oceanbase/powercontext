from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from powercontext.connectors.base import ConnectorLifecycle, SourceDefinition, registry
from powercontext.core import ContextDocument


@dataclass
class PiSessionInput:
    file_path: str


@dataclass
class PiEventInput:
    event_id: str
    parent_id: str | None
    event_type: str
    timestamp: str
    content: str | None


PI_EVENT_SOURCE_DEFINITION = SourceDefinition(
    source_id="pi-event",
    version=1,
)


def parse_pi_session(file_path: str) -> Iterable[PiEventInput]:
    p = Path(file_path)
    if not p.exists():
        raise ValueError(f"Session file not found: {file_path}")

    lines = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) < 1 or lines[0].get("type") != "session":
        raise ValueError("Invalid pi session header")

    node_map: dict[str, dict[str, Any]] = {}
    children: dict[str, list[str]] = {}
    event_list: list[dict[str, Any]] = []

    for entry in lines[1:]:
        eid = entry["id"]
        pid = entry.get("parentId")
        etype = entry["type"]
        node_map[eid] = entry
        if pid is not None:
            children.setdefault(pid, []).append(eid)
        event_list.append(entry)

    # 校验：不能存在环
    visited: set[str] = set()
    def dfs(nid: str):
        if nid in visited:
            raise ValueError("Cycle detected in session graph")
        visited.add(nid)
        for cid in children.get(nid, []):
            dfs(cid)
    for root in [k for k, v in node_map.items() if v.get("parentId") is None]:
        dfs(root)

    # 逐条生成事件，过滤 thinking / image 不输出content
    for evt in event_list:
        eid = evt["id"]
        pid = evt.get("parentId")
        etype = evt["type"]
        ts = evt["timestamp"]
        raw_msg = evt.get("message", {})

        if etype in ("thinking", "image"):
            content = None
        else:
            content = raw_msg.get("content")
        yield PiEventInput(
            event_id=eid,
            parent_id=pid,
            event_type=etype,
            timestamp=ts,
            content=content
        )


@registry.register_connector("pi-session-history")
class PiSessionConnector(ConnectorLifecycle[PiSessionInput, PiEventInput]):
    def __init__(self):
        self._checkpoint: dict[str, str] = {}

    def checkpoint(self, scope_id: str) -> str | None:
        return self._checkpoint.get(scope_id)

    def ingest(self, input: PiSessionInput) -> Iterable[ContextDocument]:
        last_id: str | None = None
        for event in parse_pi_session(input.file_path):
            doc = ContextDocument(
                source_definition=PI_EVENT_SOURCE_DEFINITION,
                doc_id=event.event_id,
                parent_doc_id=event.parent_id,
                timestamp=event.timestamp,
                payload={
                    "event_type": event.event_type,
                    "content": event.content
                }
            )
            yield doc
            last_id = event.event_id
        if last_id is not None:
            self._checkpoint[input.file_path] = last_id


__all__ = [
    "PiSessionInput",
    "PiEventInput",
    "parse_pi_session",
    "PI_EVENT_SOURCE_DEFINITION",
    "PiSessionConnector",
]
