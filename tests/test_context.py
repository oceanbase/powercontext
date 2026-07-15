from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import ClassVar, TypeVar

import pytest

from powercontext import ArtifactFamilyMismatchError, Artifacts, PowerContext, Sources
from powercontext.artifacts import Artifact, ArtifactCatalog, ArtifactDraft, ArtifactLineage, ArtifactRef, ArtifactStore
from powercontext.errors import ArtifactNotFoundError, RevisionConflictError, SourceNotFoundError
from powercontext.sources import (
    Source,
    SourceAdapter,
    SourceCatalog,
    SourceCatalogBackend,
    SourceMaterialization,
    SourceStore,
)
from powercontext.triggers import PolicyTransition, Trigger


@dataclass(frozen=True, slots=True)
class ConversationCapture:
    name: str
    session_id: str
    messages: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Conversation:
    messages: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ConversationSource(Source):
    source_type: ClassVar[str] = "conversation"

    session_id: str
    captured_value: Conversation


class ConversationAdapter(SourceAdapter[ConversationCapture, ConversationSource, Conversation]):
    input_type = ConversationCapture
    source_type = ConversationSource.source_type
    source_class = ConversationSource

    async def resolve(self, value: ConversationCapture, /) -> ConversationSource:
        return ConversationSource(
            name=value.name,
            materialization=SourceMaterialization.CAPTURED,
            session_id=value.session_id,
            captured_value=Conversation(value.messages),
        )

    async def read(self, source: ConversationSource, /) -> Conversation:
        return source.captured_value


@dataclass(frozen=True, slots=True)
class ConversationStored:
    source: ConversationSource


@dataclass(frozen=True, slots=True)
class UnitState:
    pass


@dataclass(frozen=True, slots=True)
class ExtractConversation:
    source: ConversationSource


class ExtractStoredConversation(Trigger[ConversationStored, UnitState, ExtractConversation]):
    def initial_state(self) -> UnitState:
        return UnitState()

    def activate(
        self,
        signal: ConversationStored,
        state: UnitState,
        /,
    ) -> PolicyTransition[UnitState, ExtractConversation]:
        return PolicyTransition(state=state, actions=(ExtractConversation(signal.source),))


@dataclass(frozen=True, slots=True)
class ContextTriggers:
    memory: Trigger[ConversationStored, UnitState, ExtractConversation]


StoredSourceT = TypeVar("StoredSourceT", bound=Source)


class SourceBackend(SourceCatalogBackend, SourceStore[Source]):
    def __init__(self) -> None:
        self._sources: list[Source] = []

    async def add(self, source: StoredSourceT, /) -> StoredSourceT:
        self._sources.append(source)
        return source

    async def get(self, source: Source, /) -> Source:
        for stored in self._sources:
            if stored == source and type(stored) is type(source):
                return stored
        raise SourceNotFoundError(source)

    async def list(self) -> tuple[Source, ...]:
        return tuple(self._sources)


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractedMemoryDraft(ArtifactDraft[tuple[str, ...]]):
    family: ClassVar[str] = "extracted-memory"


@dataclass(frozen=True, slots=True, kw_only=True)
class ExtractedMemory(Artifact[tuple[str, ...]]):
    family: ClassVar[str] = "extracted-memory"


@dataclass(frozen=True, slots=True, kw_only=True)
class HandoffDraft(ArtifactDraft[str]):
    family: ClassVar[str] = "handoff"


class ArtifactBackend(
    ArtifactCatalog[Artifact[object]],
    ArtifactStore[ArtifactDraft[object], Artifact[object]],
):
    def __init__(self) -> None:
        self._artifacts: dict[ArtifactRef, ExtractedMemory] = {}

    async def add(self, draft: ArtifactDraft[object], /) -> Artifact[object]:
        if not isinstance(draft, ExtractedMemoryDraft):
            raise TypeError
        return self._commit("memory-1", 1, draft)

    async def revise(
        self,
        artifact: Artifact[object],
        draft: ArtifactDraft[object],
        /,
    ) -> Artifact[object]:
        if not isinstance(artifact, ExtractedMemory) or not isinstance(draft, ExtractedMemoryDraft):
            raise TypeError
        current = await self.latest(artifact)
        if current != artifact:
            raise RevisionConflictError(artifact, current)
        return self._commit(artifact.artifact_id, artifact.revision + 1, draft)

    async def get(self, artifact: Artifact[object], /) -> Artifact[object]:
        if not isinstance(artifact, ExtractedMemory):
            raise ArtifactNotFoundError(artifact)
        stored = self._artifacts.get(artifact.ref)
        if stored is None or stored != artifact:
            raise ArtifactNotFoundError(artifact)
        return stored

    async def latest(self, artifact: Artifact[object], /) -> Artifact[object]:
        return (await self.revisions(artifact))[-1]

    async def revisions(self, artifact: Artifact[object], /) -> tuple[Artifact[object], ...]:
        if not isinstance(artifact, ExtractedMemory):
            raise ArtifactNotFoundError(artifact)
        matches = (item for ref, item in self._artifacts.items() if ref.artifact_id == artifact.artifact_id)
        return tuple(sorted(matches, key=lambda item: item.revision))

    def _commit(self, artifact_id: str, revision: int, draft: ExtractedMemoryDraft) -> ExtractedMemory:
        artifact = ExtractedMemory(
            artifact_id=artifact_id,
            revision=revision,
            content=draft.content,
            lineage=ArtifactLineage(
                sources=draft.sources,
                artifacts=tuple(item.ref for item in draft.artifacts),
            ),
        )
        self._artifacts[artifact.ref] = artifact
        return artifact


