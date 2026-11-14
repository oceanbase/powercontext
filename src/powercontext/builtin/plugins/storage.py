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

"""Dedicated plugin storage and the models a plugin may call."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any, Literal, cast

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from powercontext.plugins import ChatMessage, ModelUnavailable


class SQLitePluginStorage:
    """One SQLite file per plugin; plugins own its tables and migrations."""

    backend: Literal["sqlite"] = "sqlite"

    def __init__(self, path: Path) -> None:
        self.path = path
        self._engine: AsyncEngine | None = None

    def engine(self) -> AsyncEngine:
        if self._engine is None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._engine = create_async_engine(f"sqlite+aiosqlite:///{self.path}")
        return self._engine

    async def aclose(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()


def plugin_storage(database: Any, root: str | None, name: str) -> SQLitePluginStorage | None:
    """Allocate the plugin's area on the deployment backend; other backends are not served yet."""

    from powercontext.builtin.persistence.sqlite.profile import SQLiteConfig

    if root is None or not isinstance(database, SQLiteConfig):
        return None
    return SQLitePluginStorage(Path(root) / f"{name}.sqlite3")


class ServerModels:
    """Server's configured models; the plugin never sees their addresses or credentials."""

    def __init__(self, embedding: Any, generation: Any, inference: Any) -> None:
        self._embedding = embedding
        self._generation = generation
        self._inference = inference

    @property
    def embedding_available(self) -> bool:
        return self._embedding is not None

    @property
    def embedding_dimension(self) -> int | None:
        return None if self._embedding is None else self._embedding.profile.dimension

    @property
    def generation_available(self) -> bool:
        return self._generation is not None

    async def embed(self, texts: Sequence[str], /) -> tuple[tuple[float, ...], ...]:
        if self._embedding is None:
            raise ModelUnavailable("the Server has no embedding model")  # noqa: TRY003
        result = await self._embedding.embed(tuple(texts))
        return tuple(tuple(float(value) for value in vector) for vector in result.vectors)

    async def generate(self, messages: Sequence[ChatMessage], /, *, max_tokens: int | None = None) -> str:
        if self._generation is None:
            raise ModelUnavailable("the Server has no generation model")  # noqa: TRY003
        from pydantic_ai.direct import model_request
        from pydantic_ai.messages import (
            ModelMessage,
            ModelRequest,
            ModelRequestPart,
            ModelResponse,
            SystemPromptPart,
            TextPart,
            UserPromptPart,
        )

        history: list[ModelMessage] = []
        pending: list[ModelRequestPart] = []
        for message in messages:
            if message.role == "assistant":
                if pending:
                    history.append(ModelRequest(parts=pending))
                    pending = []
                history.append(ModelResponse(parts=[TextPart(message.content)]))
            elif message.role == "system":
                pending.append(SystemPromptPart(message.content))
            else:
                pending.append(UserPromptPart(message.content))
        if pending:
            history.append(ModelRequest(parts=pending))
        settings: dict[str, Any] = dict(self._inference.generation_model_settings)
        if max_tokens is not None:
            settings["max_tokens"] = max_tokens
        async with asyncio.timeout(self._inference.generation_timeout_seconds):
            response = await model_request(self._generation, history, model_settings=cast(Any, settings))
        return "".join(part.content for part in response.parts if isinstance(part, TextPart))


async def plugin_models(inference: Any, resources: AsyncExitStack) -> ServerModels | None:
    if inference is None or (inference.embedding_model is None and inference.generation_model is None):
        return None
    from powercontext.builtin.runtime.composition import _embedding_models, _open_pydantic_ai_model

    embedding = None
    if inference.embedding_model is not None:
        embedding, _ = await _embedding_models(inference, resources, None)
    generation = None
    if inference.generation_model is not None:
        _, generation = await _open_pydantic_ai_model(
            inference.generation_model,
            base_url=inference.generation_base_url,
            headers=inference.generation_headers,
            resources=resources,
            instrumentation=None,
            streaming=inference.generation_streaming,
            stream_idle_timeout_seconds=inference.generation_stream_idle_timeout_seconds,
        )
    return ServerModels(embedding, generation, inference)


async def open_plugin_resources(
    database: Any, root: str | None, name: str, inference: Any, resources: AsyncExitStack
) -> tuple[SQLitePluginStorage | None, ServerModels | None]:
    storage = plugin_storage(database, root, name)
    if storage is not None:
        resources.push_async_callback(storage.aclose)
    return storage, await plugin_models(inference, resources)
