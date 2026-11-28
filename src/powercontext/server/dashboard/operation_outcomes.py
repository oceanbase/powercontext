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

"""Present existing operation evidence without creating execution history on reads."""

import asyncio
from asyncio import timeout
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from powercontext.builtin.dream.models import DreamRun
from powercontext.http._generated.models import CatalogTagResult
from powercontext.server.dashboard.api import segment
from powercontext.server.dashboard.errors import ReadError
from powercontext.server.dashboard.pagination import PAGE_SIZE, cursor_links
from powercontext.server.dashboard.presenters import artifact_destination

FAMILY_OPERATIONS = {
    "atomic-memory": ("revise_memory",),
    "experience": ("refine_experience",),
    "skill": ("derive_skill", "revise_skill"),
    "profile": ("revise_profile",),
    "topic-memory": ("revise_topic_memory",),
    "prompt": ("revise_prompt",),
    "handoff": ("refresh_handoff",),
}
PAGE_FAMILIES = {
    "notes": "atomic-memory",
    "experience": "experience",
    "skill": "skill",
    "profile": "profile",
    "topics": "topic-memory",
    "prompts": "prompt",
    "handoff": "handoff",
    "handoff-detail": "handoff",
}
# Collection pages list records without opening one, so they select no exact revision.
LIST_PAGES = frozenset({"methods", "handoff"})
# Detail pages whose saved record can carry generation attribution.
GENERATION_PAGES = {"profile", "handoff-detail"}
CANDIDATE_STATUSES = frozenset({"pending", "approved", "rejected"})
GENERATION_MODES = frozenset({
    "automatic",
    "manual_create",
    "manual_replace",
    "review_approved",
    "rollback",
    "dream_review_approved",
})
GENERATION_EDIT_STATUSES = frozenset({"unchanged", "edited"})
INSPECTION_SECONDS = 8
DETAIL_CONCURRENCY = 3


def _selection(ctx, family):
    """Return only the revision the page actually opened, never a list's first record."""
    page = ctx["page"]
    if page in LIST_PAGES:
        return None
    if page == "notes":
        record = ctx["selected_note"]
    elif family == "topic-memory":
        topic = ctx["data"]["topic_memory_selected"]
        return None if topic is None else topic["artifact"]
    elif family == "prompt":
        record = ctx["prompt_opened"]
        if record is None:
            if ctx["errors"].get("prompt_selected"):
                return None
            configuration = next(
                (item for item in ctx["data"]["prompts"] if item["prompt_key"] == ctx["prompt_key"]), None
            )
            return None if configuration is None else configuration["artifact"]
    else:
        # Record detail pages load the requested exact revision, or nothing at all.
        record = ctx["data"].get(family)
    return (
        None
        if record is None
        else {"family": family, "artifact_id": record["artifact_id"], "revision": record["revision"]}
    )


def _is_reference(value) -> bool:
    """Accept only a complete Artifact reference the page can address."""
    return (
        isinstance(value, dict)
        and isinstance(value.get("family"), str)
        and isinstance(value.get("artifact_id"), str)
        and isinstance(value.get("revision"), int)
        and not isinstance(value.get("revision"), bool)
        and value["revision"] >= 1
    )


def _valid_generation(family, generation) -> bool:
    """Render attribution only when every displayed field has a known label."""
    if not isinstance(generation, dict):
        return False
    if family == "profile":
        return (
            generation.get("mode") in GENERATION_MODES
            and isinstance(generation.get("created_at"), str)
            and (generation.get("dream_run_id") is None or isinstance(generation["dream_run_id"], str))
        )
    return (
        generation.get("selection") in {"built_in", "artifact"}
        and generation.get("edit_status") in GENERATION_EDIT_STATUSES
        and (generation.get("artifact") is None or _is_reference(generation["artifact"]))
    )


def _generation(ctx, family):
    """Attribute a saved generation only to the revision the page displays."""
    if ctx["page"] not in GENERATION_PAGES:
        return None
    record = ctx["data"].get(family)
    generation = None if record is None else record.get("generation")
    return generation if _valid_generation(family, generation) else None


def _default_operation(ctx, family):
    """Pick the operation this page is about; an explicit selection always wins."""
    if family == "skill" and ctx["page"] == "skill":
        return "revise_skill"
    return FAMILY_OPERATIONS[family][0]


def _association(run, candidate, selected):
    if selected is None:
        return "scope"
    if candidate is not None and candidate.get("result_artifact") == selected:
        return "result"
    if run.get("target") == selected:
        return "target"
    tag_target = run.get("tag_target")
    if tag_target is not None and tag_target.get("basis_ref") == selected:
        return "tag_basis"
    manifest = run.get("input_manifest")
    if manifest is not None and selected in manifest["artifacts"]:
        return "input"
    return "scope"


def _validate_candidate(candidate, run):
    reference = run["candidate"]
    family = next((family for family, operations in FAMILY_OPERATIONS.items() if run["operation"] in operations), None)
    result = candidate.get("result_artifact")
    if (
        candidate["candidate_id"] != reference["candidate_id"]
        or candidate["candidate_kind"] != reference["kind"]
        or candidate["version"] < reference["version"]
        or (family is not None and candidate["family"] != family)
        or candidate.get("target") != run.get("target")
        # The template renders these fields directly, so an unlabeled value is not displayable.
        or candidate.get("status") not in CANDIDATE_STATUSES
        or (result is not None and not _is_reference(result))
    ):
        raise ReadError(409, "invalid_response")
    approved = candidate["status"] == "approved"
    if candidate["candidate_kind"] == "artifact":
        if approved != (result is not None) or (result is not None and result["family"] != candidate["family"]):
            raise _invalid_response()
    else:
        tag_result = candidate.get("result")
        if result is not None or approved != (tag_result is not None):
            raise _invalid_response()
        if tag_result is not None and CatalogTagResult.model_validate(tag_result).scope_id != run["scope_id"]:
            raise _invalid_response()
    # The template reads these nullable fields directly, so absent means absent, never undefined.
    return {**candidate, "result_artifact": result, "result": candidate.get("result")}


