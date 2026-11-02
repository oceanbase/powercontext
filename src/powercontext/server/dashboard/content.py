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

"""Load only the content needed by the selected dashboard page."""

import asyncio
from typing import Any

from fastapi import Request

from powercontext.server.dashboard.api import DashboardAPI, ReadError, segment
from powercontext.server.dashboard.markdown import profile_html
from powercontext.server.dashboard.navigation import positive_revision
from powercontext.server.dashboard.pagination import PAGE_SIZE, cursor_links, list_links, list_page
from powercontext.server.dashboard.presenters import memory_view, usage_view
from powercontext.server.dashboard.revisions import load_history

RECORDS = {"handoff-detail": "handoff", "experience": "experience", "skill": "skill"}


async def load_collection(api: DashboardAPI, request: Request, ctx: dict[str, Any], family: str) -> None:
    page, scope = ctx["page"], ctx["scope"]
    ctx["collection_page_size"] = PAGE_SIZE
    try:
        cursor = request.query_params.get(f"{family}_cursor" if page == "methods" else "cursor")
        query = ctx["search_query"] if page == "methods" else None
        result = await api.records(
            scope,
            family,
            cursor=cursor,
            limit=1 if page == "home" else PAGE_SIZE,
            query=query,
        )
        if page != "home":
            if family == "skill" or query:
                window = list_page(result["items"], request.query_params.get(f"{family}_page"))
                result["items"] = window["items"]
                result["pager"] = list_links(ctx, family, window)
            else:
                result["pager"] = cursor_links(request, ctx, family, result["next_cursor"])
        ctx["collections"][family] = result
        ctx["data"][family] = next((item for item in result["items"] if "error" not in item), None)
        for item in result["items"]:
            if "error" in item:
                ctx["errors"][family] = item["error"]
    except Exception as error:
        ctx["errors"][family] = ReadError.from_exception(error)


def validate_note_selection(request: Request, ctx: dict[str, Any]) -> None:
    if ctx["page"] != "notes":
        return
    query = request.query_params
    if (
        "entry" in query
        or (not ctx["search_query"] and "notes_page" in query)
        or ("artifact" in query) != ("revision" in query)
    ):
        raise ReadError(422, "invalid_request")
    ctx["note_selection"] = (
        {"artifact_id": query["artifact"], "revision": positive_revision(query["revision"])}
        if "artifact" in query
        else None
    )


async def load_notes(api: DashboardAPI, request: Request, ctx: dict[str, Any]) -> None:
    validate_note_selection(request, ctx)
    searching = ctx["page"] == "notes" and bool(ctx["search_query"])
    try:
        if searching:
            result = await api.read(
                "/v1/memory/search",
                {
                    "scope_id": ctx["scope"],
                    "query": ctx["search_query"],
                    "mode": ctx["search_mode"],
                    "limit": 50,
                },
            )
        else:
            result = await api.atomic_memory_list(
                ctx["scope"],
                state=ctx["note_state"],
                limit=3 if ctx["page"] == "home" else PAGE_SIZE,
                cursor=request.query_params.get("notes_cursor") if ctx["page"] == "notes" else None,
            )
    except Exception as error:
        ctx["errors"]["notes"] = ReadError.from_exception(error)
        return
    if searching:
        present_search(result, request, ctx)
    else:
        ctx["data"]["notes"] = memory_view(result)
        if ctx["page"] == "notes":
            ctx["notes_pager"] = cursor_links(request, ctx, "notes", result["next_cursor"])


def present_search(result, request, ctx):
    items = memory_view({
        "items": [{**hit["memory"], "matched_by": hit["matched_by"], "score": hit["score"]} for hit in result["hits"]]
    })
    window = list_page(items, request.query_params.get("notes_page"))
    ctx["data"]["notes"] = window["items"]
    ctx["notes_pager"] = list_links(ctx, "notes", window)
    ctx["search_limited"] = len(result["hits"]) == 50
    ctx["search_used_mode"] = result["mode"]


async def load_stats(api: DashboardAPI, ctx: dict[str, Any]) -> None:
    try:
        usage, recall = await api.usage(ctx["scope"], ctx["period"])
        ctx["stats"] = usage_view(usage, recall)
    except ReadError as error:
        ctx["errors"]["usage"] = error


