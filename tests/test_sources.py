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
from copy import deepcopy
from dataclasses import dataclass
from typing import TypeVar

import pytest

from powercontext import (
    InvalidSourceEntryError,
    InvalidSourceResultError,
    SourceAdapterNotFoundError,
    SourceNotFoundError,
)
from powercontext.context import Sources
from powercontext.sources import (
    Source,
    SourceAdapter,
    SourceCatalog,
    SourceCatalogBackend,
    SourceMaterialization,
    SourceStore,
)


@dataclass(frozen=True, slots=True)
class Conversation:
    messages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ConversationCapture:
    name: str
    session_id: str
    capture: bool = False


@dataclass(frozen=True, slots=True)
class TranscriptExportInput:
    name: str


class ConversationSource(Source):
    session_id: str
    captured_value: Conversation | None = None


class TranscriptExportSource(Source):
    pass


class UnknownConversationSource(Source):
    pass


class ConversationAdapter(SourceAdapter[ConversationCapture, ConversationSource, Conversation]):
    input_class = ConversationCapture
    name = "conversation"
    source_class = ConversationSource

    def __init__(self, conversations: dict[str, Conversation]) -> None:
        self.conversations = conversations

    async def resolve(self, value: ConversationCapture) -> ConversationSource:
        captured_value = self.conversations[value.session_id] if value.capture else None
        materialization = SourceMaterialization.CAPTURED if value.capture else SourceMaterialization.REFERENCED
        return ConversationSource(
            name=value.name,
            materialization=materialization,
            session_id=value.session_id,
            captured_value=captured_value,
        )

    async def read(self, source: ConversationSource) -> Conversation:
        if source.materialization is SourceMaterialization.CAPTURED:
            assert source.captured_value is not None
            return source.captured_value
        return self.conversations[source.session_id]


class TranscriptExportAdapter(SourceAdapter[TranscriptExportInput, TranscriptExportSource, object]):
    input_class = TranscriptExportInput
    name = "transcript-export"
    source_class = TranscriptExportSource

    async def resolve(self, value: TranscriptExportInput) -> TranscriptExportSource:
        return TranscriptExportSource(name=value.name, materialization=SourceMaterialization.REFERENCED)

    async def read(self, source: TranscriptExportSource) -> object:
        return source


StoredSourceT = TypeVar("StoredSourceT", bound=Source)


class InMemorySourceStore(SourceCatalogBackend, SourceStore[Source]):
    def __init__(self, *sources: Source) -> None:
        self.sources = list(sources)

    async def add(self, source: StoredSourceT, /) -> StoredSourceT:
        canonical = deepcopy(source)
        self.sources.append(canonical)
        return canonical

    async def get(self, source: Source, /) -> Source:
        for stored in self.sources:
            if stored == source and type(stored) is type(source):
                return stored
        raise SourceNotFoundError(source)

    async def list(self) -> tuple[Source, ...]:
        return tuple(self.sources)


def test_catalog_rejects_invalid_backend_results() -> None:
    class InvalidBackend(InMemorySourceStore):
        async def list(self) -> tuple[Source, ...]:
            return (object(),)  # ty: ignore[invalid-return-type]

    async def scenario() -> None:
        catalog = SourceCatalog(backend=InvalidBackend(), adapters=())
        with pytest.raises(InvalidSourceEntryError) as error:
            await catalog.list()
        assert error.value.actual_type is object

    asyncio.run(scenario())