def _invalid_response() -> ReadError:
    return ReadError(409, "invalid_response")


async def _run_details(api, ctx, item, selected):
    """Read one run's dependent records, isolating failures to their own dimension."""
    run = item["run"]
    reference = run.get("candidate")
    if reference is None:
        return
    try:
        candidate = _validate_candidate(
            await api.read("/v1/candidates/get", {"scope_id": ctx["scope"], "candidate_id": reference["candidate_id"]}),
            run,
        )
        item["candidate"] = candidate
        item["association"] = _association(run, candidate, selected)
    except ReadError as error:
        item["candidate_error"] = error
        return
    except Exception:
        item["candidate_error"] = _invalid_response()
        return
    result = candidate.get("result_artifact")
    if result is not None:
        try:
            # A readable Candidate does not grant access to its saved output.
            await api.artifact_revision(ctx["scope"], result["family"], result["artifact_id"], result["revision"])
            item["result_url"] = artifact_destination(ctx, result)
        except ReadError as error:
            item["result_error"] = error
        except Exception:
            item["result_error"] = _invalid_response()


def _requested_operation(view, request):
    """Accept only an operation this page can inspect, never an arbitrary value."""
    requested = request.query_params.get("outcome_operation")
    if requested is not None and requested not in view["operations"]:
        raise ReadError(422, "invalid_request")
    return requested


def _base_run(ctx, value, operation, selected):
    try:
        run = DreamRun.model_validate(value).model_dump(mode="json")
    except Exception as error:
        raise _invalid_response() from error
    if run["scope_id"] != ctx["scope"] or run["operation"] != operation:
        raise _invalid_response()
    return {
        "run": run,
        "candidate": None,
        "candidate_error": None,
        "result_url": None,
        "result_error": None,
        # Association from the base record needs no detail read, so a canceled read cannot lose it.
        "association": _association(run, None, selected),
    }


async def _load_runs(api, request, ctx, view):
    operation = view["operation"]
    scope = segment(ctx["scope"])
    run_id = request.query_params.get("outcome_run")
    result: dict[str, Any]
    if run_id is not None:
        if not run_id or len(run_id) > 64:
            raise ReadError(422, "invalid_request")
        result = {"runs": [await api.read(f"/v1/scopes/{scope}/dream/{segment(run_id)}")], "next_cursor": None}
    else:
        params = {"operation": operation, "limit": PAGE_SIZE}
        if cursor := request.query_params.get("operation_runs_cursor"):
            params["cursor"] = cursor
        result = await api.read(f"/v1/scopes/{scope}/dream?{urlencode(params)}")
    runs = result.get("runs") if isinstance(result, dict) else None
    if not isinstance(runs, list):
        raise _invalid_response()
    # Retain every structurally valid base record before any dependent detail read.
    items = [_base_run(ctx, value, operation, view["selected"]) for value in runs[:PAGE_SIZE]]
    view["runs"].extend(items)
    # Never follow the next cursor automatically or expand a complete Run history.
    semaphore = asyncio.Semaphore(DETAIL_CONCURRENCY)

    async def detail(item):
        async with semaphore:
            await _run_details(api, ctx, item, view["selected"])

    await asyncio.gather(*(detail(item) for item in items))
    if run_id is None:
        view["pager"] = cursor_links(request, ctx, "operation_runs", result.get("next_cursor"))


async def load_operation_outcomes(api, request, ctx):
    """Keep optional inspection failures independent of already authorized content."""
    family = ctx["method_kind"] if ctx["page"] == "methods" else PAGE_FAMILIES.get(ctx["page"])
    if family not in FAMILY_OPERATIONS:
        return
    view = {
        "family": family,
        "operations": (*FAMILY_OPERATIONS[family], "revise_tags"),
        "operation": _default_operation(ctx, family),
        "selected": None,
        "generation": None,
        "runs": [],
        "matched": [],
        "pager": None,
        "error": None,
        "read_at": datetime.now(UTC).isoformat(),
        "record_only": ctx["record_only"],
    }
    ctx["operation_outcomes"] = view
    if ctx["record_only"]:
        return
    try:
        async with timeout(INSPECTION_SECONDS):
            view["selected"] = _selection(ctx, family)
            view["generation"] = _generation(ctx, family)
            view["operation"] = _requested_operation(view, request) or view["operation"]
            await _load_runs(api, request, ctx, view)
    except asyncio.CancelledError:
        # A cancelled request is not an inspection failure.
        raise
    except Exception as error:
        view["error"] = ReadError.from_exception(error)
    finally:
        if view["error"] is not None:
            for item in view["runs"]:
                if (
                    item["run"]["candidate"] is not None
                    and item["candidate"] is None
                    and item["candidate_error"] is None
                ):
                    item["candidate_error"] = ReadError(503, "service_unavailable")
                candidate = item["candidate"]
                if (
                    candidate is not None
                    and candidate["result_artifact"] is not None
                    and item["result_url"] is None
                    and item["result_error"] is None
                ):
                    item["result_error"] = ReadError(503, "service_unavailable")
        # Recompute the association summary from whatever was retained, success or not.
        view["matched"] = [item for item in view["runs"] if item["association"] != "scope"]
        view["read_at"] = datetime.now(UTC).isoformat()
