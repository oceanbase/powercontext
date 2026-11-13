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

"""Select exact evidence and submit reviewed generation through the public API."""

import asyncio
import json

import httpx
from pydantic import ValidationError

from powercontext.http._generated.models import (
    CreateDreamRunRequest,
    DreamRun,
    FlushProfileRequest,
    FlushProfileResponse,
    GeneratedCandidateResponse,
    GenerateExperienceRequest,
    GenerateSkillRequest,
)
from powercontext.server.dashboard.api import segment
from powercontext.server.dashboard.errors import ReadError
from powercontext.server.dashboard.pagination import PAGE_SIZE, cursor_links
from powercontext.server.dashboard.processing import present_source
from powercontext.server.dashboard.session import same_origin


def evidence_types(family):
    return ("source", "experience") if family == "skill" else ("source", "atomic-memory", "experience")


async def load_generation(api, request, ctx):
    if not ctx["generation_open"]:
        return
    dream = ctx["generation_mode"] == "dream"
    kinds = (
        ("experience",)
        if dream and ctx["method_kind"] == "skill"
        else ("atomic-memory", "experience")
        if dream
        else evidence_types(ctx["method_kind"])
    )
    kind = request.query_params.get("evidence_type", kinds[0])
    if kind not in kinds:
        raise ReadError(422, "invalid_request")
    ctx["generation_evidence_type"] = kind
    cursor = request.query_params.get("generation_cursor")
    options = []
    try:
        if kind == "source":
            result = await api.source_directory(ctx["scope"], limit=PAGE_SIZE, cursor=cursor)
            for item in result["items"]:
                source = present_source(item)
                options.append({
                    "ref": {"name": source["source_type"], "source_id": source["source_id"]},
                    "text": source["preview"],
                    "revision": None,
                })
        elif kind == "atomic-memory":
            result = await api.atomic_memory_list(ctx["scope"], state="active", limit=PAGE_SIZE, cursor=cursor)
            options = [
                {"ref": item["artifact"], "text": item["text"], "revision": item["artifact"]["revision"]}
                for item in result["items"]
            ]
        else:
            result = await api.records(ctx["scope"], "experience", limit=PAGE_SIZE, cursor=cursor)
            options = [
                {
                    "ref": {"family": "experience", "artifact_id": item["artifact_id"], "revision": item["revision"]},
                    "text": item["lesson"],
                    "revision": item["revision"],
                }
                for item in result["items"]
                if "error" not in item
            ]
            if any("error" in item for item in result["items"]):
                ctx["errors"]["generation_evidence"] = next(
                    item["error"] for item in result["items"] if "error" in item
                )
        ctx["generation_options"] = options
        ctx["generation_pager"] = cursor_links(request, ctx, "generation", result.get("next_cursor"))
    except ReadError as error:
        ctx["errors"]["generation_evidence"] = error


def _generation_selection(form):
    allowed = {"scope", "kind", "evidence_type", "evidence", "reason"}
    if set(form) - allowed or any(len(form.getlist(key)) != 1 for key in form if key != "evidence"):
        raise ReadError(422, "invalid_request")
    family, kind = form.get("kind"), form.get("evidence_type")
    if family not in {"experience", "skill"} or kind not in evidence_types(family):
        raise ReadError(422, "invalid_request")
    values = form.getlist("evidence")
    if not 1 <= len(values) <= 32:
        raise ReadError(422, "invalid_request")
    try:
        refs = [json.loads(str(value)) for value in values]
        if any(not isinstance(ref, dict) for ref in refs) or (
            kind != "source" and any(ref.get("family") != kind for ref in refs)
        ):
            raise ReadError(422, "invalid_request")
        payload = {
            "scope_id": str(form.get("scope") or ""),
            "source_refs": refs if kind == "source" else [],
            "artifact_refs": [] if kind == "source" else refs,
            "reason": str(form.get("reason") or "").strip() or None,
        }
        model = GenerateSkillRequest if family == "skill" else GenerateExperienceRequest
        if family == "skill":
            payload["origin"] = "source" if kind == "source" else "experience"
        selected = model.model_validate(payload)
    except (ValueError, ValidationError) as error:
        raise ReadError(422, "invalid_request") from error
    return family, selected


