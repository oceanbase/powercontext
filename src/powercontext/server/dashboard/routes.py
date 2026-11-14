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

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from pydantic import ValidationError

from powercontext.server.dashboard.api import DashboardAPI, ReadError, segment
from powercontext.server.dashboard.content import RECORDS, load_content, load_profile, load_record, select_note
from powercontext.server.dashboard.markdown import handoff_markdown, profile_html
from powercontext.server.dashboard.navigation import (
    collection_return,
    directory_context,
    positive_revision,
    request_reading_return,
    with_language,
)
from powercontext.server.dashboard.page import load_builtin_page
from powercontext.server.dashboard.pagination import PAGE_SIZE
from powercontext.server.dashboard.preferences import CATALOGS, presentation, remember_language
from powercontext.server.dashboard.presenters import set_scope, source_view
from powercontext.server.dashboard.review import load_review, load_review_inspection, submit_review
from powercontext.server.dashboard.session import login_response, same_origin

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
    "review",
    "processing",
    "runtime",
    "observability",
    "entry",
    *RECORDS,
}
ENV = Environment(
    loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(), undefined=StrictUndefined
)
ENV.policies["json.dumps_kwargs"] = {"sort_keys": True, "ensure_ascii": False}
ENV.filters["markdown"] = profile_html


def display_time(value):
    try:
        timestamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError:
        return str(value)
    return (
        timestamp.replace(tzinfo=UTC).strftime("%Y-%m-%d %H:%M UTC")
        if timestamp.tzinfo is None
        else timestamp.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
    )


ENV.filters["display_time"] = display_time


def plural(t, key, count):
    """Pick a singular label when the translation provides one, then format the count."""
    label = t[key] if count != 1 else t.get(f"{key}_one", t[key])
    return label.format(count=count)


router = APIRouter()