async def select_note(api: DashboardAPI, ctx: dict[str, Any]) -> None:
    scope = ctx["scope"]
    ref = ctx.get("note_selection") or next(iter(ctx["data"]["notes"]), None)
    if ref:
        ctx["selected_note"] = await api.atomic_memory_get(scope, ref["artifact_id"], ref["revision"])
        current = next(
            (item for item in ctx["data"]["notes"] if item["note_key"] == ctx["selected_note"]["note_key"]), None
        )
        if current and "matched_by" in current:
            ctx["selected_note"].update(matched_by=current["matched_by"], score=current["score"])


async def load_record(api: DashboardAPI, request: Request, ctx: dict[str, Any], *, include_history=True) -> None:
    page, scope = ctx["page"], ctx["scope"]
    family = RECORDS[page]
    artifact = request.query_params.get("artifact")
    revision = request.query_params.get("revision")
    if not artifact or not revision:
        raise ReadError(404, "not_found")
    try:
        revision_number = int(revision)
    except ValueError as error:
        raise ReadError(422, "invalid_request") from error
    record = await api.record(scope, family, artifact, revision_number)
    ctx["data"][family] = record
    ctx["source_record"] = record
    ctx["related_sources"] = [source for source in record["sources"] if source["source_type"] == "content"]
    if include_history:
        ctx["history"] = await load_history(
            api,
            request,
            ctx,
            family=family,
            artifact_id=artifact,
            selected=revision_number,
            cursor_key=f"{family}_revision_cursor",
        )


async def load_home(api: DashboardAPI, request: Request, ctx: dict[str, Any]) -> None:
    await asyncio.gather(
        load_notes(api, request, ctx),
        load_stats(api, ctx),
        *(load_collection(api, request, ctx, family) for family in ("handoff", "experience", "skill")),
    )


async def load_content(api: DashboardAPI, request: Request, ctx: dict[str, Any]) -> None:
    page = ctx["page"]
    if page == "home":
        await load_home(api, request, ctx)
    elif page == "notes":
        await load_notes(api, request, ctx)
        await select_note(api, ctx)
        await load_note_history(api, request, ctx)
    elif page == "handoff":
        await load_collection(api, request, ctx, "handoff")
    elif page == "methods":
        await load_collection(api, request, ctx, ctx["method_kind"])
    elif page == "usage":
        await load_stats(api, ctx)
    elif page == "topics":
        await load_topics(api, request, ctx)
    elif page == "profile":
        await load_profile(api, request, ctx)
    elif page == "prompts":
        await load_prompts(api, request, ctx)
    elif page in RECORDS:
        await load_record(api, request, ctx)


async def load_topics(api: DashboardAPI, request: Request, ctx: dict[str, Any]) -> None:
    """Load Topic Memory browse/search results and an exact selected revision."""
    query = ctx["artifact_query"]
    if query:
        try:
            result = await api.read("/v1/topic-memory/search", {"scope_id": ctx["scope"], "query": query})
            ctx["data"]["topic_memory"] = result["hits"]
        except ReadError as error:
            ctx["errors"]["topic_memory"] = error
    else:
        try:
            page = await api.topic_memory_browse(ctx["scope"], cursor=ctx["topic_cursor"])
            ctx["data"]["topic_memory"] = page["items"]
            ctx["topic_memory_pager"] = cursor_links(request, ctx, "topic", page["next_cursor"])
        except ReadError as error:
            ctx["errors"]["topic_memory"] = error
    if ctx["topic_artifact"] and ctx["topic_revision"]:
        try:
            ctx["data"]["topic_memory_selected"] = await api.topic_memory_get(
                ctx["scope"],
                {
                    "family": "topic-memory",
                    "artifact_id": ctx["topic_artifact"],
                    "revision": int(ctx["topic_revision"]),
                },
            )
        except ValueError:
            ctx["errors"]["topic_memory_selected"] = ReadError(422, "invalid_request")
        except ReadError as error:
            ctx["errors"]["topic_memory_selected"] = error
    if ctx["topic_artifact"] and ctx["topic_revision"] and ctx["topic_revision"].isdigit():
        ctx["history"] = await load_history(
            api,
            request,
            ctx,
            family="topic-memory",
            artifact_id=ctx["topic_artifact"],
            selected=int(ctx["topic_revision"]),
            cursor_key="topic_revision_cursor",
        )


