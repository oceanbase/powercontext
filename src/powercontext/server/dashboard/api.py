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

"""Read the public HTTP surface without bypassing its authentication or access checks."""

from __future__ import annotations

import asyncio
import base64
import json
from contextlib import suppress
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import Request
from pydantic import ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.handoff.models import HandoffContent
from powercontext.builtin.artifacts.skill import SkillContent
from powercontext.builtin.artifacts.topic_memory import TopicMemoryBrowseCursor
from powercontext.builtin.runtime.models import GetTopicMemoryRequest
from powercontext.errors import ArtifactNotFoundError
from powercontext.server.dashboard.session import authentication_headers

CONTENT_MODELS = {"handoff": HandoffContent, "experience": ExperienceContent, "skill": SkillContent}


class ReadError(Exception):
    def __init__(self, status: int, code: str, request_id: str | None = None) -> None:
        self.status = status
        self.code = code
        self.request_id = request_id
        super().__init__(code)


def segment(value: str) -> str:
    return quote(value, safe="")


class DashboardAPI:
    """Use an in-process HTTP transport with the incoming user's credentials."""

    def __init__(self, request: Request) -> None:
        self.app = request.app
        headers = authentication_headers(request.scope)
        headers = {key: value for key, value in headers.items() if key in {"authorization", "cookie"}}
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=request.app, raise_app_exceptions=False),
            base_url=str(request.base_url),
            headers=headers,
        )

    async def read(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with asyncio.timeout(20):
                result = await self.client.request("GET" if payload is None else "POST", path, json=payload)
        except (TimeoutError, httpx.HTTPError) as error:
            raise ReadError(503, "service_unavailable") from error
        if result.is_error:
            code = "service_unavailable"
            with suppress(ValueError, KeyError, TypeError):
                code = result.json()["error"]["code"]
            raise ReadError(result.status_code, code, result.headers.get("X-PowerContext-Request-ID"))
        return result.json()

    async def record(self, scope: str, family: str, artifact: str, revision: int) -> dict[str, Any]:
        if family in {"experience", "skill"}:
            value = await self.read(
                f"/v1/{family}/get",
                {
                    "scope_id": scope,
                    "artifact": {"family": family, "artifact_id": artifact, "revision": revision},
                },
            )
            return self.family_record(family, value)
        value = await self.read(
            f"/v1/scopes/{segment(scope)}/artifacts/{family}/{segment(artifact)}/revisions/{revision}"
        )
        return self.family_record(
            family,
            {
                "content": value["content"],
                "artifact": {"artifact_id": value["artifact_id"], "revision": value["revision"]},
                "source_refs": [
                    {"name": source["source_type"], "source_id": source["source_id"]} for source in value["sources"]
                ],
                "artifact_refs": value["artifacts"],
                "memory_citations": value.get("memory_citations", []),
            },
        )

    @staticmethod
    def family_record(family: str, value: dict[str, Any]) -> dict[str, Any]:
        try:
            content = (
                CONTENT_MODELS[family]
                .model_validate_json(json.dumps(value["content"]))
                .model_dump(mode="json", by_alias=True)
            )
        except ValidationError as error:
            raise ReadError(422, "unsupported_content") from error
        return {
            **content,
            **value["artifact"],
            "sources": [
                {"source_type": source["name"], "source_id": source["source_id"]} for source in value["source_refs"]
            ],
            "artifacts": value["artifact_refs"],
            "memory_citations": value.get("memory_citations", []),
        }

    async def records(
        self, scope: str, family: str, *, cursor: str | None = None, limit: int = 12, query: str | None = None
    ) -> dict[str, Any]:
        if family == "skill":
            maximum = 1 if limit == 1 else 200
            result = await self.read("/v1/skill/library", {"scope_id": scope, "limit": maximum, "query": query})
            return {
                "items": [self.family_record(family, item) for item in result["skills"]],
                "next_cursor": None,
                "search_limited": maximum == 200 and len(result["skills"]) == maximum,
            }
        url = httpx.URL(f"/v1/scopes/{segment(scope)}/artifacts/{family}", params={"limit": limit})
        if cursor:
            url = url.copy_add_param("cursor", cursor)
        page = await self.read(str(url))
        semaphore = asyncio.Semaphore(4)

        async def load(item: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                try:
                    return await self.record(scope, family, item["artifact_id"], item["revision"])
                except ReadError as error:
                    return {"artifact_id": item["artifact_id"], "revision": item["revision"], "error": error}

        return {
            "items": list(await asyncio.gather(*(load(item) for item in page["items"]))),
            "next_cursor": page["next_cursor"],
        }

    async def topic_memory_search(self, scope: str, query: str) -> dict[str, Any]:
        return await self.read("/v1/topic-memory/search", {"scope_id": scope, "query": query})

    async def prompt_configuration(self, scope: str, key: str) -> dict[str, Any]:
        return await self.read(f"/v1/scopes/{segment(scope)}/prompts/{segment(key)}")

    async def topic_memory_get(self, scope: str, artifact: dict[str, Any]) -> dict[str, Any]:
        application = getattr(self.app.state, "application", None)
        if application is None:
            raise ReadError(503, "service_unavailable")
        try:
            value = await application.topic_memory.for_scope(scope).get(
                GetTopicMemoryRequest(artifact=ArtifactRef.model_validate(artifact))
            )
        except ArtifactNotFoundError as error:
            raise ReadError(404, "not_found") from error
        except ValueError as error:
            raise ReadError(422, "invalid_request") from error
        return {
            "artifact": value.topic.as_ref().model_dump(mode="json"),
            "title": value.topic.content.title,
            "summary": value.topic.content.summary,
            "detail": value.topic.content.detail,
            "source_refs": [
                {"name": source.source_type, "source_id": source.source_id} for source in value.topic.lineage.sources
            ],
            "is_current": value.is_current,
            "current_artifact": value.current_artifact.model_dump(mode="json"),
        }

    @staticmethod
    def _decode_topic_cursor(value: str) -> TopicMemoryBrowseCursor:
        try:
            padding = "=" * (-len(value) % 4)
            payload = base64.urlsafe_b64decode(value + padding)
            return TopicMemoryBrowseCursor.model_validate_json(payload)
        except (ValueError, TypeError, ValidationError) as error:
            raise ReadError(422, "invalid_request") from error

    @staticmethod
    def _encode_topic_cursor(value: TopicMemoryBrowseCursor) -> str:
        payload = json.dumps(value.model_dump(mode="json"), separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(payload).decode().rstrip("=")

    async def topic_memory_browse(self, scope: str, *, cursor: str | None = None, limit: int = 50) -> dict[str, Any]:
        application = getattr(self.app.state, "application", None)
        if application is None:
            raise ReadError(503, "service_unavailable")
        after = self._decode_topic_cursor(cursor) if cursor else None
        items = await application.topic_memory.for_scope(scope).browse(limit=limit + 1, after=after)
        has_next = len(items) > limit
        items = items[:limit]
        next_cursor = None
        if has_next and items:
            last = items[-1]
            next_cursor = self._encode_topic_cursor(
                TopicMemoryBrowseCursor(
                    published_at=last.published_at,
                    artifact_id=last.artifact_ref.artifact_id,
                    revision=last.artifact_ref.revision,
                )
            )
        return {
            "items": [
                {
                    **item.model_dump(mode="json"),
                    "artifact": item.artifact_ref.model_dump(mode="json"),
                }
                for item in items
            ],
            "next_cursor": next_cursor,
        }
