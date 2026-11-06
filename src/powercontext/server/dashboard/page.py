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

"""Own the built-in page's authorization, snapshot, projection and audit lifecycle."""

import asyncio
from contextlib import asynccontextmanager, nullcontext

from powercontext.builtin.authz import AccessAction, AccessDeniedError, ResourceRef
from powercontext.builtin.authz.service import _require_allowed, access_control_for_mode
from powercontext.builtin.scope.errors import ScopeBindingTargetMissingError
from powercontext.builtin.sources import validate_scope_id
from powercontext.server.context import current_principal
from powercontext.server.dashboard.content import (
    RECORDS,
    load_content,
    load_note_history,
    present_search,
    validate_note_selection,
)
from powercontext.server.dashboard.errors import ReadError
from powercontext.server.dashboard.pagination import PAGE_SIZE, cursor_links
from powercontext.server.dashboard.presenters import memory_view, set_scope, usage_view


@asynccontextmanager
async def _snapshot(runtime, request, ctx, *, default_scope=False):  # noqa: C901
    from powercontext.server.app import _access_audit_context, _atomic_execution_context

    access = access_control_for_mode(request.app.state.access_control, mode=request.app.state.access_mode)
    page_error = None
    try:
        scope = validate_scope_id(ctx["scope"])
        resource = ResourceRef.scope(scope)
        audit = _access_audit_context(f"dashboard_{ctx['page']}")
        execution = _atomic_execution_context(request, audit.operation)
        checks = [(AccessAction.SCOPE_READ, resource)]
        selected_artifact = (
            ctx["requested_note"]
            if ctx["page"] == "notes"
            else request.query_params.get("artifact")
            if ctx["page"] in RECORDS
            else None
        )
        if selected_artifact:
            checks.append((
                AccessAction.ARTIFACT_READ,
                ResourceRef.artifact(
                    scope,
                    family="atomic-memory" if ctx["page"] == "notes" else RECORDS[ctx["page"]],
                    artifact_id=selected_artifact,
                ),
            ))
        if default_scope and access is not None:
            checks.append((AccessAction.SERVER_OBSERVE, ResourceRef.server(access.deployment_id)))
        async with asyncio.timeout(20):
            for attempt in range(2):
                async with (
                    access.defer_decision_audit() if access is not None else nullcontext(),
                    runtime.read_snapshot(scope, flush_usage=ctx["page"] in {"home", "usage"}) as snapshot,
                ):
                    decisions = None
                    if access is not None:
                        decisions = await access.for_read(snapshot.connection).check_batch(
                            current_principal(), checks, context=audit
                        )
                        if default_scope:
                            _require_allowed(decisions[-1])
                    try:
                        if decisions is not None:
                            ctx["record_only"] = not decisions[0].allowed
                            selected = decisions[1] if selected_artifact else decisions[0]
                            _require_allowed(selected if ctx["record_only"] else decisions[0])
                    except AccessDeniedError:
                        if (
                            attempt
                            or access is None
                            or not access.can_bootstrap_static_scope(
                                current_principal(), AccessAction.SCOPE_READ, resource
                            )
                        ):
                            raise
                    else:
                        try:
                            yield snapshot, execution
                        except Exception as error:
                            page_error = error
                            if ctx["scope_descriptor"] is None and not ctx["record_only"]:
                                descriptor = await snapshot.scope()
                                if default_scope and descriptor is None:
                                    raise ScopeBindingTargetMissingError(scope) from error
                                set_scope(ctx, descriptor)
                            raise
                        return
                # A cold preset retry starts a new snapshot after its writes commit.
                if access is not None:
                    await access.bootstrap_static_scope(current_principal(), scope, context=audit)

    except Exception as error:
        if error is not page_error:
            # Transaction and audit completion precede exposing any page projection.
            from powercontext.server.dashboard.routes import initial_context

            empty = initial_context(request, ctx["page"])
            ctx.clear()
            ctx.update(empty)
        raise ReadError.from_exception(error) from error


async def _load_usage(snapshot, ctx):
    try:
        usage, recall, descriptor = await snapshot.usage(ctx["period"], include_scope=ctx["page"] == "usage")
        if ctx["page"] == "usage":
            set_scope(ctx, descriptor)
        ctx["stats"] = usage_view(usage.model_dump(mode="json"), recall.model_dump(mode="json"))
    except Exception as error:
        ctx["errors"]["usage"] = ReadError.from_exception(error)
        if ctx["page"] == "usage" and ctx["scope_descriptor"] is None:
            set_scope(ctx, await snapshot.scope())


async def _select_note(snapshot, rows, ctx):
    selected = ctx.get("note_selection") or next(iter(ctx["data"]["notes"]), None)
    if selected is not None:
        row = next(
            (
                row
                for row in rows
                if row["artifact_id"] == selected["artifact_id"] and row["revision"] == selected["revision"]
            ),
            None,
        )
        if row is None:
            raise ReadError(404, "not_found")
        value = await snapshot.artifact(row)
        ctx["selected_note"] = {
            **memory_view({"items": [snapshot.preview(row)]})[0],
            "sources": [source.model_dump(mode="json") for source in value.lineage.sources],
            "artifacts": [ref.model_dump(mode="json") for ref in value.lineage.artifacts],
        }
        if "matched_by" in selected:
            ctx["selected_note"].update(matched_by=selected["matched_by"], score=selected["score"])


