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

"""Unified review presentation over the two public candidate resources."""

from __future__ import annotations

import difflib
import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

from fastapi import Request

from powercontext.server.dashboard.api import DashboardAPI, ReadError, segment
from powercontext.server.dashboard.review_reading import candidate_title, content_fields, family_label, present_detail

KINDS = {"artifact", "tag"}


def formatted(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


async def load_review(api: DashboardAPI, request: Request, ctx: dict[str, Any]) -> None:
    kind = request.query_params.get("resource_type", "all")
    status = request.query_params.get("review_status", "pending")
    if kind not in {"all", *KINDS} or status not in {"pending", "approved", "rejected"}:
        raise ReadError(422, "invalid_request")
    view: dict[str, Any] = {"kind": kind, "status": status, "sections": [], "selected": None}
    ctx["review"] = view
    selected = request.query_params.get("candidate_id")
    try:
        result = await api.candidates(
            {
                "scope_id": ctx["scope"],
                "status": status,
                "limit": 12,
                "cursor": request.query_params.get("cursor"),
                "candidate_kind": None if kind == "all" else kind,
            },
        )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in result["candidates"]:
            grouped.setdefault(item["candidate_kind"], []).append(item)
        # The inbox keeps both group headings visible so an empty group is
        # distinguishable from a filtered page.
        for candidate_kind in ("artifact", "tag") if kind == "all" else (kind,):
            view["sections"].append({
                "kind": candidate_kind,
                "candidates": grouped.get(candidate_kind, []),
                "next_cursor": result.get("next_cursor"),
            })
    except ReadError as error:
        ctx["errors"]["candidates"] = error
    if selected:
        selected_kind = request.query_params.get("candidate_kind", "artifact")
        if selected_kind not in KINDS:
            raise ReadError(422, "invalid_request")
        candidate = next(
            (
                item
                for section in view["sections"]
                for item in section["candidates"]
                if item["candidate_id"] == selected and "proposal" in item
            ),
            None,
        )
        if candidate is None:
            candidate = await api.read("/v1/candidates/get", {"scope_id": ctx["scope"], "candidate_id": selected})
        detail: dict[str, Any] = {
            "kind": candidate["candidate_kind"],
            "candidate": candidate,
            "evidence": [],
            "diff": "",
            "history": [],
        }
        view["selected"] = detail
        await _detail(api, request, ctx, detail)
    for section in view["sections"]:
        for item in section["candidates"]:
            opened = view["selected"]
            value = (
                opened["candidate"] if opened and item["candidate_id"] == opened["candidate"]["candidate_id"] else item
            )
            item.update(
                display_title=item.get("catalog_title") or candidate_title(value, ctx["t"]),
                family_label=family_label(item, ctx["t"]),
            )


async def _detail(api: DashboardAPI, request: Request, ctx: dict[str, Any], detail: dict[str, Any]) -> None:
    scope = ctx["scope"]
    candidate, kind = detail["candidate"], detail["kind"]
    proposal = candidate["proposal"]
    baseline: Any = None
    if kind == "tag":
        baseline, proposed = proposal["before_tags"], proposal["after_tags"]
        revision = {
            "proposal": proposal,
            "reason": candidate["reason"],
            "source_refs": candidate["source_refs"],
            "artifact_refs": candidate["artifact_refs"],
        }
    else:
        target = candidate.get("target")
        if target:
            try:
                baseline = (
                    await api.artifact_revision(scope, target["family"], target["artifact_id"], target["revision"])
                )["content"]
            except ReadError as error:
                detail["baseline_error"] = error
        proposed = proposal
        revision = {
            "proposal": proposal,
            "source_refs": candidate["source_refs"],
            "artifact_refs": candidate["artifact_refs"],
            "target": target,
            "reason": candidate["reason"],
        }
        # The public revision input accepts Profile content, while the stored proposal also has generation metadata.
        if candidate["family"] == "profile":
            revision["proposal"] = {"content": proposal["content"]}
    if request.query_params.get("view") == "history":
        if api.builtin is None:
            detail["history"] = (
                await api.read("/v1/candidates/history", {"scope_id": scope, "candidate_id": candidate["candidate_id"]})
            )["versions"]
        else:
            _candidate, detail["history"] = await api.review_inspection(scope, candidate["candidate_id"], history=True)
    detail["revision"] = formatted(revision)
    detail["proposal"] = formatted(proposal)
    detail["diff"] = "\n".join(
        difflib.unified_diff(
            formatted(baseline).splitlines(),
            formatted(proposed).splitlines(),
            fromfile="current target",
            tofile="candidate",
            lineterm="",
        )
    )
    detail["evidence"] = await _evidence(api, scope, candidate, request.query_params.get("evidence"))
    present_detail(ctx, detail, baseline)


async def _evidence(
    api: DashboardAPI, scope: str, candidate: dict[str, Any], selected: str | None
) -> list[dict[str, Any]]:
    evidence = [
        (
            ref,
            f"/v1/scopes/{segment(scope)}/sources/{segment(ref.get('source_type', ref.get('name')))}/{segment(ref['source_id'])}",
        )
        for ref in candidate.get("sources", candidate.get("source_refs", []))
    ] + [
        (
            ref,
            f"/v1/scopes/{segment(scope)}/artifacts/{segment(ref['family'])}/{segment(ref['artifact_id'])}/revisions/{ref['revision']}",
        )
        for ref in candidate.get("artifacts", candidate.get("artifact_refs", []))
    ]
    try:
        index_to_read = int(selected) if selected is not None else None
    except ValueError as error:
        raise ReadError(422, "invalid_request") from error
    if index_to_read is not None and not 0 <= index_to_read < len(evidence):
        raise ReadError(422, "invalid_request")
    items = []
    for index, (ref, path) in enumerate(evidence):
        item = {
            "reference": formatted(ref),
            "selected": index_to_read == index,
            "ref": ref,
            "number": index + 1,
            "kind": "artifact" if "artifact_id" in ref else "source",
        }
        if item["selected"]:
            try:
                value = await api.read(path)
                item["content"] = formatted(value)
                if item["kind"] == "source":
                    content = value["content"]
                    item["text"] = content if isinstance(content, str) else formatted(content)
                else:
                    item["fields"] = content_fields(ref["family"], value["content"])
            except ReadError as error:
                item["error"] = error
        items.append(item)
    return items


async def load_review_inspection(api: DashboardAPI, request: Request, ctx: dict[str, Any]) -> None:
    """Read an opened disclosure independently of the inbox and selected editor."""
    scope = ctx["scope"]
    candidate_id = request.query_params.get("candidate_id")
    if not scope or not candidate_id:
        raise ReadError(422, "invalid_request")
    history = request.query_params.get("view") == "history"
    candidate, versions = await api.review_inspection(scope, candidate_id, history=history)
    if history:
        ctx["inspection"] = {"history": versions}
    elif request.query_params.get("evidence") is not None:
        evidence = await _evidence(api, scope, candidate, request.query_params["evidence"])
        ctx["inspection"] = {"evidence": next(item for item in evidence if item["selected"])}
    else:
        raise ReadError(422, "invalid_request")


async def submit_review(api: DashboardAPI, request: Request) -> tuple[str, str, str, str]:
    origin = request.headers.get("origin")
    expected = urlsplit(str(request.base_url))
    if origin != f"{expected.scheme}://{expected.netloc}":
        raise ReadError(403, "cross_origin_request")
    body = await request.body()
    if len(body) > 350_000:
        raise ReadError(413, "invalid_request")
    try:
        values = parse_qs(body.decode("utf-8"), strict_parsing=True)
        if any(len(items) != 1 for items in values.values()):
            raise ReadError(422, "invalid_request")
        form = {key: items[0] for key, items in values.items()}
        scope, kind, candidate_id = form["scope"], form["candidate_kind"], form["candidate_id"]
        action = form["action"]
        if kind not in KINDS or action not in {"approve", "reject", "revise"}:
            raise ReadError(422, "invalid_request")
        payload: dict[str, Any] = {}
        if action == "revise":
            payload = json.loads(form["replacement"])
            if not isinstance(payload, dict):
                raise ReadError(422, "invalid_request")
        if action == "reject":
            payload["reason"] = form["reason"]
        payload.update(scope_id=scope, candidate_id=candidate_id, expected_version=int(form["expected_version"]))
    except (ValueError, KeyError, UnicodeDecodeError) as error:
        raise ReadError(422, "invalid_request") from error
    result = await api.read("/v1/candidates/" + action, payload)
    return scope, kind, candidate_id, result["status"]
