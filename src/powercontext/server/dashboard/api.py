"""Read the public HTTP surface without bypassing its authentication or access checks."""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import Request
from pydantic import ValidationError

from powercontext.builtin.artifacts.experience import ExperienceContent
from powercontext.builtin.artifacts.handoff.models import HandoffContent
from powercontext.builtin.artifacts.skill import SkillContent
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
