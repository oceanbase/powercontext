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

KINDS = {"artifact": "/v1/candidates", "tag": "/v1/candidates"}


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
        result = await api.read(
            "/v1/candidates/list",
            {
                "scope_id": ctx["scope"],
                "status": status,
                "limit": 12,
                "cursor": request.query_params.get("cursor"),
                "candidate_kind": None if kind == "all" else kind,
            },
        )
        view["sections"].append({"kind": kind, **result})
    except ReadError as error:
        ctx["errors"]["candidates"] = error
    if selected:
        selected_kind = request.query_params.get("candidate_kind", "artifact")
        if selected_kind not in KINDS:
            raise ReadError(422, "invalid_request")
        candidate = await api.read(KINDS[selected_kind] + "/get", {"scope_id": ctx["scope"], "candidate_id": selected})
        detail: dict[str, Any] = {
            "kind": candidate["candidate_kind"],
            "candidate": candidate,
            "evidence": [],
            "diff": "",
            "history": [],
        }
        view["selected"] = detail
        await _detail(api, ctx["scope"], detail)


async def _detail(api: DashboardAPI, scope: str, detail: dict[str, Any]) -> None:
    candidate, kind = detail["candidate"], detail["kind"]
    proposal = candidate["proposal"]
    baseline: Any = None
    if kind == "tag":
        baseline, proposed = proposal["before_tags"], proposal["after_tags"]
        detail["history"] = (
            await api.read(KINDS[kind] + "/history", {"scope_id": scope, "candidate_id": candidate["candidate_id"]})
        )["versions"]
        revision = {
            "proposal": proposal,
            "reason": candidate["reason"],
            "source_refs": candidate["source_refs"],
            "artifact_refs": candidate["artifact_refs"],
            "memory_citations": candidate["memory_citations"],
        }
    else:
        target = candidate.get("target")
        baseline, proposed = await _artifact_comparison(api, scope, detail)
        revision = {
            "proposal": proposal,
            "source_refs": candidate["source_refs"],
            "artifact_refs": candidate["artifact_refs"],
            "memory_citations": candidate["memory_citations"],
            "target": target,
            "reason": candidate["reason"],
        }
        # The public revision input accepts Profile content, while the stored proposal also has generation metadata.
        if candidate["family"] == "profile":
            revision["proposal"] = {"content": proposal["content"]}
    detail["history"] = (
        await api.read("/v1/candidates/history", {"scope_id": scope, "candidate_id": candidate["candidate_id"]})
    )["versions"]
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
    for source in candidate.get("sources", candidate.get("source_refs", [])):
        source_type = source.get("source_type", source.get("name"))
        await _evidence(
            api,
            detail,
            source,
            f"/v1/scopes/{segment(scope)}/sources/{segment(source_type)}/{segment(source['source_id'])}",
        )
    for artifact in candidate.get("artifacts", candidate.get("artifact_refs", [])):
        await _evidence(
            api,
            detail,
            artifact,
            f"/v1/scopes/{segment(scope)}/artifacts/{segment(artifact['family'])}/{segment(artifact['artifact_id'])}/revisions/{artifact['revision']}",
        )
    for citation in candidate.get("memory_citations", []):
        await _evidence(api, detail, citation, "/v1/memory/entries/get", {"scope_id": scope, "citation": citation})


async def _artifact_comparison(api: DashboardAPI, scope: str, detail: dict[str, Any]) -> tuple[Any, Any]:
    candidate = detail["candidate"]
    proposal = candidate["proposal"]
    baseline: Any = None
    target = candidate.get("target")
    if target and candidate["family"] != "memory":
        try:
            baseline = (
                await api.artifact_revision(scope, target["family"], target["artifact_id"], target["revision"])
            )["content"]
        except ReadError as error:
            detail["baseline_error"] = error
    proposed = proposal
    if candidate["family"] == "memory":
        try:
            baseline = []
            proposed = []
            for change in proposal["changes"]:
                citation = {
                    "memory_ref": proposal["base"],
                    "entry_id": change["entry_id"],
                    "entry_version_id": change["entry_version_id"],
                }
                entry = await api.read("/v1/memory/entries/get", {"scope_id": scope, "citation": citation})
                baseline.append({"entry_id": change["entry_id"], "kind": entry["kind"], "text": entry["text"]})
                proposed.append({"entry_id": change["entry_id"], "kind": change["kind"], "text": change["text"]})
        except ReadError as error:
            detail["baseline_error"] = error
    elif candidate["family"] == "profile":
        baseline = None if baseline is None else baseline["content"]
        proposed = proposal["content"]
    return baseline, proposed


async def _evidence(
    api: DashboardAPI, detail: dict[str, Any], ref: dict[str, Any], path: str, payload: dict[str, Any] | None = None
) -> None:
    item = {"reference": formatted(ref)}
    try:
        item["content"] = formatted(await api.read(path, payload))
    except ReadError as error:
        item["error"] = error
    detail["evidence"].append(item)


async def submit_review(api: DashboardAPI, request: Request) -> tuple[str, str, str]:
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
    await api.read(KINDS[kind] + "/" + action, payload)
    return scope, kind, candidate_id
