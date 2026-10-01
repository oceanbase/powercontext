# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Revision history, comparison, and rollback for the Dashboard."""

from __future__ import annotations

import asyncio
import difflib
import json
from typing import Any
from urllib.parse import urlencode

from fastapi import Request

from powercontext.server.dashboard.api import DashboardAPI, ReadError, segment
from powercontext.server.dashboard.pagination import PAGE_SIZE

ROLLBACK_FAMILIES = frozenset({"profile", "prompt", "experience", "skill", "handoff", "topic-memory"})
_MEMORY_COMPARE_LIMIT = 32
_SKILL_FIELDS = (
    "name",
    "description",
    "instructions",
    "validation",
    "package",
    "license",
    "compatibility",
    "metadata",
    "allowed_tools",
)
_EXPERIENCE_FIELDS = ("situation", "action", "outcome", "lesson", "failure")
_HANDOFF_FIELDS = ("objective", "state", "disposition", "next_action", "omissions")
_TEXT_FIELDS = {"instructions", "detail", "objective", "situation", "action", "outcome", "lesson", "content"}


def canonical_view(family: str, content: dict[str, Any]) -> object:
    """Return the family content used to decide whether a rollback would change the head."""

    if family == "profile":
        return content.get("content")
    if family == "topic-memory":
        return (content.get("title"), content.get("summary"), content.get("detail"))
    if family == "prompt":
        return (content.get("instructions"), content.get("demonstrations"))
    if family == "experience":
        return {name: content.get(name) for name in _EXPERIENCE_FIELDS}
    if family == "skill":
        return {name: content.get(name) for name in _SKILL_FIELDS}
    if family == "handoff":
        return {name: content.get(name) for name in _HANDOFF_FIELDS}
    if family == "memory":
        return content.get("changes")
    return content


def replace_body(family: str, content: dict[str, Any], source_revision: int, reason: str) -> dict[str, Any]:
    """Build a conditional replace that restores one stored revision."""

    if family == "profile":
        payload: dict[str, Any] = {"content": content.get("content")}
    elif family == "topic-memory":
        payload = {name: content[name] for name in ("title", "summary", "detail")}
    elif family == "handoff":
        payload = _handoff_payload(content)
    elif family == "experience":
        payload = {name: content.get(name) for name in _EXPERIENCE_FIELDS if name in content}
    elif family == "skill":
        required = {"name", "description", "instructions", "validation"}
        payload = {name: content.get(name) for name in _SKILL_FIELDS if name in content or name in required}
    else:
        payload = content
    return {"content": payload, "restored_from_revision": source_revision, "reason": reason}


def _handoff_payload(content: dict[str, Any]) -> dict[str, Any]:
    """Return handoff content in the replace contract.

    Stored revisions cite sources with ``source_type``. The replace contract names that field ``name``.
    """

    payload = {
        "objective": content.get("objective"),
        "state": [_handoff_statement(item) for item in content.get("state") or []],
        "disposition": content.get("disposition"),
        "next_action": None if content.get("next_action") is None else _handoff_statement(content["next_action"]),
        "omissions": content.get("omissions") or [],
    }
    if "schema" in content:
        payload["schema"] = content["schema"]
    return payload


def _handoff_statement(statement: dict[str, Any]) -> dict[str, Any]:
    return {
        "text": statement.get("text"),
        "citations": [_handoff_citation(item) for item in statement.get("citations") or []],
    }


def _handoff_citation(citation: dict[str, Any]) -> dict[str, Any]:
    kind = citation.get("kind")
    if kind == "source":
        ref = citation.get("source_ref") or {}
        return {
            "kind": "source",
            "source_ref": {"name": ref.get("name") or ref.get("source_type"), "source_id": ref.get("source_id")},
        }
    if kind == "artifact":
        ref = citation.get("artifact_ref") or {}
        return {
            "kind": "artifact",
            "artifact_ref": {
                "family": ref.get("family"),
                "artifact_id": ref.get("artifact_id"),
                "revision": ref.get("revision"),
            },
        }
    if kind == "memory":
        memory = citation.get("memory_citation") or {}
        memory_ref = memory.get("memory_ref") or {}
        return {
            "kind": "memory",
            "memory_citation": {
                "memory_ref": {
                    "family": memory_ref.get("family"),
                    "artifact_id": memory_ref.get("artifact_id"),
                    "revision": memory_ref.get("revision"),
                },
                "entry_id": memory.get("entry_id"),
                "entry_version_id": memory.get("entry_version_id"),
            },
        }
    return citation


