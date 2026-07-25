"""Ready-to-run Server composition over the built-in runtime."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from powercontext.api import Capabilities, MemorySearchMode
from powercontext.builtin.artifacts.memory import CandidatePipeline
from powercontext.builtin.inference import EmbeddingModel
from powercontext.builtin.runtime import BuiltinRuntime
from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.sources import CONTENT_SOURCE_NAME
from powercontext.server.app import create_app
from powercontext.server.mcp import mount_mcp
from powercontext.server.settings import ServerSettings


def create_server_app(
    *,
    settings: ServerSettings | None = None,
    candidate_pipeline: CandidatePipeline | None = None,
    embedding_model: EmbeddingModel | None = None,
) -> FastAPI:
    """Build the Server process and mount MCP when configured."""

    resolved = ServerSettings() if settings is None else settings
    config = BuiltinConfig(
        runtime=resolved.runtime,
        database=resolved.database,
        inference=resolved.inference,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_builtin_runtime(
            config,
            candidate_pipeline=candidate_pipeline,
            embedding_model=embedding_model,
        ) as runtime:
            app.state.application = runtime
            app.state.capabilities = await _server_capabilities(runtime)
            try:
                yield
            finally:
                app.state.application = None
                app.state.capabilities = Capabilities(
                    source_types=[],
                    artifact_families=[],
                    memory_extraction=False,
                    search_modes=[],
                )

    app = create_app(lifespan=lifespan)
    if resolved.mcp.enabled:
        mount_mcp(app, path=resolved.mcp.path)
    return app


async def _server_capabilities(runtime: BuiltinRuntime) -> Capabilities:
    capabilities = await runtime.capabilities()
    return Capabilities(
        source_types=[CONTENT_SOURCE_NAME],
        artifact_families=["memory"],
        memory_extraction=capabilities.memory_extraction,
        search_modes=[MemorySearchMode(mode) for mode in capabilities.memory_search_modes],
    )


__all__ = [
    "create_server_app",
]
