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

from powercontext.server.dashboard.api import DashboardAPI, ReadError
from powercontext.server.dashboard.pagination import PAGE_SIZE, cursor_links, list_links, list_page
from powercontext.server.dashboard.presenters import memory_view, usage_view

RECORDS = {"handoff-detail": "handoff", "experience": "experience", "skill": "skill"}


async def load_collection(api: DashboardAPI, request: Request, ctx: dict[str, Any], family: str) -> None:
    page, scope = ctx["page"], ctx["scope"]
    ctx["collection_page_size"] = PAGE_SIZE
    try:
        cursor = request.query_params.get(f"{family}_cursor" if page == "methods" else "cursor")
        result = await api.records(
            scope,
            family,
            cursor=cursor,
            limit=1 if page == "home" else PAGE_SIZE,
            query=ctx["search_query"] if family == "skill" and page == "methods" else None,
        )
        if page != "home":
            if family == "skill":
                window = list_page(result["items"], request.query_params.get("skill_page"))
                result["items"] = window["items"]
                result["pager"] = list_links(ctx, family, window)
            else:
                result["pager"] = cursor_links(request, ctx, family, result["next_cursor"])
        ctx["collections"][family] = result
        ctx["data"][family] = next((item for item in result["items"] if "error" not in item), None)
        for item in result["items"]:
            if "error" in item:
                ctx["errors"][family] = item["error"]
    except ReadError as error:
        ctx["errors"][family] = error


async def load_notes(api: DashboardAPI, ctx: dict[str, Any]) -> None:
    try:
        if ctx["page"] == "notes" and ctx["search_query"]:
            result = await api.read(
                "/v1/memory/search",
                {"scope_id": ctx["scope"], "query": ctx["search_query"], "mode": "fts", "limit": 50},
            )
            ctx["data"]["notes"] = [{**hit, **hit["citation"]} for hit in result["hits"]]
            ctx["search_limited"] = len(result["hits"]) == 50
        else:
            ctx["data"]["notes"] = memory_view(await api.read("/v1/memory/entries/list", {"scope_id": ctx["scope"]}))
    except ReadError as error:
        ctx["errors"]["notes"] = error


async def load_stats(api: DashboardAPI, ctx: dict[str, Any]) -> None:
    selection = {"mode": "exact", "scope_ids": [ctx["scope"]]}
    try:
        result = await api.read("/v1/stats", {"selection": selection, "period": ctx["period"]})
        ctx["stats"] = usage_view(result["usage"], result["recall"])
        ctx["stats"]["scope_ids"] = result["scope_ids"]
    except ReadError as error:
        ctx["errors"]["usage"] = error


async def select_note(api: DashboardAPI, request: Request, ctx: dict[str, Any]) -> None:
    scope = ctx["scope"]
    selected = request.query_params.get("entry")
    if selected:
        current = next((item for item in ctx["data"]["notes"] if item["entry_id"] == selected), None)
        query = request.query_params
        identity_fields = {"memory_id", "memory_revision", "entry_version"}
        if identity_fields.intersection(query) and not identity_fields.issubset(query):
            raise ReadError(422, "invalid_request")
        if all(key in query for key in ("memory_id", "memory_revision", "entry_version")):
            try:
                citation = {
                    "memory_ref": {
                        "family": "memory",
                        "artifact_id": query["memory_id"],
                        "revision": int(query["memory_revision"]),
                    },
                    "entry_id": selected,
                    "entry_version_id": query["entry_version"],
                }
            except ValueError as error:
                raise ReadError(422, "invalid_request") from error
        elif current:
            citation = current["citation"]
        else:
            raise ReadError(404, "not_found")
        entry = await api.read("/v1/memory/entries/get", {"scope_id": scope, "citation": citation})
        ctx["selected_note"] = {**entry, **entry["citation"]}
    elif ctx["data"]["notes"]:
        ctx["selected_note"] = ctx["data"]["notes"][0]


async def load_record(api: DashboardAPI, request: Request, ctx: dict[str, Any]) -> None:
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


async def load_content(api: DashboardAPI, request: Request, ctx: dict[str, Any]) -> None:
    page = ctx["page"]
    if page == "home":
        await asyncio.gather(
            load_notes(api, ctx),
            load_stats(api, ctx),
            *(load_collection(api, request, ctx, family) for family in ("handoff", "experience", "skill")),
        )
    elif page == "notes":
        await load_notes(api, ctx)
        window = list_page(
            ctx["data"]["notes"], request.query_params.get("notes_page"), request.query_params.get("entry")
        )
        ctx["data"]["notes"] = window["items"]
        if ctx["search_query"]:
            entries = await asyncio.gather(
                *(
                    api.read("/v1/memory/entries/get", {"scope_id": ctx["scope"], "citation": hit["citation"]})
                    for hit in window["items"]
                )
            )
            ctx["data"]["notes"] = memory_view({"entries": entries})
        ctx["notes_pager"] = list_links(ctx, "notes", window)
        ctx["notes_page_size"] = PAGE_SIZE
        await select_note(api, request, ctx)
    elif page == "handoff":
        await load_collection(api, request, ctx, "handoff")
    elif page == "methods":
        await load_collection(api, request, ctx, ctx["method_kind"])
    elif page == "usage":
        await load_stats(api, ctx)
    elif page in RECORDS:
        await load_record(api, request, ctx)
