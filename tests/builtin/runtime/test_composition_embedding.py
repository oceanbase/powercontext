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

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack
from typing import ClassVar

from pydantic_ai import Embedder

from powercontext.builtin.runtime.composition import _embedding_models
from powercontext.builtin.runtime.config import InferenceConfig


class _SpyEmbedder(Embedder):
    settings_seen: ClassVar[list[dict[str, int] | None]] = []

    def __init__(self, model, *, settings=None, defer_model_check=True, instrument=None):
        super().__init__(model, settings=settings, defer_model_check=defer_model_check, instrument=instrument)
        type(self).settings_seen.append(settings)


def test_embedding_models_send_the_configured_dimension_to_the_provider(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setattr("pydantic_ai.Embedder", _SpyEmbedder)
    _SpyEmbedder.settings_seen = []

    async def scenario() -> None:
        config = InferenceConfig(
            embedding_model="openai:text-embedding-3-small",
            embedding_profile_id="bailian-1536-v1",
            embedding_dimension=1536,
        )
        async with AsyncExitStack() as resources:
            operational, readiness = await _embedding_models(config, resources, None)
        assert operational is not None
        assert readiness is not None
        assert _SpyEmbedder.settings_seen == [{"dimensions": 1536}, {"dimensions": 1536}]

    asyncio.run(scenario())


def test_embedding_models_without_configuration_return_no_models() -> None:
    async def scenario() -> None:
        async with AsyncExitStack() as resources:
            operational, readiness = await _embedding_models(InferenceConfig(), resources, None)
        assert operational is None
        assert readiness is None

    asyncio.run(scenario())