async def _load_content(snapshot, request, ctx, execution):
    validate_note_selection(request, ctx)
    home = ctx["page"] == "home"
    searching = bool(ctx["search_query"]) and ctx["page"] == "notes"
    selection = ctx.get("note_selection") or (next(iter(ctx["data"]["notes"]), None) if searching else None)
    try:
        rows, notes, cursor, descriptor = await snapshot.content(
            state=ctx["note_state"],
            limit=3 if home else PAGE_SIZE,
            cursor=None if home else request.query_params.get("notes_cursor"),
            selected=selection,
            families=("handoff", "experience", "skill") if home else (),
            browse=not ctx["record_only"] and not searching,
            include_scope=not ctx["record_only"],
            context=execution,
        )
    except Exception as error:
        if not home:
            raise ReadError.from_exception(error) from error
        failure = ReadError.from_exception(error)
        ctx["errors"].update(dict.fromkeys(("notes", "handoff", "experience", "skill"), failure))
        set_scope(ctx, await snapshot.scope())
        return
    if not ctx["record_only"]:
        set_scope(ctx, descriptor)
    try:
        if not searching:
            ctx["data"]["notes"] = memory_view({"items": [snapshot.preview(row) for row in notes]})
    except Exception as error:
        ctx["errors"]["notes"] = ReadError.from_exception(error)
    if home:
        for family in ("handoff", "experience", "skill"):
            try:
                items = [snapshot.preview(row) for row in rows if row["read_kind"] == family]
                ctx["collections"][family] = {"items": items, "next_cursor": None}
                ctx["data"][family] = next(iter(items), None)
            except Exception as error:
                ctx["errors"][family] = ReadError.from_exception(error)
        return
    if not searching:
        ctx["notes_pager"] = cursor_links(request, ctx, "notes", cursor)
    await _select_note(snapshot, rows, ctx)


async def _resolve_default(api, request, ctx):
    try:
        async with asyncio.timeout(20):
            ctx["scope"] = await api.builtin.scopes.default_scope_id()
            if ctx["scope"] is None:
                from powercontext.server.app import _access_audit_context

                access = access_control_for_mode(request.app.state.access_control, mode=request.app.state.access_mode)
                if access is not None:
                    await access.require(
                        current_principal(),
                        AccessAction.SERVER_OBSERVE,
                        ResourceRef.server(access.deployment_id),
                        context=_access_audit_context(f"dashboard_{ctx['page']}"),
                    )
                return False
    except Exception as error:
        raise ReadError.from_exception(error) from error
    return ctx["scope"] is not None


async def _search_notes(api, request, ctx, *, default_scope):
    from powercontext.server import mapping
    from powercontext.server.app import _access_audit_context, _atomic_execution_context

    validate_note_selection(request, ctx)
    if ctx["search_mode"] not in {"fts", "vector", "hybrid", "auto"}:
        ctx["errors"]["notes"] = ReadError(422, "invalid_request")
        return
    access = access_control_for_mode(request.app.state.access_control, mode=request.app.state.access_mode)
    try:
        if default_scope and access is not None:
            await access.require(
                current_principal(),
                AccessAction.SERVER_OBSERVE,
                ResourceRef.server(access.deployment_id),
                context=_access_audit_context("dashboard_notes"),
            )
    except Exception as error:
        raise ReadError.from_exception(error) from error
    try:
        result = await api.builtin.atomic_memory.for_scope(ctx["scope"]).search(
            ctx["search_query"],
            mode="text" if ctx["search_mode"] == "fts" else ctx["search_mode"],
            limit=50,
            context=_atomic_execution_context(request, "dashboard_notes_search"),
        )
        present_search(mapping.search_response(result).model_dump(mode="json"), request, ctx)
    except Exception as error:
        ctx["errors"]["notes"] = ReadError.from_exception(error)


async def load_builtin_page(api, request, ctx) -> bool:  # noqa: C901 - Keep the page lifecycle explicit.
    """Return whether the page used the built-in snapshot instead of public reads."""
    runtime = api.builtin
    if runtime is None or ctx["scope"] == "":
        return False
    default_scope = ctx["scope"] is None
    if default_scope and not await _resolve_default(api, request, ctx):
        return True
    if ctx["page"] not in {"home", "notes", "usage"}:
        async with _snapshot(runtime, request, ctx, default_scope=default_scope) as (snapshot, _execution):
            if not ctx["record_only"]:
                set_scope(ctx, await snapshot.scope())
        # Each detail, history and mutation keeps its own current authorization.
        # Finish the metadata snapshot before a transport adapter can make reads.
        if ctx["page"] == "review":
            from powercontext.server.dashboard.review import load_review

            await load_review(api, request, ctx)
        else:
            await load_content(api, request, ctx)
        return True
    if ctx["page"] == "notes" and ctx["search_query"]:
        await _search_notes(api, request, ctx, default_scope=default_scope)
    async with _snapshot(runtime, request, ctx, default_scope=default_scope) as (snapshot, execution):
        if ctx["page"] in {"home", "notes"}:
            await _load_content(snapshot, request, ctx, execution)
        if ctx["page"] == "notes":
            await load_note_history(api, request, ctx, snapshot=snapshot)
        if ctx["page"] in {"home", "usage"}:
            await _load_usage(snapshot, ctx)
        if ctx["errors"] and not ctx["scope_descriptor"] and not ctx["record_only"]:
            raise next(iter(ctx["errors"].values()))
    return True