async def load_prompts(api: DashboardAPI, request: Request, ctx: dict[str, Any]) -> None:
    """Load the scoped Prompt configurations exposed by the Prompt Dashboard."""
    keys = (
        "atomic_memory.extract",
        "atomic_memory.reconcile",
        "memory.rerank",
        "experience.incubate",
        "experience.generate",
        "skill.generate",
        "handoff.generate",
    )
    for value in await api.prompt_configurations(ctx["scope"], keys):
        if isinstance(value, ReadError):
            ctx["errors"].setdefault("prompts", value)
        else:
            ctx["data"]["prompts"].append(value)
    selected_key = request.query_params.get("prompt_key")
    if selected_key:
        configuration = next((item for item in ctx["data"]["prompts"] if item["prompt_key"] == selected_key), None)
        artifact = None if configuration is None else configuration.get("artifact")
        if artifact and str(artifact.get("revision", "")).isdigit():
            selected = request.query_params.get("prompt_revision") or str(artifact["revision"])
            if str(selected).isdigit():
                ctx["prompt_key"] = selected_key
                try:
                    ctx["prompt_opened"] = await api.artifact_revision(
                        ctx["scope"], "prompt", selected_key, int(selected)
                    )
                except ReadError as error:
                    # Showing the current configuration here would mislabel it as the requested revision.
                    ctx["errors"]["prompt_selected"] = error
                    return
                ctx["history"] = await load_history(
                    api,
                    request,
                    ctx,
                    family="prompt",
                    artifact_id=selected_key,
                    selected=int(selected),
                    cursor_key="prompt_revision_cursor",
                )


async def load_note_history(api: DashboardAPI, request: Request, ctx: dict[str, Any], *, snapshot=None) -> None:
    """Show the selected Atomic Memory's own revisions. Restoration uses its own preview flow."""

    note = ctx.get("selected_note")
    ref = note.get("artifact") if isinstance(note, dict) else None
    if not isinstance(ref, dict):
        return
    ctx["history"] = await load_history(
        api,
        request,
        ctx,
        family="atomic-memory",
        artifact_id=ref["artifact_id"],
        selected=ref["revision"],
        cursor_key="notes_revision_cursor",
        snapshot=snapshot,
    )


async def load_profile(api: DashboardAPI, request: Request, ctx: dict[str, Any], *, include_history=True) -> None:
    query = request.query_params
    revision = query.get("revision")
    view = query.get("view")
    if view is not None and view != "history":
        raise ReadError(422, "invalid_request")
    scope = ctx["scope"]
    selected_revision = revision
    if revision is None:
        # An absent artifact has no owner; the scope-authorized collection is the
        # only reliable empty-state discovery under enforced access control.
        heads = await api.read(f"/v1/scopes/{segment(scope)}/artifacts/profile?limit=1")
        if not heads["items"]:
            return
        selected_revision = str(heads["items"][0]["revision"])
    if include_history:
        ctx["history"] = await load_history(
            api,
            request,
            ctx,
            family="profile",
            artifact_id="profile",
            selected=positive_revision(selected_revision),
            cursor_key="profile_cursor" if view == "history" else "profile_revision_cursor",
        )
    if view == "history" and include_history:
        history = ctx["history"]
        if not history["loaded"]:
            raise history["error"]
        ctx["profile_revisions"] = history["items"]
        ctx["profile_pager"] = cursor_links(request, ctx, "profile", history["next_cursor"])
        if revision is None:
            return
    record = await api.record(scope, "profile", "profile", positive_revision(selected_revision))
    ctx["data"]["profile"] = record
    ctx["profile_html"] = profile_html(record["content"])
    ctx["source_record"] = record
    ctx["related_sources"] = [source for source in record["sources"] if source["source_type"] == "content"]
