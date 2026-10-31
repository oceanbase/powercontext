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

"""Read exact usage details and save explicitly attributed, idempotent feedback."""

from uuid import uuid4

from pydantic import ValidationError

from powercontext.builtin.context_usage import ContextFeedbackRequest
from powercontext.server.dashboard.errors import ReadError
from powercontext.server.dashboard.pagination import PAGE_SIZE, cursor_links
from powercontext.server.dashboard.presenters import artifact_destination
from powercontext.server.dashboard.session import same_origin


async def load_usage_records(snapshot, request, ctx, execution):
    identity = request.query_params.get("usage_id")
    kind = request.query_params.get("usage_kind")
    if (identity is None) != (kind is None):
        raise ReadError(422, "invalid_request")
    try:
        result = await snapshot.context_usage(
            period=ctx["period"],
            limit=PAGE_SIZE,
            cursor=request.query_params.get("usage_cursor"),
            record_kind=kind,
            record_id=identity,
            context=execution,
        )
    except Exception as error:
        if identity:
            raise ReadError.from_exception(error) from error
        ctx["errors"]["usage_records"] = ReadError.from_exception(error)
        return
    ctx["usage_records"] = result["items"]
    ctx["usage_pager"] = cursor_links(request, ctx, "usage", result["next_cursor"])
    if identity:
        ctx["usage_selected"] = result["items"][0]
        ctx["usage_feedback_id"] = uuid4().hex
        ctx["usage_can_feedback"] = result.get("can_feedback", False)


async def hydrate_usage(api, request, ctx):
    selected = ctx["usage_selected"]
    if selected is None:
        return
    return_to = ctx["link"]("usage", view="records")
    previews = {}
    for item in selected["items"]:
        ref = item["artifact"]
        item["url"] = artifact_destination(ctx, ref, scope=item["scope_id"], return_to=return_to)
        key = (item["scope_id"], ref["family"], ref["artifact_id"], ref["revision"])
        if key not in previews:
            try:
                value = await api.artifact_revision(*key)
                content = value["content"]
                text = next(
                    (
                        content.get(name)
                        for name in ("text", "title", "lesson", "objective", "description", "name", "content")
                        if content.get(name)
                    ),
                    ref["family"],
                )
                title = str(text).strip()
                previews[key] = {
                    "title": title.splitlines()[0].lstrip("# ")[:300] if title else ref["family"],
                    "body": "\n\n".join(
                        str(content[name]) for name in ("title", "summary", "detail") if content.get(name)
                    )
                    if ref["family"] == "topic-memory"
                    else title,
                    "saved_at": value.get("created_at"),
                    "error": None,
                }
            except ReadError as error:
                previews[key] = {"title": None, "error": error}
        item.update(previews[key])


async def submit_feedback(api, request):
    if not same_origin(request):
        raise ReadError(403, "cross_origin_request")
    if len(await request.body()) > 16000:
        raise ReadError(413, "invalid_request")
    form = await request.form()
    if any(len(form.getlist(key)) != 1 for key in form):
        raise ReadError(422, "invalid_request")
    scope = str(form.get("scope") or "")
    if not scope.strip() or len(scope) > 256:
        raise ReadError(422, "invalid_request")
    try:
        payload = ContextFeedbackRequest.model_validate({key: value for key, value in form.items() if key != "scope"})
    except ValidationError as error:
        raise ReadError(422, "invalid_request") from error
    if api.builtin is None or api.builtin.context_usage is None:
        raise ReadError(503, "operation_not_supported")
    from powercontext.server.app import _atomic_execution_context

    try:
        await api.builtin.context_usage.feedback(
            scope, payload, context=_atomic_execution_context(request, "dashboard_usage_feedback")
        )
    except Exception as error:
        raise ReadError.from_exception(error) from error
    return scope, payload


async def finish_usage(api, request, ctx):
    if ctx["usage_tab"] not in {"resources", "records"}:
        raise ReadError(422, "invalid_request")
    if ctx["usage_tab"] == "records" and api.builtin is None:
        ctx["errors"]["usage_records"] = ReadError(503, "operation_not_supported")
    await hydrate_usage(api, request, ctx)
