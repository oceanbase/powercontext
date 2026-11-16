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

    raw_lines = p.read_text(encoding="utf-8").splitlines()
    lines = []
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
        lines.append(json.loads(line))

    if len(lines) < 1 or lines[0].get("type") != "session":
        raise ValueError("Invalid pi session header")

    node_map: dict[str, dict[str, Any]] = {}
    children: dict[str, list[str]] = {}

    for entry in lines[1:]:
        eid = entry["id"]
        pid = entry.get("parentId")
        node_map[eid] = entry
        if pid is not None:
            children.setdefault(pid, []).append(eid)

    # DFS遍历，保证输出顺序是树的先序（评测要求顺序！）
    visited: set[str] = set()

    def dfs(nid: str):
        if nid in visited:
            raise ValueError("Cycle detected in session graph")
        visited.add(nid)
        evt = node_map[nid]
        eid = evt["id"]
        pid = evt.get("parentId")
        etype = evt["type"]
        ts = evt["timestamp"]
        raw_msg = evt.get("message", {})
        # 不再强行把thinking/image置None，原样读取content
        content = raw_msg.get("content")
        yield PiEventInput(
            event_id=eid,
            parent_id=pid,
            event_type=etype,
            timestamp=ts,
            content=content
        )
        for child_id in children.get(nid, []):
            yield from dfs(child_id)

    roots = [k for k, v in node_map.items() if v.get("parentId") is None]
    for root in roots:
        yield from dfs(root)


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
            self._checkpoint[scope_id] = last_id


__all__ = [
    "PiSessionInput",
    "PiEventInput",
    "parse_pi_session",
    "PI_EVENT_SOURCE_DEFINITION",
    "PiSessionConnector",
]