def extract_facts(conversation: Conversation) -> tuple[str, ...]:
    """Stand in for an application-owned extraction component."""

    transcript = " ".join(conversation.messages).casefold()
    facts = []
    if "aisle" in transcript:
        facts.append("User prefers aisle seats.")
    if "overnight" in transcript:
        facts.append("User avoids overnight flights.")
    return tuple(facts)


def test_powercontext_supports_application_owned_composition() -> None:
    async def scenario() -> None:
        source_backend = SourceBackend()
        source_catalog = SourceCatalog(backend=source_backend, adapters=(ConversationAdapter(),))
        artifact_backend = ArtifactBackend()
        artifacts = Artifacts(catalog=artifact_backend, store=artifact_backend)
        pc = PowerContext(
            sources=Sources(catalog=source_catalog, store=source_backend),
            artifacts=artifacts,
            triggers=ContextTriggers(memory=ExtractStoredConversation()),
        )

        resolved = await pc.sources.resolve(
            ConversationCapture(
                name="session-42-snapshot",
                session_id="session-42",
                messages=("Please book aisle seats for future trips.",),
            )
        )
        conversation_source = await pc.sources.add(resolved)
        assert isinstance(conversation_source, ConversationSource)
        memory_trigger = pc.triggers.memory
        transition = memory_trigger.activate(
            ConversationStored(conversation_source),
            memory_trigger.initial_state(),
        )
        assert transition.actions == (ExtractConversation(conversation_source),)
        assert await pc.sources.get(conversation_source) == conversation_source
        conversation = await pc.sources.read(conversation_source)
        assert isinstance(conversation, Conversation)

        extracted_facts = extract_facts(conversation)
        first = await pc.artifacts.add(ExtractedMemoryDraft(content=extracted_facts, sources=(conversation_source,)))
        assert isinstance(first, ExtractedMemory)

        followup_source = await pc.sources.add(
            await pc.sources.resolve(
                ConversationCapture(
                    name="session-43-snapshot",
                    session_id="session-43",
                    messages=("I avoid overnight flights whenever possible.",),
                )
            )
        )
        followup = await pc.sources.read(followup_source)
        assert isinstance(followup, Conversation)
        second = await pc.artifacts.revise(
            first,
            ExtractedMemoryDraft(
                content=first.content + extract_facts(followup),
                sources=(followup_source,),
                artifacts=(first,),
            ),
        )
        assert isinstance(second, ExtractedMemory)

        exact = await pc.artifacts.get(first)
        assert exact == first
        assert await pc.artifacts.latest(first) == second
        assert await pc.artifacts.revisions(first) == (first, second)
        assert first.content == ("User prefers aisle seats.",)
        assert first.lineage.sources == (conversation_source,)
        assert second.content == (
            "User prefers aisle seats.",
            "User avoids overnight flights.",
        )
        assert second.lineage.sources == (followup_source,)
        assert second.lineage.artifacts == (first.ref,)

        with pytest.raises(RevisionConflictError) as error:
            await pc.artifacts.revise(
                first,
                ExtractedMemoryDraft(content=("Stale preference extraction.",)),
            )
        assert error.value.artifact is first
        assert error.value.current is second
        assert await pc.artifacts.revisions(first) == (first, second)

        draft = HandoffDraft(content="handoff")
        with pytest.raises(ArtifactFamilyMismatchError) as mismatch:
            await pc.artifacts.revise(second, draft)
        assert mismatch.value.artifact is second
        assert mismatch.value.draft is draft
        assert await pc.artifacts.revisions(second) == (first, second)

    asyncio.run(scenario())
