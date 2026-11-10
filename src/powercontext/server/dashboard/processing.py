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

from datetime import UTC, date, datetime, time, timedelta
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
                    for name in ("text", "summary", "lesson", "description", "content", "instructions", "detail")
                    if isinstance(content.get(name), str)
                ),
                None,
            )
            if isinstance(content, dict)
            else content
            if isinstance(content, str)
            else None
        )
        ctx["processing_memory_previews"][key] = {"title": title, "text": text, "error": None}
    except ReadError as error:
        ctx["processing_memory_previews"][key] = {"title": None, "text": None, "error": error}


async def load_memory_previews(api, ctx):
    """Read a bounded set of exact result bodies, never substitute input text."""
    history = ctx["processing_history"] or {}
    records = history.get("items", [])
    refs = [] if history.get("direct_artifact") is None else [history["direct_artifact"]]
    if records:
        refs.extend(result["artifact"] for result in records[0]["results"])
        refs.extend(records[0]["retained_artifacts"])
    refs.extend(ctx.get("source_artifacts") or [])
    for record in records[1:]:
        refs.extend(result["artifact"] for result in record["results"])
        refs.extend(record["retained_artifacts"])
    selected = {f"{ref['family']}/{ref['artifact_id']}@{ref['revision']}": ref for ref in refs}
    for ref in list(selected.values())[:20]:
        await _load_memory_preview(api, ctx, ref)


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


ATTEMPT_STATUSES = ("started", "succeeded", "failed", "cancelled", "conflict")
DREAM_STATUSES = ("queued", "running", "succeeded", "failed")


def _day_bounds(raw: str) -> tuple[str, str]:
    """Bound one UTC calendar day; the page states the zone instead of guessing a local one."""
    try:
        selected = date.fromisoformat(raw)
    except ValueError as error:
        raise ReadError(422, "invalid_request") from error
    start = datetime.combine(selected, time.min, tzinfo=UTC)
    return start.isoformat(), (start + timedelta(days=1)).isoformat()


def _validate_query(kind: str, source_id, run_id, status, day) -> None:
    allowed_status = {
        "overview": (None,),
        "sources": (None,),
        "dreams": (None, *DREAM_STATUSES),
        "attempts": (None, *ATTEMPT_STATUSES),
    }
    if (
        kind not in allowed_status
        or (source_id is not None and (kind != "sources" or not source_id or len(source_id) > 256))
        or (run_id is not None and (kind != "dreams" or not run_id or len(run_id) > 64))
        or status not in allowed_status.get(kind, ())
        or (day is not None and kind != "attempts")
        or (kind == "overview" and (source_id is not None or run_id is not None or status is not None))
    ):
        raise ReadError(422, "invalid_request")


def _period_bounds(period: str, start: str = "", end: str = "") -> tuple[str, str]:
    """Resolve inclusive UTC dates to a bounded window stable across pagination."""
    if period == "custom":
        try:
            first, last = date.fromisoformat(start), date.fromisoformat(end)
            if first.isoformat() != start or last.isoformat() != end or not 0 <= (last - first).days < 92:
                raise ValueError  # noqa: TRY301 - Normalize all invalid date ranges at the form boundary.
            since = datetime.combine(first, time.min, tzinfo=UTC)
            until = datetime.combine(last, time.min, tzinfo=UTC) + timedelta(days=1)
        except (ValueError, OverflowError) as error:
            raise ReadError(422, "invalid_request") from error
        return since.isoformat(), until.isoformat()
    days = {"today": 1, "7d": 7, "30d": 30, "90d": 90}.get(period)
    if days is None:
        raise ReadError(422, "invalid_request")
    today = datetime.combine(datetime.now(UTC).date(), time.min, tzinfo=UTC)
    return (today - timedelta(days=days - 1)).isoformat(), (today + timedelta(days=1)).isoformat()


def _resolve_window(ctx) -> bool:
    """Keep invalid selections editable without querying a different reporting window."""
    try:
        since, until = _period_bounds(ctx["processing_period"], ctx["processing_start"], ctx["processing_end"])
    except ReadError as error:
        ctx["errors"]["processing_window"] = error
        ctx["status"] = error.status
        return False
    ctx["processing_bounds"] = since, until
    ctx["processing_start"] = since[:10]
    ctx["processing_end"] = (datetime.fromisoformat(until).date() - timedelta(days=1)).isoformat()
    return True