def _active_manifest(content: dict[str, Any]) -> dict[str, dict[str, Any]]:
    manifest = content.get("manifest") if isinstance(content, dict) else None
    entries = manifest.get("entries") if isinstance(manifest, dict) else None
    selected = {}
    for item in entries or []:
        if not isinstance(item, dict) or item.get("state", "active") != "active":
            continue
        entry_id = item.get("entry_id")
        version_id = item.get("entry_version_id")
        if isinstance(entry_id, str) and isinstance(version_id, str):
            selected[entry_id] = item
    return selected


async def _entry_text(
    api: DashboardAPI, scope: str, artifact_id: str, revision: int, item: dict[str, Any] | None
) -> str:
    if item is None:
        return ""
    entry = await api.read(
        "/v1/memory/entries/get",
        {
            "scope_id": scope,
            "citation": {
                "memory_ref": {"family": "memory", "artifact_id": artifact_id, "revision": revision},
                "entry_id": item["entry_id"],
                "entry_version_id": item["entry_version_id"],
            },
        },
    )
    text = entry.get("text")
    return text if isinstance(text, str) else ""


async def _memory_text_rows(
    api: DashboardAPI,
    scope: str,
    artifact_id: str,
    left_revision: int,
    left: dict[str, Any],
    right_revision: int,
    right: dict[str, Any],
) -> tuple[list[dict[str, Any]], bool]:
    """Diff note text for entries whose exact version differs between two Memory revisions."""

    left_entries = _active_manifest(left)
    right_entries = _active_manifest(right)
    changed = [
        entry_id
        for entry_id in sorted(set(left_entries) | set(right_entries))
        if _version(left_entries, entry_id) != _version(right_entries, entry_id)
    ]
    if not changed:
        return [_text_row("content", "", "")], False
    shown = changed[:_MEMORY_COMPARE_LIMIT]
    texts = await asyncio.gather(
        *(
            asyncio.gather(
                _entry_text(api, scope, artifact_id, left_revision, left_entries.get(entry_id)),
                _entry_text(api, scope, artifact_id, right_revision, right_entries.get(entry_id)),
            )
            for entry_id in shown
        )
    )
    rows = [
        _text_row(entry_id, left_text, right_text)
        for entry_id, (left_text, right_text) in zip(shown, texts, strict=True)
    ]
    return rows, len(changed) > _MEMORY_COMPARE_LIMIT


def _version(entries: dict[str, dict[str, Any]], entry_id: str) -> str | None:
    item = entries.get(entry_id)
    version = None if item is None else item.get("entry_version_id")
    return version if isinstance(version, str) else None