def test_source_catalog_supports_the_read_only_usage_flow() -> None:
    async def scenario() -> None:
        conversations = {
            "session-42": Conversation(("I prefer aisle seats.",)),
        }
        adapter = ConversationAdapter(conversations)
        backend = InMemorySourceStore()
        catalog = SourceCatalog(
            backend=backend,
            adapters=(adapter,),
        )
        sources = Sources(catalog=catalog, store=backend)

        captured_input = ConversationCapture("session-42-snapshot", "session-42", capture=True)
        captured_resolved = await sources.resolve(captured_input)
        assert await sources.list() == ()
        captured = await sources.add(captured_resolved)
        assert type(captured) is ConversationSource
        referenced = await sources.add(await sources.resolve(ConversationCapture("session-42-current", "session-42")))

        assert captured == captured_resolved

        conversations["session-42"] = Conversation((
            "I prefer aisle seats.",
            "Please remember that for future trips.",
        ))

        assert await sources.get(captured) == captured
        assert await sources.list() == (captured, referenced)
        assert await sources.read(captured) == Conversation(("I prefer aisle seats.",))
        assert await sources.read(referenced) == conversations["session-42"]

        resolved = await sources.resolve(ConversationCapture("session-42-next-view", "session-42"))
        assert resolved.name == "session-42-next-view"
        assert resolved.materialization is SourceMaterialization.REFERENCED
        assert await sources.list() == (captured, referenced)

        detached = ConversationSource(
            name=captured.name,
            materialization=captured.materialization,
            session_id="session-detached",
            captured_value=captured.captured_value,
        )
        with pytest.raises(SourceNotFoundError) as error:
            await sources.get(detached)
        assert error.value.source is detached

    asyncio.run(scenario())


def test_catalog_rejects_persisted_sources_without_an_adapter() -> None:
    async def scenario() -> None:
        source = UnknownConversationSource(
            name="captured-event",
            materialization=SourceMaterialization.CAPTURED,
        )
        catalog = SourceCatalog(backend=InMemorySourceStore(source), adapters=())

        with pytest.raises(SourceAdapterNotFoundError):
            await catalog.list()
        with pytest.raises(SourceAdapterNotFoundError):
            await catalog.get(source)

    asyncio.run(scenario())


def test_catalog_routes_only_exact_input_and_source_classes() -> None:
    @dataclass(frozen=True, slots=True)
    class SpecializedConversationCapture(ConversationCapture):
        pass

    async def scenario() -> None:
        adapter = ConversationAdapter({"session-42": Conversation(("Remember aisle seats.",))})
        catalog = SourceCatalog(
            backend=InMemorySourceStore(),
            adapters=(adapter,),
        )

        with pytest.raises(SourceAdapterNotFoundError) as input_error:
            await catalog.resolve(SpecializedConversationCapture("session-42", "session-42"))
        assert input_error.value.route == "input"
        assert input_error.value.requested_type is SpecializedConversationCapture

        unknown = UnknownConversationSource(name="unknown", materialization=SourceMaterialization.REFERENCED)
        with pytest.raises(SourceAdapterNotFoundError) as source_error:
            await catalog.read(unknown)
        assert source_error.value.route == "source"
        assert source_error.value.requested_type is UnknownConversationSource

        base_source = Source(name="base", materialization=SourceMaterialization.REFERENCED)
        with pytest.raises(SourceAdapterNotFoundError) as base_source_error:
            await catalog.read(base_source)
        assert base_source_error.value.requested_type is Source

    asyncio.run(scenario())


def test_adapter_results_and_declarations_are_checked_at_the_boundary() -> None:
    class InvalidResultAdapter(SourceAdapter[ConversationCapture, ConversationSource, object]):
        input_class = ConversationCapture
        name = "conversation"
        source_class = ConversationSource

        async def resolve(self, value: ConversationCapture, /) -> ConversationSource:
            return TranscriptExportSource(  # ty: ignore[invalid-return-type]
                name=value.name,
                materialization=SourceMaterialization.REFERENCED,
            )

        async def read(self, source: ConversationSource, /) -> object:
            return source

    async def scenario() -> None:
        catalog = SourceCatalog(
            backend=InMemorySourceStore(),
            adapters=(InvalidResultAdapter(),),
        )
        with pytest.raises(InvalidSourceResultError) as error:
            await catalog.resolve(ConversationCapture("session-42", "session-42"))
        assert error.value.operation == "resolve"
        assert error.value.expected_type is ConversationSource
        assert error.value.actual_type is TranscriptExportSource

    asyncio.run(scenario())


def test_source_composition_rejects_unregistered_values_before_storage() -> None:
    async def scenario() -> None:
        backend = InMemorySourceStore()
        catalog = SourceCatalog(backend=backend, adapters=())
        sources = Sources(catalog=catalog, store=backend)
        unknown = UnknownConversationSource(
            name="unknown",
            materialization=SourceMaterialization.REFERENCED,
        )

        with pytest.raises(SourceAdapterNotFoundError):
            await sources.add(unknown)
        assert backend.sources == []

    asyncio.run(scenario())