async def _load_topic_status(api, ctx) -> None:
    try:
        ctx["processing_topic_status"] = await api.read(
            "/v1/topic-memory/processing/status", {"scope_id": ctx["scope"]}
        )
    except ReadError as error:
        ctx["errors"]["processing_topic_status"] = error


async def _load_overview(api, ctx, scope) -> None:
    since, until = ctx["processing_bounds"]
    try:
        ctx["processing_overview"] = await api.read(
            f"/v1/scopes/{scope}/processing/summary?{urlencode({'since': since, 'until': until})}"
        )
    except ReadError as error:
        ctx["errors"]["processing_overview"] = error

    await _load_topic_status(api, ctx)
    if api.builtin is not None:
        try:
            directory = await api.project(ctx["scope"], "source_directory", limit=6, cursor=None)
            ctx["processing_recent_sources"] = [present_source(item) for item in directory["items"]]
        except ReadError as error:
            ctx["errors"]["processing_recent_sources"] = error


async def _load_attempts(api, request, ctx, scope, status, day) -> None:
    await _load_topic_status(api, ctx)
    params: dict[str, str | int] = {"limit": PAGE_SIZE}
    if cursor := request.query_params.get("processing_attempts_cursor"):
        params["cursor"] = cursor
    if status is not None:
        params["status"] = status
    ctx["processing_summary_window"] = ctx["processing_period"]
    if day is not None:
        params["since"], params["until"] = _day_bounds(day)
        ctx["processing_summary_window"] = "day"
    else:
        params["since"], params["until"] = ctx["processing_bounds"]
    summary_params = {key: params[key] for key in ("since", "until") if key in params}
    try:
        ctx["processing_summary"] = await api.read(f"/v1/scopes/{scope}/processing/summary?{urlencode(summary_params)}")
    except ReadError as error:
        ctx["errors"]["processing_summary"] = error
    try:
        result = await api.read(f"/v1/scopes/{scope}/processing?{urlencode(params)}")
        ctx["processing_items"] = result["items"]
        previews = {}
        for item in result["items"]:
            item["source_previews"] = []
            for ref in item["sources"][:3]:
                key = (ref["source_type"], ref["source_id"])
                if key not in previews:
                    try:
                        value = await api.read(f"/v1/scopes/{scope}/sources/{segment(key[0])}/{segment(key[1])}")
                        previews[key] = {
                            "source_id": key[1],
                            "source_type": key[0],
                            "preview": present_source(value)["preview"],
                        }
                    except ReadError:
                        previews[key] = {"source_id": key[1], "source_type": key[0], "preview": None}
                item["source_previews"].append(previews[key])
        ctx["processing_pager"] = cursor_links(request, ctx, "processing_attempts", result.get("next_cursor"))
    except ReadError as error:
        ctx["errors"]["processing"] = error


def _request_kind(request) -> str:
    """Keep the entry point implicit so old links still open the page they meant."""
    kind = request.query_params.get("record_type")
    if kind is not None:
        return kind
    if request.query_params.get("source_id"):
        return "sources"
    return "dreams" if request.query_params.get("run_id") else "overview"


async def _load_source_history(api, request, ctx, scope, source_id) -> None:
    ctx["processing_source"] = present_source(
        await api.read(f"/v1/scopes/{scope}/sources/content/{segment(source_id)}")
    )
    try:
        # Keep a full bounded page so attempt ordinals stay stable for typical histories.
        params = {"limit": 20}
        if cursor := request.query_params.get("processing_attempts_cursor"):
            params["cursor"] = cursor
        history = await api.read(
            f"/v1/scopes/{scope}/sources/content/{segment(source_id)}/processing?{urlencode(params)}"
        )
        if not history.get("next_cursor"):
            history["total"] = len(history["items"])
            history["offset"] = 0
        ctx["processing_history"] = history
        ctx["processing_attempts_pager"] = cursor_links(
            request,
            ctx,
            "processing_attempts",
            ctx["processing_history"].get("next_cursor"),
        )
    except ReadError as error:
        ctx["errors"]["processing_history"] = error
    if api.builtin is None:
        await load_memory_previews(api, ctx)


