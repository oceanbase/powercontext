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

import difflib
import json
from contextlib import suppress
from typing import Any
from urllib.parse import urlencode

from fastapi import Request

from powercontext.builtin.artifacts.skill.models import SkillContent
from powercontext.builtin.artifacts.skill.package import SkillPackageError, comparable_skill_content
from powercontext.server.dashboard.api import DashboardAPI, ReadError, segment
from powercontext.server.dashboard.navigation import positive_revision
from powercontext.server.dashboard.pagination import PAGE_SIZE

ROLLBACK_FAMILIES = frozenset({"profile", "prompt", "experience", "skill", "handoff", "topic-memory"})
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
        return _skill_view(content)
    if family == "handoff":
        return {name: content.get(name) for name in _HANDOFF_FIELDS}
    return content


def _skill_view(content: dict[str, Any]) -> dict[str, Any]:
    skill = SkillContent.model_validate(content)
    # Historical names and sizes can be valid without satisfying modern package constraints.
    with suppress(SkillPackageError):
        skill = comparable_skill_content(skill)
    return skill.model_dump(mode="json")


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
    return citation


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
        left = _skill_view(left)
        right = _skill_view(right)
        return [_text_row(name, left.get(name), right.get(name)) for name in _SKILL_FIELDS]
    if family == "handoff":
        return [_text_row(name, left.get(name), right.get(name)) for name in _HANDOFF_FIELDS]
    if family == "atomic-memory":
        return [_text_row(name, left.get(name), right.get(name)) for name in ("kind", "text")]
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
    snapshot=None,
) -> dict[str, Any]:
    """Load one artifact's revision list and optional comparison."""

    scope = ctx["scope"]
    query = request.query_params
    invalid_compare = False
    try:
        compare = _revision_param(query.get("compare"))
    except ReadError:
        # A malformed comparison must not discard the selected revision or its history.
        compare = None
        invalid_compare = True
    try:
        if api.builtin is not None:
            result = await api.revision_history(
                scope,
                family,
                artifact_id,
                selected,
                cursor=query.get(cursor_key),
                compare=compare,
                snapshot=snapshot,
            )
        else:
            result = await _public_history(api, request, scope, family, artifact_id, selected, cursor_key, compare)
    except ReadError as error:
        return {
            "loaded": False,
            "error": error,
            "selected": selected,
            "head": selected,
            "family": family,
            "artifact_id": artifact_id,
            "same_content": False,
            "items": [],
            "next_url": None,
            "rows": None,
            "can_rollback": False,
            "compare_error": False,
        }
    values = result["values"]
    head_revision = result["head"]
    left, right = values.get(selected), values.get(compare)
    rows = None
    compare_error = invalid_compare or (compare is not None and (left is None or right is None))
    if compare is not None and compare != selected and not compare_error:
        rows = comparison(family, left["content"], right["content"])
    head = values.get(head_revision)
    same_content = (
        family in ROLLBACK_FAMILIES
        and left is not None
        and head is not None
        and canonical_view(family, left["content"]) == canonical_view(family, head["content"])
    )
    return {
        "loaded": True,
        "family": family,
        "artifact_id": artifact_id,
        "head": head_revision,
        "selected": selected,
        "items": result["items"],
        "next_cursor": result["next_cursor"],
        "next_url": ctx["link"](**{cursor_key: result["next_cursor"]}) if result["next_cursor"] else None,
        "can_rollback": family in ROLLBACK_FAMILIES and result["can_replace"],
        "same_content": same_content,
        "compare": compare,
        "compare_error": compare_error,
        "rows": rows,
        "error": query.get("rollback_error"),
    }


async def _public_history(api, request, scope, family, artifact_id, selected, cursor_key, compare):
    base = f"/v1/scopes/{segment(scope)}/artifacts/{segment(family)}/{segment(artifact_id)}"
    params = {"limit": str(PAGE_SIZE)}
    if request.query_params.get(cursor_key):
        params["cursor"] = request.query_params[cursor_key]
    page = await api.read(base + "/revisions?" + urlencode(params))
    head = await api.read(base)
    values = {head["revision"]: head}
    for revision in dict.fromkeys((selected, compare)):
        if revision is None or revision in values:
            continue
        try:
            values[revision] = await api.artifact_revision(scope, family, artifact_id, revision)
        except ReadError as error:
            if error.status != 404:
                raise
            continue
    return {
        **page,
        "head": head["revision"],
        "values": values,
        "can_replace": await _can_replace(request, api, scope, family, artifact_id),
    }


def _revision_param(value: str | None) -> int | None:
    return None if value is None else positive_revision(value)


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
