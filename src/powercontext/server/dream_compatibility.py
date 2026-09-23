# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Negotiate the closed Dream and Candidate schemas without breaking legacy reads."""

from __future__ import annotations

import json
from typing import Any, cast

from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.middleware.base import RequestResponseEndpoint

from powercontext.builtin.dream.models import DreamError

DREAM_CONTRACT_HEADER = "X-PowerContext-Dream-Contract"
MIN_DREAM_CONTRACT = "2"
LEGACY_DREAM_OPERATIONS = frozenset({"refine_experience", "derive_skill"})


def supports_extended_dream(request: Request) -> bool:
    return request.headers.get(DREAM_CONTRACT_HEADER) == MIN_DREAM_CONTRACT


def require_extended_dream(request: Request) -> None:
    if not supports_extended_dream(request):
        raise DreamError("client_upgrade_required")


def require_candidate_contract(request: Request, candidate: Any) -> None:
    if supports_extended_dream(request):
        return
    audit = getattr(candidate, "audit", None)
    if (
        candidate.family not in {"experience", "skill", "profile"}
        or (audit is not None and audit.operation not in LEGACY_DREAM_OPERATIONS)
        or getattr(candidate.proposal, "dream_run_id", None) is not None
    ):
        require_extended_dream(request)


def _legacy_profile(result: dict[str, Any]) -> None:
    if result.get("schema") == "powercontext.profile.v1":
        generation = dict(result.get("generation", {}))
        if generation.get("mode") == "dream_review_approved":
            raise DreamError("client_upgrade_required")
        generation.pop("dream_run_id", None)
        result["generation"] = generation
    if result.get("schema") == "powercontext.profile-candidate.v1":
        if result.get("dream_run_id") is not None:
            raise DreamError("client_upgrade_required")
        result.pop("dream_run_id", None)
        result.pop("policy_version", None)


def _legacy_candidate(result: dict[str, Any]) -> None:
    audit = result.get("audit") or {}
    if (
        result["family"] not in {"experience", "skill", "profile"}
        or audit.get("operation", "refine_experience") not in LEGACY_DREAM_OPERATIONS
        or result["proposal"].get("dream_run_id") is not None
    ):
        raise DreamError("client_upgrade_required")
    result.pop("audit", None)
    if result["family"] == "profile":
        result["proposal"] = dict(result["proposal"])
        _legacy_profile(result["proposal"])


def _legacy_run(result: dict[str, Any]) -> None:
    if result["operation"] not in LEGACY_DREAM_OPERATIONS:
        raise DreamError("client_upgrade_required")
    manifest = result.get("input_manifest")
    if isinstance(manifest, dict):
        supported_kinds = {"source", "experience", "memory", "unresolved"}
        if any(node.get("kind") not in supported_kinds for node in manifest.get("nodes", ()) if isinstance(node, dict)):
            raise DreamError("client_upgrade_required")
    result.pop("tag_target", None)
    result.pop("reused", None)
    if result.get("candidate"):
        result["candidate"] = {k: v for k, v in result["candidate"].items() if k != "kind"}


def _legacy_payload(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    result = dict(value)
    _legacy_profile(result)
    # Legacy Capabilities is a closed schema, even when no Dream operations are enabled.
    result.pop("artifact_dreaming_operations", None)
    if "run_id" in result and "operation" in result and "accepted_at" in result:
        _legacy_run(result)
    if "candidate_id" in result and "proposal" in result and "family" in result:
        _legacy_candidate(result)
    if result.get("family") == "profile" and isinstance(result.get("content"), dict):
        result["content"] = dict(result["content"])
        _legacy_profile(result["content"])
    # Only descend through protocol envelopes. Prompt demonstrations and Artifact/Source
    # content are arbitrary business JSON and must never be interpreted as transport DTOs.
    for key in ("runs", "candidates", "items"):
        if isinstance(result.get(key), list):
            result[key] = [_legacy_payload(item) for item in result[key]]
    candidate = result.get("candidate")
    if isinstance(candidate, dict) and "proposal" in candidate:
        result["candidate"] = _legacy_payload(candidate)
    return result


async def negotiate_dream_contract(request: Request, call_next: RequestResponseEndpoint) -> Response:
    response = await call_next(request)
    response.headers[DREAM_CONTRACT_HEADER] = MIN_DREAM_CONTRACT
    path = request.url.path
    relevant = (
        path == "/v1/capabilities"
        or path.startswith("/v1/artifact-candidates/")
        or (path.startswith("/v1/scopes/") and ("/dream" in path or "/artifacts" in path))
        or path in {"/v1/context/prepare", "/v1/profile/flush"}
        or path
        in {
            "/v1/experience/propose",
            "/v1/experience/generate",
            "/v1/skill/propose",
            "/v1/skill/generate",
            "/v1/skill/package/propose",
            "/v1/external-skills/import",
        }
    )
    if supports_extended_dream(request) or not relevant:
        return response
    if response.status_code >= 400 or "application/json" not in response.headers.get("content-type", ""):
        return response
    # Only bounded API JSON is inspected; download and streaming routes retain their transport.
    stream = cast(StreamingResponse, response)
    body = b"".join([part.encode() if isinstance(part, str) else bytes(part) async for part in stream.body_iterator])
    headers = dict(response.headers)
    headers.pop("content-length", None)
    try:
        payload = _legacy_payload(json.loads(body))
    except DreamError:
        return JSONResponse(
            status_code=426,
            content={
                "error": {
                    "code": "client_upgrade_required",
                    "message": "Upgrade to a client supporting Dream contract 2.",
                    "details": {"required_dream_contract": 2},
                }
            },
            headers=headers,
            background=response.background,
        )
    return JSONResponse(payload, status_code=response.status_code, headers=headers, background=response.background)
