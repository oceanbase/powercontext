# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Read durable inputs and recorded Dream executions without inferring missing history."""

from typing import Any, cast
from urllib.parse import urlencode

from powercontext.server.dashboard.api import segment
from powercontext.server.dashboard.errors import ReadError
from powercontext.server.dashboard.pagination import PAGE_SIZE, cursor_links
from powercontext.server.dashboard.presenters import artifact_destination, source_view


def present_source(value):
    source = cast(dict[str, Any], source_view(value))
    source.setdefault("saved_at", None)
    content = value["content"]
    preview = (
        next(
            (
                content[key]
                for key in ("text", "title", "lesson", "objective", "description", "name", "content")
                if isinstance(content.get(key), str) and content[key].strip()
            ),
            source["text"],
        )
        if isinstance(content, dict)
        else content
    )
    source["preview"] = preview if isinstance(preview, str) else source["text"]
    return source


def manifest_sources(run):
    """Keep explicit and resolved source identities from the saved manifest only."""
    manifest = run.get("input_manifest")
    if manifest is None:
        return []
    sources = {(item["source_type"], item["source_id"]): item for item in manifest["sources"]}
    for node in manifest["nodes"]:
        source = node.get("source")
        if source is not None:
            sources.setdefault((source["source_type"], source["source_id"]), source)
    return list(sources.values())


async def _load_memory_preview(api, ctx, ref) -> None:
    key = f"{ref['family']}/{ref['artifact_id']}@{ref['revision']}"
    if key in ctx["processing_memory_previews"]:
        return
    try:
        value = await api.artifact_revision(ctx["scope"], ref["family"], ref["artifact_id"], ref["revision"])
        content = value["content"]
        title = (
            next(
                (content[name] for name in ("title", "name", "objective") if isinstance(content.get(name), str)),
                None,
            )
            if isinstance(content, dict)
            else None
        )
        text = (
            next(
                (
                    content[name]
                    for name in ("text", "summary", "lesson", "instructions", "description", "content", "detail")
                    if isinstance(content.get(name), str)
                ),
                None,
            )
            if isinstance(content, dict)
            else content
            if isinstance(content, str)
            else None
        )
        description = content.get("description") if isinstance(content, dict) else None
        ctx["processing_memory_previews"][key] = {
            "title": title,
            "text": text,
            "description": description if isinstance(description, str) and description not in (title, text) else None,
            "error": None,
        }
    except ReadError as error:
        ctx["processing_memory_previews"][key] = {"title": None, "text": None, "error": error}


async def load_run_previews(api, ctx, runs):
    """Resolve only the visible Dream inputs, with the same bounded read as results."""
    selected = {}
    for run in runs:
        manifest = run.get("input_manifest")
        if manifest is None:
            continue
        for ref in manifest["artifacts"]:
            selected.setdefault(f"{ref['family']}/{ref['artifact_id']}@{ref['revision']}", ref)
    for ref in list(selected.values())[:20]:
        await _load_memory_preview(api, ctx, ref)


async def load_topic_status(api, ctx) -> None:
    try:
        ctx["processing_topic_status"] = await api.read(
            "/v1/topic-memory/processing/status", {"scope_id": ctx["scope"]}
        )
    except ReadError as error:
        ctx["errors"]["processing_topic_status"] = error


async def load_memory_previews(api, ctx):
    for ref in (ctx.get("source_artifacts") or [])[:20]:
        await _load_memory_preview(api, ctx, ref)


def _validate_selection(kind, source_id, run_id, status):
    if (
        kind not in {"sources", "dreams"}
        or (source_id is not None and (kind != "sources" or not source_id or len(source_id) > 256))
        or (run_id is not None and (kind != "dreams" or not run_id or len(run_id) > 64))
        or (status is not None and (kind != "dreams" or status not in {"queued", "running", "succeeded", "failed"}))
    ):
        raise ReadError(422, "invalid_request")


async def load_processing(api, request, ctx):
    kind = request.query_params.get("record_type") or ("dreams" if request.query_params.get("run_id") else "sources")
    if kind in {"overview", "attempts"}:
        kind = "sources"
    source_id = request.query_params.get("source_id")
    run_id = request.query_params.get("run_id")
    status = (request.query_params.get("run_status") or None) if kind == "dreams" else None
    _validate_selection(kind, source_id, run_id, status)
    ctx["processing_show_topic_status"] = request.query_params.get("record_type") != "sources"
    ctx.update(processing_kind=kind, processing_status=status)
    ctx["artifact_destination"] = lambda ref: artifact_destination(ctx, ref)
    scope = segment(ctx["scope"])
    if kind == "sources":
        if ctx["processing_show_topic_status"]:
            await load_topic_status(api, ctx)
        if source_id is not None:
            ctx["processing_source"] = present_source(
                await api.read(f"/v1/scopes/{scope}/sources/content/{segment(source_id)}")
            )
            if api.builtin is not None:
                result = await api.project(
                    ctx["scope"],
                    "source_artifacts",
                    source_id=source_id,
                    family=request.query_params.get("artifact_family"),
                    cursor=request.query_params.get("processing_artifacts_cursor"),
                )
                ctx["source_artifacts"] = result["items"]
                ctx["processing_artifacts_pager"] = cursor_links(
                    request, ctx, "processing_artifacts", result["next_cursor"]
                )
            await load_memory_previews(api, ctx)
            return
        family = "processing_sources"
        cursor = request.query_params.get(family + "_cursor")
        try:
            result = await api.source_directory(ctx["scope"], limit=PAGE_SIZE, cursor=cursor)
        except ReadError as error:
            ctx["errors"]["processing"] = error
            return
        ctx["processing_items"] = [
            present_source({"has_artifact": False, "artifact_families": None, **item}) for item in result["items"]
        ]
    else:
        if run_id is not None:
            ctx["processing_run"] = await api.read(f"/v1/scopes/{scope}/dream/{segment(run_id)}")
            ctx["processing_run_sources"] = manifest_sources(ctx["processing_run"])
            await load_run_previews(api, ctx, [ctx["processing_run"]])
            return
        family = "processing_runs"
        params = {"limit": PAGE_SIZE}
        if cursor := request.query_params.get(family + "_cursor"):
            params["cursor"] = cursor
        if status:
            params["status"] = status
        result = await api.read(f"/v1/scopes/{scope}/dream?{urlencode(params)}")
        ctx["processing_items"] = result["runs"]
        await load_run_previews(api, ctx, result["runs"])
    ctx["processing_pager"] = cursor_links(request, ctx, family, result.get("next_cursor"))