async def submit_generation(api, request):
    if not same_origin(request):
        raise ReadError(403, "cross_origin_request")
    if len(await request.body()) > 32_000:
        raise ReadError(413, "invalid_request")
    family, selected = _generation_selection(await request.form())
    try:
        async with asyncio.timeout(120):
            response = await api.client.post(f"/v1/{family}/generate", json=selected.model_dump(mode="json"))
    except (TimeoutError, httpx.HTTPError) as error:
        raise ReadError(503, "service_unavailable") from error
    if response.is_error:
        # Render the existing bounded error, never provider or validation payloads.
        raise ReadError(response.status_code, "generation_failed", response.headers.get("X-PowerContext-Request-ID"))
    try:
        result = GeneratedCandidateResponse.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise ReadError(503, "service_unavailable") from error
    return selected.scope_id, family, result


async def submit_profile_generation(api, request):
    if not same_origin(request):
        raise ReadError(403, "cross_origin_request")
    if len(await request.body()) > 2000:
        raise ReadError(413, "invalid_request")
    form = await request.form()
    if set(form) != {"scope"} or len(form.getlist("scope")) != 1:
        raise ReadError(422, "invalid_request")
    try:
        selected = FlushProfileRequest(scope_id=str(form["scope"]))
    except ValidationError as error:
        raise ReadError(422, "invalid_request") from error
    try:
        async with asyncio.timeout(120):
            response = await api.client.post("/v1/profile/flush", json=selected.model_dump(mode="json"))
    except (TimeoutError, httpx.HTTPError) as error:
        raise ReadError(503, "service_unavailable") from error
    if response.is_error:
        raise ReadError(response.status_code, "generation_failed", response.headers.get("X-PowerContext-Request-ID"))
    try:
        result = FlushProfileResponse.model_validate(response.json())
    except (ValueError, ValidationError) as error:
        raise ReadError(503, "service_unavailable") from error
    return selected.scope_id, result


async def submit_dream_generation(api, request):
    """Create a durable task from exact saved artifacts through the Dream API."""
    if not same_origin(request):
        raise ReadError(403, "cross_origin_request")
    if len(await request.body()) > 32_000:
        raise ReadError(413, "invalid_request")
    form = await request.form()
    if set(form) - {"scope", "kind", "evidence_type", "evidence", "idempotency_key"} or any(
        len(form.getlist(key)) != 1 for key in form if key != "evidence"
    ):
        raise ReadError(422, "invalid_request")
    family, kind = form.get("kind"), form.get("evidence_type")
    if family not in {"experience", "skill"} or kind not in (
        ("experience",) if family == "skill" else ("atomic-memory", "experience")
    ):
        raise ReadError(422, "invalid_request")
    values = form.getlist("evidence")
    if not 1 <= len(values) <= 20:
        raise ReadError(422, "invalid_request")
    try:
        refs = [json.loads(str(value)) for value in values]
        if any(not isinstance(ref, dict) or ref.get("family") != kind for ref in refs):
            raise ReadError(422, "invalid_request")
        selected = CreateDreamRunRequest.model_validate({
            "operation": "derive_skill" if family == "skill" else "refine_experience",
            "artifacts": refs,
            "idempotency_key": form.get("idempotency_key"),
        })
        scope = str(form.get("scope") or "")
        if not scope.strip() or len(scope) > 256:
            raise ReadError(422, "invalid_request")
    except (ValueError, ValidationError) as error:
        raise ReadError(422, "invalid_request") from error
    value = await api.read(f"/v1/scopes/{segment(scope)}/dream", selected.model_dump(mode="json", exclude_unset=True))
    try:
        result = DreamRun.model_validate(value)
    except ValidationError as error:
        raise ReadError(503, "service_unavailable") from error
    return scope, result