async def _load_list_context(api, ctx, scope):
    """Read source diagnostics or Dream input previews for the visible list."""
    if ctx["processing_kind"] == "dreams":
        await load_run_previews(api, ctx, ctx["processing_items"])
        return
    if ctx["processing_kind"] != "sources":
        return
    await _load_topic_status(api, ctx)
    ctx["processing_counts"] = {}
    ctx["processing_results"] = {}
    for item in ctx["processing_items"]:
        try:
            history = await api.read(
                f"/v1/scopes/{scope}/sources/{segment(item['source_type'])}/{segment(item['source_id'])}/processing?limit=20"
            )
            ctx["processing_counts"][item["source_id"]] = {
                "count": len(history["items"]),
                "more": bool(history.get("next_cursor")),
            }
            records = history["items"]
            latest_results = (records[0].get("results") or []) if records else []
            results = [
                {"action": result["action"], "ref": result["artifact"]}
                for result in latest_results
                if any(
                    ref["source_type"] == item["source_type"] and ref["source_id"] == item["source_id"]
                    for ref in result["source_refs"]
                )
            ]
            ctx["processing_results"][item["source_id"]] = results[:2]
            for result in results[:2]:
                await _load_memory_preview(api, ctx, result["ref"])
        except ReadError:
            # Unknown history must never look like zero recorded executions.
            ctx["processing_counts"][item["source_id"]] = None
            ctx["processing_results"][item["source_id"]] = []


async def load_processing(api, request, ctx):
    kind = _request_kind(request)
    source_id = request.query_params.get("source_id")
    run_id = request.query_params.get("run_id")
    status = request.query_params.get("run_status") or None
    day = request.query_params.get("processing_day") or None
    _validate_query(kind, source_id, run_id, status, day)
    ctx["processing_kind"] = kind
    ctx["processing_status"] = status
    ctx["processing_day"] = day
    ctx["artifact_destination"] = lambda ref: artifact_destination(ctx, ref)
    scope = segment(ctx["scope"])
    if source_id is not None:
        await _load_source_history(api, request, ctx, scope, source_id)
        return
    if kind in {"overview", "attempts"} and not _resolve_window(ctx):
        return
    if kind == "overview":
        await _load_overview(api, ctx, scope)
        return
    if run_id is not None:
        ctx["processing_run"] = await api.read(f"/v1/scopes/{scope}/dream/{segment(run_id)}")
        ctx["processing_run_sources"] = manifest_sources(ctx["processing_run"])
        await load_run_previews(api, ctx, [ctx["processing_run"]])
        return
    family = {"sources": "processing_sources", "dreams": "processing_runs"}.get(kind, "processing_attempts")
    params: dict[str, str | int] = {"limit": PAGE_SIZE}
    cursor = request.query_params.get(family + "_cursor")
    if cursor is not None:
        params["cursor"] = cursor
    if kind == "dreams" and status is not None:
        params["status"] = status
    if kind == "attempts":
        await _load_attempts(api, request, ctx, scope, status, day)
        return
    path = "sources" if kind == "sources" else "dream" if kind == "dreams" else "processing"
    try:
        if kind == "sources" and api.builtin is not None:
            result = await api.project(ctx["scope"], "source_directory", limit=PAGE_SIZE, cursor=cursor)
            ctx["processing_order"] = "newest"
            ctx["processing_items"] = [present_source(item) for item in result["items"]]
        else:
            result = await api.read(f"/v1/scopes/{scope}/{path}?{urlencode(params)}")
            ctx["processing_items"] = (
                result["runs"] if kind == "dreams" else [present_source(item) for item in result["items"]]
            )
        ctx["processing_pager"] = cursor_links(request, ctx, family, result.get("next_cursor"))
        await _load_list_context(api, ctx, scope)
    except ReadError as error:
        ctx["errors"]["processing"] = error


async def load_home_processing(api, ctx):
    """Reuse processing facts on Home without loading a second source directory."""
    import asyncio

    async def summary():
        try:
            ctx["processing_overview"] = await api.project(ctx["scope"], "processing_summary", all_time=True)
        except ReadError as error:
            ctx["errors"]["processing_overview"] = error

    await asyncio.gather(summary(), _load_topic_status(api, ctx))