def comparison(family: str, left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    """Readable rows for two stored content objects. The left side is the opened revision."""

    if family == "profile":
        return [_text_row("content", left.get("content"), right.get("content"))]
    if family == "topic-memory":
        return [_text_row(name, left.get(name), right.get(name)) for name in ("title", "summary", "detail")]
    if family == "prompt":
        return [
            _text_row("instructions", left.get("instructions"), right.get("instructions")),
            _text_row("demonstrations", left.get("demonstrations"), right.get("demonstrations")),
        ]
    if family == "experience":
        return [_text_row(name, left.get(name), right.get(name)) for name in _EXPERIENCE_FIELDS]
    if family == "skill":
        return [_text_row(name, left.get(name), right.get(name)) for name in _SKILL_FIELDS]
    if family == "handoff":
        return [_text_row(name, left.get(name), right.get(name)) for name in _HANDOFF_FIELDS]
    return [_text_row("content", left, right)]


async def load_history(
    api: DashboardAPI,
    request: Request,
    ctx: dict[str, Any],
    *,
    family: str,
    artifact_id: str,
    selected: int,
    cursor_key: str,
) -> dict[str, Any] | None:
    """Load one artifact's revision list and optional comparison."""

    scope = ctx["scope"]
    base = f"/v1/scopes/{segment(scope)}/artifacts/{segment(family)}/{segment(artifact_id)}"
    params = {"limit": str(PAGE_SIZE)}
    if request.query_params.get(cursor_key):
        params["cursor"] = request.query_params[cursor_key]
    try:
        page = await api.read(base + "/revisions?" + urlencode(params))
        head = await api.read(base)
    except ReadError:
        return None
    items = page["items"]
    head_revision = int(head["revision"])
    compare_raw = request.query_params.get("compare")
    compare = _revision_param(compare_raw)
    rows = None
    compare_error = False
    compare_truncated = False
    if compare is not None and compare != selected:
        try:
            left = await api.artifact_revision(scope, family, artifact_id, selected)
            right = await api.artifact_revision(scope, family, artifact_id, compare)
            if family == "memory":
                rows, compare_truncated = await _memory_text_rows(
                    api, scope, artifact_id, selected, left["content"], compare, right["content"]
                )
            else:
                rows = comparison(family, left["content"], right["content"])
        except ReadError:
            rows = None
            compare_error = True
    same_content = False
    if family in ROLLBACK_FAMILIES and selected != head_revision:
        try:
            opened = await api.artifact_revision(scope, family, artifact_id, selected)
            same_content = canonical_view(family, opened["content"]) == canonical_view(family, head["content"])
        except ReadError:
            same_content = False
    return {
        "family": family,
        "artifact_id": artifact_id,
        "head": head_revision,
        "selected": selected,
        "items": items,
        "pager_key": cursor_key,
        "next_cursor": page.get("next_cursor"),
        "next_url": (
            ctx["link"](**{cursor_key: page["next_cursor"]})
            if page.get("next_cursor") and ctx.get("link") is not None
            else None
        ),
        "can_rollback": family in ROLLBACK_FAMILIES and await _can_replace(request, api, scope, family, artifact_id),
        "same_content": same_content,
        "compare": compare,
        "compare_error": compare_error,
        "compare_truncated": compare_truncated,
        "rows": rows,
        "error": request.query_params.get("rollback_error"),
    }


def _revision_param(value: str | None) -> int | None:
    if value is None or not value.isdigit() or value.startswith("0"):
        return None
    number = int(value)
    return number if number >= 1 else None


async def _can_replace(request: Request, api: DashboardAPI, scope: str, family: str, artifact_id: str) -> bool:
    # Disabled mode has no access check. Treating that 503 as a denial hides rollback
    # from the local deployment that is otherwise allowed to write.
    if request.app.state.access_mode == "disabled":
        return True
    if family in {"prompt", "topic-memory"}:
        requirement = {"action": "scope.admin", "resource": {"type": "scope", "scope_id": scope}}
    else:
        requirement = {
            "action": "artifact.write",
            "resource": {
                "type": "artifact",
                "scope_id": scope,
                "identity": {"family": family, "artifact_id": artifact_id},
                "selector": None,
            },
        }
    try:
        result = await api.read("/v1/access/check", {"match": "all", "requirements": [requirement]})
    except ReadError:
        return False
    return result.get("allowed") is True


def _text_row(name: str, left: object, right: object) -> dict[str, Any]:
    left_text = left if isinstance(left, str) else json.dumps(left, ensure_ascii=False, indent=2, sort_keys=True)
    right_text = right if isinstance(right, str) else json.dumps(right, ensure_ascii=False, indent=2, sort_keys=True)
    diff = []
    if left_text != right_text:
        for line in difflib.ndiff(left_text.splitlines(), right_text.splitlines()):
            if line.startswith("? "):
                continue
            kind = {"+": "add", "-": "remove"}.get(line[:1], "same")
            diff.append({"kind": kind, "text": line[2:] if line[:2] in {"+ ", "- ", "  "} else line})
    return {"name": name, "diff": diff, "changed": left_text != right_text}