def render(
    request: Request, ctx: dict[str, Any], fragment: str = "workspace.html", full: str = "base.html"
) -> HTMLResponse:
    partial = (
        request.headers.get("HX-Request") == "true" and request.headers.get("HX-History-Restore-Request") != "true"
    )
    title_key = ctx["page"]
    if title_key == "processing":
        title_key = {
            "sources": "processing_sources_title",
            "dreams": "processing_dream_tab",
        }.get(ctx["processing_kind"], "processing")
    elif title_key == "usage":
        title_key = "usage_resources"
    ctx["document_title"] = f"{ctx['t'][title_key]} · PowerContext"
    response = HTMLResponse(
        ENV.get_template(fragment if partial else full).render(plural=plural, **ctx),
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
                    "compare",
                    "history",
                    "rollback_error",
                    "prompt_key",
                    "prompt_revision",
                    "prompt_revision_cursor",
                    "topic_revision_cursor",
                    "profile_revision_cursor",
                    "experience_revision_cursor",
                    "skill_revision_cursor",
                    "handoff_revision_cursor",
                    "kind",
                    "generate",
                    "generation_mode",
                    "evidence_type",
                    "generation_cursor",
                    "generation_history",
                    "cursor",
                    "experience_cursor",
                    "skill_cursor",
                    "q",
                    "mode",
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
                    "notes_cursor",
                    "notes_revision_cursor",
                    "notes_history",
                    "note_state",
                    "skill_page",
                    "experience_page",
                    "experience_history",
                    "handoff_history",
                    "resource_type",
                    "review_status",
                    "candidate_id",
                    "candidate_kind",
                    "evidence",
                    "artifact_cursor",
                    "record_type",
                    "source_id",
                    "artifact_family",
                    "processing_artifacts_cursor",
                    "processing_artifacts_history",
                    "run_id",
                    "run_status",
                    "processing_sources_cursor",
                    "processing_sources_history",
                    "processing_runs_cursor",
                    "processing_runs_history",
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
        if destination == "notes" and {"artifact", "revision"}.intersection(params):
            for key in ("artifact", "revision"):
                query.pop(key, None)
        reading_link_context(request, ctx, destination, params, query)
        if "scope" in params and params["scope"] != ctx["scope"]:
            query = {"scope": params["scope"], "period": ctx["period"]}
        else:
            _drop_foreign_revision_cursors(query, params, request.query_params)
        query.update(params)
        if destination.startswith("evidence/"):
            destination = "evidence/" + segment(destination.removeprefix("evidence/"))
        return (
            f"/dashboard/{destination}?{urlencode({key: value for key, value in query.items() if value is not None})}"
            + (f"#{fragment}" if fragment else "")
        )

    return link


_REVISION_CURSOR_IDENTITY = {
    "topic_revision_cursor": "topic_artifact",
    "prompt_revision_cursor": "prompt_key",
    "experience_revision_cursor": "artifact",
    "skill_revision_cursor": "artifact",
    "handoff_revision_cursor": "artifact",
    "notes_revision_cursor": "artifact",
}


def _drop_foreign_revision_cursors(query: dict[str, Any], params: dict[str, Any], current: Mapping[str, str]) -> None:
    """Remove retained revision cursors that belong to an artifact the link no longer targets.

    A link that clears the selection drops the cursor too, because the page then opens a default artifact.
    """

    for cursor, identity in _REVISION_CURSOR_IDENTITY.items():
        if identity not in params:
            continue
        if params[identity] is None or params[identity] != current.get(identity):
            if cursor not in params:
                query.pop(cursor, None)
            for key in ("compare", "rollback_error"):
                if key not in params:
                    query.pop(key, None)


def reading_link_context(
    request: Request, ctx: dict[str, Any], destination: str, params: dict[str, Any], query: dict[str, Any]
) -> None:
    if destination.startswith("evidence/") and ctx["page"] == "notes":
        query["lang"] = ctx["language"]
    if (
        destination.startswith("evidence/")
        and ctx["page"] in {"profile", "handoff-detail", "notes"}
        and ctx.get("return_to")
    ):
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
    return_page = "profile" if page == "profile" else "methods" if page in {"experience", "skill"} else "handoff"
    ctx: dict[str, Any] = {
        **presentation(request),
        "request": request,
        "page": page,
        "parent_page": PARENTS.get(page),
        "scope": request.query_params.get("scope"),
        "period": request.query_params.get("period", "7d"),
        "method_kind": method_kind,
        "generation_open": request.query_params.get("generate") == "1",
        "generation_mode": request.query_params.get("generation_mode", "dream"),
        "generation_idempotency_key": str(uuid4()),
        "generation_evidence_type": "source",
        "generation_options": [],
        "generation_pager": None,
        "search_query": request.query_params.get("q", "").strip() or None,
        "search_mode": request.query_params.get("mode", "auto"),
        "search_used_mode": None,
        "artifact_query": request.query_params.get("topic_q", "").strip() or None,
        "topic_artifact": request.query_params.get("topic_artifact"),
        "topic_revision": request.query_params.get("topic_revision"),
        "topic_cursor": request.query_params.get("topic_cursor"),
        "search_limited": False,
        "note_state": request.query_params.get("note_state", "active"),
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
        "scopes_cursor": None,
        "scope_descriptor": None,
        "record_only": False,
        "parent_scope": None,
        "errors": {},
        "page_error": None,
        "status": 200,
        "stats": None,
        "inventory": None,
        "home_activity": [],
        "home_changes": [],
        "work_followup": [],
        "selected_note": None,
        "requested_note": request.query_params.get("artifact") if page == "notes" else None,
        "notes_pager": None,
        "notes_page_size": PAGE_SIZE,
        "collections": {},
        "review": {"kind": "all", "status": "pending", "sections": [], "selected": None},
        "related_sources": [],
        "source_record": None,
        "source": None,
        "processing_kind": "sources",
        "processing_show_topic_status": False,
        "runtime_health": {},
        "runtime_configuration": None,
        "runtime_checked_at": None,
        "processing_status": None,
        "processing_source": None,
        "processing_run": None,
        "processing_run_sources": [],
        "processing_items": [],
        "processing_pager": None,
        "source_artifacts": None,
        "processing_artifacts_pager": None,
        "processing_memory_previews": {},
        "topic_memory_pager": None,
        "request_revision": request.query_params.get("revision"),
        "profile_history_view": request.query_params.get("view") == "history",
        "profile_revisions": [],
        "profile_pager": None,
        "profile_html": "",
        "history": None,
        "prompt_key": None,
        "prompt_opened": None,
        "return_to": collection_return(
            request.query_params.get("return_to"),
            request.query_params.get("scope", ""),
            page=return_page,
        ),
        "reading_return": request_reading_return(request),
    }
    if ctx["return_to"]:
        ctx["return_to"] = with_language(ctx["return_to"], ctx["language"])
    ctx["link"] = links(request, ctx)
    return ctx


async def default_scope(api: DashboardAPI, ctx: dict[str, Any]) -> dict[str, Any] | None:
    if ctx["scope"] is None:
        try:
            descriptor = await api.read("/v1/scopes/default")
        except ReadError as error:
            if error.status != 404:
                ctx["errors"]["default_scope"] = error
        else:
            ctx["scope"] = descriptor["scope_id"]
            return descriptor
    return None


async def scope_context(api: DashboardAPI, ctx: dict[str, Any], descriptor: dict[str, Any] | None = None) -> None:
    if not ctx["scope"]:
        return
    try:
        if descriptor is None:
            descriptor = await api.read(f"/v1/scopes/{segment(ctx['scope'])}")
        else:
            await api.authorize_scope(ctx["scope"], "dashboard_scope")
    except ReadError as error:
        record_page = (
            ctx["page"] in RECORDS
            or ctx["page"] == "handoff-download"
            or (ctx["page"] == "notes" and ctx["requested_note"])
        )
        if record_page and error.status in {403, 404}:
            ctx["record_only"] = True
            return
        raise
    set_scope(ctx, descriptor)


@router.get("/scope-options")
async def scope_options(
    request: Request,
    query: str = Query(default="", max_length=256),
    cursor: str | None = Query(default=None, min_length=1, max_length=4096),
    current: str | None = Query(default=None, max_length=256),
) -> JSONResponse:
    """Page the public Scope discovery surface using the browser's dashboard credentials."""

    params = {"limit": "25"}
    if query:
        params.update(query=query, query_field="title")
    if cursor is not None:
        params["cursor"] = cursor
    api = DashboardAPI(request)
    try:
        result = await api.read("/v1/scopes?" + urlencode(params))
        items = result["items"]
        navigation = {}
        if api.builtin is not None:
            identities = tuple(item["scope_id"] for item in items) + (() if current is None else (current,))
            navigation = await api.builtin.scopes.navigation(identities)
        for item in items:
            item["display_title"] = navigation.get(item["scope_id"], {}).get("display_title", item["title"])
        selected = navigation.get(current)
        parent = None if selected is None else navigation.get(selected["parent_scope_id"])
        return JSONResponse(
            {
                "items": [
                    {key: item[key] for key in ("scope_id", "title", "display_title", "parent_scope_id")}
                    for item in items
                ],
                "next_cursor": result["next_cursor"],
                "current": selected,
                "parent": parent,
            },
            headers={"Cache-Control": "no-store"},
        )
    except ReadError as error:
        return JSONResponse(
            {"error": {"code": error.code}}, status_code=error.status, headers={"Cache-Control": "no-store"}
        )
    finally:
        await api.client.aclose()


def validate_selection(page: str, ctx: dict[str, Any]) -> None:
    if page not in PAGES:
        raise ReadError(404, "not_found")
    if (
        ctx["period"] not in {"today", "7d", "30d"}
        or ctx["method_kind"] not in {"experience", "skill"}
        or len(ctx["search_query"] or "") > (8192 if page == "notes" else 2000)
        or ctx["note_state"] not in {"active", "forgotten", "merged", "retired"}
    ):
        raise ReadError(422, "invalid_request")


@router.get("")
@router.get("/")
async def index(request: Request) -> RedirectResponse:
    return RedirectResponse("/dashboard/home" + ("?" + str(request.query_params) if request.query_params else ""))


@router.get("/evidence/{source_id:path}")
async def evidence(request: Request, source_id: str) -> HTMLResponse:
    origin = request.query_params.get("origin", "experience")
    ctx = initial_context(request, origin if origin in RECORDS or origin in {"profile", "notes"} else "experience")
    api = DashboardAPI(request)
    ctx.update(source_id=source_id, origin=ctx["page"], source_type=request.query_params.get("source_type", ""))
    try:
        descriptor = await default_scope(api, ctx)
        await scope_context(api, ctx, descriptor)
        if ctx["page"] == "profile":
            await load_profile(api, request, ctx, include_history=False)
        elif ctx["page"] == "notes":
            from powercontext.server.dashboard.content import validate_note_selection

            validate_note_selection(request, ctx)
            if not ctx.get("note_selection"):
                raise ReadError(422, "invalid_request")  # noqa: TRY301
            await select_note(api, ctx)
        else:
            await load_record(api, request, ctx, include_history=False)
        if not any(
            item["source_id"] == source_id and item["source_type"] == ctx["source_type"]
            for item in ctx["related_sources"]
        ):
            # Association failures use the same HTML boundary as failed content reads.
            raise ReadError(404, "not_found")  # noqa: TRY301
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


@router.post("/rollback")
async def rollback_revision(request: Request) -> Response:
    if not same_origin(request):
        return HTMLResponse(status_code=403)
    form = await request.form()
    scope = str(form.get("scope") or "")
    family = str(form.get("family") or "")
    artifact_id = str(form.get("artifact_id") or "")
    reason = str(form.get("reason") or "")
    return_page = {
        "experience": "methods",
        "skill": "methods",
        "handoff": "handoff",
        "profile": "profile",
    }.get(family)
    return_to = (
        collection_return(str(form.get("return_to") or "") or None, scope, page=return_page)
        if return_page is not None
        else None
    )
    try:
        source_revision = positive_revision(str(form.get("source_revision") or ""))
        expected_revision = positive_revision(str(form.get("expected_revision") or ""))
    except ReadError:
        return RedirectResponse(f"/dashboard/home?scope={scope}", status_code=303)
    api = DashboardAPI(request)
    try:
        stored = await api.artifact_revision(scope, family, artifact_id, source_revision)
        from powercontext.server.dashboard.revisions import replace_body

        response = await api.send(
            "PUT",
            f"/v1/scopes/{segment(scope)}/artifacts/{segment(family)}/{segment(artifact_id)}",
            replace_body(family, stored["content"], source_revision, reason),
            headers={"If-Match": f'"revision:{expected_revision}"'},
        )
    except ReadError as error:
        code = "forbidden" if error.status == 403 else "rejected"
        return RedirectResponse(
            _rollback_return(scope, family, artifact_id, source_revision, code, return_to), status_code=303
        )
    finally:
        await api.client.aclose()
    if response.status_code == 412:
        code = "revision_conflict"
    elif response.status_code == 403:
        code = "forbidden"
    elif response.status_code != 200:
        code = "rejected"
    else:
        created = response.json()["revision"]
        return RedirectResponse(_rollback_return(scope, family, artifact_id, created, None, return_to), status_code=303)
    return RedirectResponse(
        _rollback_return(scope, family, artifact_id, source_revision, code, return_to), status_code=303
    )


def _rollback_return(
    scope: str,
    family: str,
    artifact_id: str,
    revision: int,
    error: str | None,
    return_to: str | None = None,
) -> str:
    params: dict[str, Any] = {"scope": scope, "rollback_error": error, "return_to": return_to}
    if family == "profile":
        page = "profile"
        params["revision"] = revision
    elif family == "topic-memory":
        page = "topics"
        params.update(topic_artifact=artifact_id, topic_revision=revision)
    elif family == "prompt":
        page = "prompts"
        params.update(prompt_key=artifact_id, prompt_revision=revision)
    else:
        page = "handoff-detail" if family == "handoff" else family
        params.update(artifact=artifact_id, revision=revision)
    return f"/dashboard/{page}?{urlencode({key: value for key, value in params.items() if value is not None})}"


@router.get("/entry/scopes")
async def entry_scopes(request: Request) -> HTMLResponse:
    ctx = initial_context(request, "entry")
    api = DashboardAPI(request)
    try:
        await load_entry_scopes(api, request, ctx)
    except ReadError as error:
        ctx.update(page_error=error, status=error.status)
    finally:
        await api.client.aclose()
    return render(request, ctx, "components/entry-scopes.html", "components/entry-scopes.html")


async def load_entry_scopes(api, request, ctx):
    params = {"limit": "25"}
    if request.query_params.get("cursor"):
        params["cursor"] = request.query_params["cursor"]
    result = await api.read("/v1/scopes?" + urlencode(params))
    ctx.update(scopes=result["items"], scopes_cursor=result["next_cursor"])


@router.get("/review/inspect")
async def review_inspection(request: Request) -> HTMLResponse:
    ctx = initial_context(request, "review")
    api = DashboardAPI(request)
    try:
        await load_review_inspection(api, request, ctx)
    except ReadError as error:
        if error.status == 401:
            return login_response(request=request)
        ctx.update(page_error=error, status=error.status)
    finally:
        await api.client.aclose()
    return render(request, ctx, "components/review-inspection.html", "components/review-inspection.html")


@router.post("/review/decision")
async def review_decision(request: Request) -> Response:
    ctx = initial_context(request, "review")
    api = DashboardAPI(request)
    try:
        scope, kind, candidate_id, status = await submit_review(api, request)
        return RedirectResponse(
            "/dashboard/review?"
            + urlencode({
                "scope": scope,
                "candidate_kind": kind,
                "candidate_id": candidate_id,
                "review_status": status,
                "lang": ctx["language"],
                "period": ctx["period"],
            }),
            status_code=303,
        )
    except ReadError as error:
        if error.status == 401:
            return login_response(request=request)
        ctx.update(page_error=error, status=error.status)
        return render(request, ctx)
    finally:
        await api.client.aclose()


@router.post("/profile/generate")
async def profile_generation(request: Request) -> Response:
    from powercontext.server.dashboard.generation import submit_profile_generation

    ctx = initial_context(request, "profile")
    api = DashboardAPI(request)
    try:
        scope, result = await submit_profile_generation(api, request)
        params = {"scope": scope, "period": ctx["period"], "lang": ctx["language"]}
        if result.candidate_id is not None:
            params.update(candidate_id=result.candidate_id, candidate_kind="artifact", review_status="pending")
            destination = "/dashboard/review"
        else:
            params["profile_generation_result"] = result.status.value
            destination = "/dashboard/profile"
        return RedirectResponse(destination + "?" + urlencode(params), status_code=303)
    except ReadError as error:
        if error.status == 401:
            return login_response(request=request)
        ctx.update(page_error=error, status=error.status)
        return render(request, ctx)
    finally:
        await api.client.aclose()


@router.post("/methods/dream")
async def method_dream_generation(request: Request) -> Response:
    from powercontext.server.dashboard.generation import submit_dream_generation

    ctx = initial_context(request, "methods")
    api = DashboardAPI(request)
    try:
        scope, result = await submit_dream_generation(api, request)
        params = {
            "scope": scope,
            "period": ctx["period"],
            "lang": ctx["language"],
            "record_type": "dreams",
            "run_id": result.run_id,
        }
        return RedirectResponse("/dashboard/processing?" + urlencode(params), status_code=303)
    except ReadError as error:
        if error.status == 401:
            return login_response(request=request)
        ctx.update(page_error=error, status=error.status)
        return render(request, ctx)
    finally:
        await api.client.aclose()


@router.post("/processing/retry")
async def processing_retry(request: Request) -> Response:
    from powercontext.server.dashboard.recovery import submit_topic_retry

    ctx = initial_context(request, "processing")
    api = DashboardAPI(request)
    try:
        scope = await submit_topic_retry(api, request)
        params = {
            "scope": scope,
            "period": ctx["period"],
            "lang": ctx["language"],
            "record_type": "attempts",
            "retry_result": "accepted",
        }
        return RedirectResponse(
            "/dashboard/processing?" + urlencode(params) + "#processing-status-heading", status_code=303
        )
    except ReadError as error:
        if error.status == 401:
            return login_response(request=request)
        ctx.update(page_error=error, status=error.status)
        return render(request, ctx)
    finally:
        await api.client.aclose()


@router.post("/methods/generate")
async def method_generation(request: Request) -> Response:
    from powercontext.server.dashboard.generation import submit_generation

    ctx = initial_context(request, "methods")
    api = DashboardAPI(request)
    try:
        scope, family, result = await submit_generation(api, request)
        params = {"scope": scope, "period": ctx["period"], "lang": ctx["language"]}
        if result.candidate is not None:
            params.update(
                candidate_id=result.candidate.candidate_id, candidate_kind="artifact", review_status="pending"
            )
            destination = "/dashboard/review"
        else:
            params.update(kind=family, generation_result="no_op")
            destination = "/dashboard/methods"
        return RedirectResponse(destination + "?" + urlencode(params), status_code=303)
    except ReadError as error:
        if error.status == 401:
            return login_response(request=request)
        ctx.update(page_error=error, status=error.status)
        return render(request, ctx)
    finally:
        await api.client.aclose()


@router.get("/{page}")
async def screen(request: Request, page: str) -> HTMLResponse:  # noqa: C901 -- Keep page loading, recovery and final rendering explicit.
    ctx = initial_context(request, page if page in PAGES else "home")
    api = DashboardAPI(request)
    try:
        validate_selection(page, ctx)
        if page == "entry":
            await load_entry_scopes(api, request, ctx)
        elif page == "runtime":
            from powercontext.server.dashboard.runtime import load_runtime

            await load_runtime(api, ctx)
        elif page != "observability" and not await load_builtin_page(api, request, ctx):
            descriptor = await default_scope(api, ctx)
            if ctx["scope"]:
                await scope_context(api, ctx, descriptor)
            if ctx["scope"] and page == "review":
                await load_review(api, request, ctx)
            elif ctx["scope"] and page != "entry":
                await load_content(api, request, ctx)
            if ctx["errors"] and not ctx["scope_descriptor"] and not ctx["record_only"]:
                raise next(iter(ctx["errors"].values()))
        if page == "home" and ctx["scope_descriptor"]:
            from powercontext.server.dashboard.processing import load_topic_status

            await load_topic_status(api, ctx)
        if page == "methods" and ctx["scope_descriptor"]:
            from powercontext.server.dashboard.generation import load_generation

            await load_generation(api, request, ctx)
        if page not in {"entry", "runtime", "observability"} and not ctx["scope_descriptor"] and not ctx["record_only"]:
            await load_entry_scopes(api, request, ctx)
    except ReadError as error:
        if error.status == 401:
            return login_response(request=request)
        ctx.update(page_error=error, status=error.status)
    finally:
        await api.client.aclose()
    return render(request, ctx)
