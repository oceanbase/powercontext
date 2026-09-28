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

"""Render exact Scope content and independent read failures as HTML."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from pydantic import ValidationError

from powercontext.server.dashboard.api import DashboardAPI, ReadError, segment
from powercontext.server.dashboard.content import RECORDS, load_content
from powercontext.server.dashboard.markdown import handoff_markdown
from powercontext.server.dashboard.navigation import (
    collection_return,
    directory_context,
    positive_revision,
    request_reading_return,
)
from powercontext.server.dashboard.preferences import CATALOGS, presentation, remember_language
from powercontext.server.dashboard.presenters import source_view
from powercontext.server.dashboard.session import login_response

ROOT = Path(__file__).parent
LABELS = CATALOGS["zh"]
PARENTS = {"handoff-detail": "handoff", "experience": "methods", "skill": "methods", "handoff-download": "handoff"}
PAGES = {
    "home",
    "handoff",
    "notes",
    "methods",
    "topics",
    "prompts",
    "profile",
    "handoff-download",
    "usage",
    "entry",
    *RECORDS,
}
ENV = Environment(
    loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(), undefined=StrictUndefined
)
ENV.policies["json.dumps_kwargs"] = {"sort_keys": True, "ensure_ascii": False}
router = APIRouter()


def render(
    request: Request, ctx: dict[str, Any], fragment: str = "workspace.html", full: str = "base.html"
) -> HTMLResponse:
    partial = (
        request.headers.get("HX-Request") == "true" and request.headers.get("HX-History-Restore-Request") != "true"
    )
    response = HTMLResponse(
        ENV.get_template(fragment if partial else full).render(**ctx),
        status_code=ctx["status"],
        headers={
            "Cache-Control": "no-store",
            "Vary": "HX-Request, HX-History-Restore-Request",
            "X-Dashboard-HTML": "1",
        },
    )
    remember_language(response, request)
    return response


def links(request: Request, ctx: dict[str, Any]):
    def link(destination: str | None = None, fragment: str = "", **params: Any) -> str:
        destination = destination or ctx["page"]
        query = {"scope": ctx["scope"], "period": ctx["period"]}
        if destination == ctx["page"]:
            query.update({
                key: value
                for key, value in request.query_params.items()
                if key
                in {
                    "artifact",
                    "revision",
                    "kind",
                    "entry",
                    "memory_id",
                    "memory_revision",
                    "entry_version",
                    "cursor",
                    "experience_cursor",
                    "skill_cursor",
                    "q",
                    "topic_q",
                    "topic_artifact",
                    "topic_revision",
                    "topic_cursor",
                    "topic_history",
                    "view",
                    "profile_cursor",
                    "profile_history",
                    "return_to",
                    "lang",
                    "notes_page",
                    "skill_page",
                    "experience_history",
                    "handoff_history",
                }
            })
        if destination == "methods" and ctx["page"] in {"experience", "skill"}:
            query["kind"] = ctx["page"]
        record = (
            ctx.get("source_record")
            if destination.startswith("evidence/")
            else ctx["data"].get(RECORDS.get(destination, ""))
        )
        if destination == "handoff-download":
            record = ctx["data"].get("handoff")
        if record:
            query.update(artifact=record["artifact_id"], revision=record["revision"])
        if destination == "notes" and "entry" in params:
            note = next((item for item in ctx["data"]["notes"] if item["entry_id"] == params["entry"]), None)
            if note:
                query.update(
                    memory_id=note["memory_ref"]["artifact_id"],
                    memory_revision=note["memory_ref"]["revision"],
                    entry_version=note["entry_version_id"],
                )
        reading_link_context(request, ctx, destination, params, query)
        if "scope" in params and params["scope"] != ctx["scope"]:
            query = {"scope": params["scope"], "period": ctx["period"]}
        else:
            query.update(params)
        if destination.startswith("evidence/"):
            destination = "evidence/" + segment(destination.removeprefix("evidence/"))
        return (
            f"/dashboard/{destination}?{urlencode({key: value for key, value in query.items() if value is not None})}"
            + (f"#{fragment}" if fragment else "")
        )

    return link


def reading_link_context(
    request: Request, ctx: dict[str, Any], destination: str, params: dict[str, Any], query: dict[str, Any]
) -> None:
    if destination.startswith("evidence/") and ctx["page"] in {"profile", "handoff-detail"} and ctx.get("return_to"):
        query["return_to"] = ctx["return_to"]
    if destination in {"handoff-detail", "handoff-download"}:
        source_return = (
            directory_context(request, ctx["scope"], "handoff", ctx["language"])
            if ctx["page"] == "handoff"
            else ctx.get("return_to")
        )
        if source_return:
            query["return_to"] = source_return
        query["lang"] = ctx["language"]
    if destination == "profile" and "revision" in params and params["revision"] is not None:
        query.pop("view", None)
        query.pop("profile_cursor", None)
        query.pop("profile_history", None)
        if ctx.get("profile_history_view"):
            query["return_to"] = directory_context(request, ctx["scope"], "profile", ctx["language"])


def initial_context(request: Request, page: str) -> dict[str, Any]:
    method_kind = request.query_params.get("kind", "experience")
    if method_kind == "all":
        method_kind = "experience"
    ctx: dict[str, Any] = {
        **presentation(request),
        "page": page,
        "parent_page": PARENTS.get(page),
        "scope": request.query_params.get("scope"),
        "period": request.query_params.get("period", "7d"),
        "method_kind": method_kind,
        "search_query": request.query_params.get("q", "").strip() or None,
        "artifact_query": request.query_params.get("topic_q", "").strip() or None,
        "topic_artifact": request.query_params.get("topic_artifact"),
        "topic_revision": request.query_params.get("topic_revision"),
        "topic_cursor": request.query_params.get("topic_cursor"),
        "search_limited": False,
        "data": {
            "title": "PowerContext",
            "summary": "",
            "notes": [],
            "handoff": None,
            "experience": None,
            "skill": None,
            "topic_memory": [],
            "topic_memory_selected": None,
            "prompts": [],
            "profile": None,
        },
        "scopes": [],
        "scope_descriptor": None,
        "record_only": False,
        "default_scope": None,
        "children": [],
        "parent_scope": None,
        "errors": {},
        "page_error": None,
        "status": 200,
        "stats": None,
        "selected_note": None,
        "requested_entry": request.query_params.get("entry"),
        "collections": {},
        "related_sources": [],
        "source_record": None,
        "source": None,
        "topic_memory_pager": None,
        "request_revision": request.query_params.get("revision"),
        "profile_history_view": request.query_params.get("view") == "history",
        "profile_revisions": [],
        "profile_pager": None,
        "profile_html": "",
        "return_to": collection_return(
            request.query_params.get("return_to"),
            request.query_params.get("scope", ""),
            page="profile" if page == "profile" else "handoff",
        ),
        "reading_return": request_reading_return(request),
    }
    ctx["link"] = links(request, ctx)
    return ctx


async def scope_context(api: DashboardAPI, ctx: dict[str, Any]) -> None:
    try:
        ctx["scopes"] = (await api.read("/v1/scopes"))["items"]
        for item in ctx["scopes"]:
            item["display_title"] = item["title"]
    except ReadError as error:
        ctx["errors"]["scopes"] = error
    if ctx["scope"] is None:
        try:
            descriptor = await api.read("/v1/scopes/default")
            ctx["default_scope"] = descriptor["scope_id"]
            ctx["scope"] = descriptor["scope_id"]
        except ReadError as error:
            if error.status != 404:
                ctx["errors"]["default_scope"] = error
    if not ctx["scope"]:
        return
    try:
        descriptor = await api.read(f"/v1/scopes/{segment(ctx['scope'])}")
    except ReadError as error:
        if (ctx["page"] in RECORDS or ctx["page"] == "handoff-download") and error.status in {403, 404}:
            ctx["record_only"] = True
            return
        raise
    ctx["scope_descriptor"] = descriptor
    ctx["data"].update(title=descriptor["title"], summary=descriptor["summary"])
    if not any(item["scope_id"] == ctx["scope"] for item in ctx["scopes"]):
        ctx["scopes"].append({**descriptor, "display_title": descriptor["title"]})
    describe_scope_relations(ctx, descriptor)


def describe_scope_relations(ctx: dict[str, Any], descriptor: dict[str, Any]) -> None:
    by_id = {item["scope_id"]: item for item in ctx["scopes"]}
    for item in ctx["scopes"]:
        names = [item["title"]]
        ancestor = item.get("parent_scope_id")
        visited = {item["scope_id"]}
        while ancestor in by_id and ancestor not in visited:
            visited.add(ancestor)
            names.insert(0, by_id[ancestor]["title"])
            ancestor = by_id[ancestor].get("parent_scope_id")
        item["display_title"] = item["title"] + (" (" + " / ".join(names[:-1]) + ")" if len(names) > 1 else "")
    ctx["children"] = [item for item in ctx["scopes"] if item.get("parent_scope_id") == ctx["scope"]]
    ctx["parent_scope"] = next(
        (item for item in ctx["scopes"] if item["scope_id"] == descriptor.get("parent_scope_id")), None
    )


def require_source(exists: bool) -> None:
    if not exists:
        raise ReadError(404, "not_found")


def validate_selection(page: str, ctx: dict[str, Any]) -> None:
    if page not in PAGES:
        raise ReadError(404, "not_found")
    if (
        ctx["period"] not in {"today", "7d", "30d"}
        or ctx["method_kind"] not in {"experience", "skill"}
        or len(ctx["search_query"] or "") > (8192 if page == "notes" else 2000)
    ):
        raise ReadError(422, "invalid_request")


@router.get("")
@router.get("/")
@router.get("/guide")
async def index(request: Request) -> RedirectResponse:
    return RedirectResponse("/dashboard/home" + ("?" + str(request.query_params) if request.query_params else ""))


@router.get("/evidence/{source_id:path}")
async def evidence(request: Request, source_id: str) -> HTMLResponse:
    origin = request.query_params.get("origin", "experience")
    ctx = initial_context(request, origin if origin in RECORDS or origin == "profile" else "experience")
    api = DashboardAPI(request)
    ctx.update(source_id=source_id, origin=ctx["page"], source_type=request.query_params.get("source_type", ""))
    try:
        await scope_context(api, ctx)
        await load_content(api, request, ctx)
        require_source(
            any(
                item["source_id"] == source_id and item["source_type"] == ctx["source_type"]
                for item in ctx["related_sources"]
            )
        )
        value = await api.read(
            f"/v1/scopes/{segment(ctx['scope'])}/sources/{segment(ctx['source_type'])}/{segment(source_id)}"
        )
        ctx["source"] = source_view(value)
    except ReadError as error:
        if error.status == 401:
            return login_response(request=request)
        ctx.update(page_error=error, status=error.status)
    finally:
        await api.client.aclose()
    return render(request, ctx, "evidence.html", "source.html")


def validate_download(request: Request, scope: str, artifact: str) -> None:
    query = request.query_params
    if (
        not scope.strip()
        or len(scope) > 256
        or not 1 <= len(artifact) <= 128
        or any(not 33 <= ord(char) <= 126 for char in artifact)
        or len(query) != len(query.multi_items())
    ):
        raise ReadError(422, "invalid_request")


@router.get("/handoff-download")
async def download_handoff(request: Request) -> Response:
    ctx = initial_context(request, "handoff-download")
    api = DashboardAPI(request)
    try:
        query = request.query_params
        scope, artifact = query.get("scope", ""), query.get("artifact", "")
        validate_download(request, scope, artifact)
        revision = positive_revision(query.get("revision"))
        raw = await api.artifact_revision(scope, "handoff", artifact, revision)
        result = handoff_markdown(raw, ctx["language"])
        return Response(
            result,
            media_type="text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="handoff-r{revision}.md"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
    except ValidationError:
        ctx.update(page_error=ReadError(422, "unsupported_content"), status=422)
    except ReadError as error:
        if error.status == 401:
            return login_response(request=request)
        ctx.update(page_error=error, status=error.status)
    finally:
        await api.client.aclose()
    return render(request, ctx)


@router.get("/{page}")
async def screen(request: Request, page: str) -> HTMLResponse:
    ctx = initial_context(request, page if page in PAGES else "home")
    api = DashboardAPI(request)
    try:
        validate_selection(page, ctx)
        await scope_context(api, ctx)
        if ctx["scope"] and page != "entry":
            await load_content(api, request, ctx)
        if ctx["errors"] and not ctx["scope_descriptor"] and not ctx["record_only"]:
            raise next(iter(ctx["errors"].values()))
    except ReadError as error:
        if error.status == 401:
            return login_response(request=request)
        ctx.update(page_error=error, status=error.status)
    finally:
        await api.client.aclose()
    return render(request, ctx)
