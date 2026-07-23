# ruff: noqa: TRY003

"""Executable companion for the internal Core Protocol integration guide.

The scenario treats Sources as captured evidence, Memories as derived and scoped Artifacts,
retrieval as a family-specific operation, and Triggers as pure partitioned transitions.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import ClassVar, TypeVar, cast

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from powercontext import (
    Artifact,
    ArtifactCatalog,
    ArtifactDraft,
    ArtifactLineage,
    Artifacts,
    ArtifactStore,
    PowerContext,
    Source,
    SourceAdapter,
    SourceCatalog,
    SourceMaterialization,
    Sources,
    SourceStore,
    Trigger,
)
from powercontext.artifacts import ArtifactRef
from powercontext.errors import ArtifactNotFoundError, RevisionConflictError, SourceConflictError, SourceNotFoundError
from powercontext.sources import SourceCatalogBackend
from powercontext.triggers import PolicyTransition

DAY = "2026-07-15"
ALICE = "user:alice"
BOB = "user:bob"
PROJECT = "project:powercontext"
ALICE_CONTEXT = (ALICE, PROJECT)


@dataclass(frozen=True, slots=True)
class AgentTurnInput:
    owner_id: str
    session_id: str
    round_number: int
    day: str
    user: str
    assistant: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AgentTurnSource(Source):
    owner_id: str
    session_id: str
    round_number: int
    day: str
    user: str
    assistant: str


class AgentTurnAdapter(SourceAdapter[AgentTurnInput, AgentTurnSource, AgentTurnInput]):
    input_class = AgentTurnInput
    name = "agent-turn"
    source_class = AgentTurnSource

    async def resolve(self, value: AgentTurnInput, /) -> AgentTurnSource:
        return AgentTurnSource(
            name=f"{value.session_id}/round-{value.round_number}",
            materialization=SourceMaterialization.CAPTURED,
            owner_id=value.owner_id,
            session_id=value.session_id,
            round_number=value.round_number,
            day=value.day,
            user=value.user,
            assistant=value.assistant,
        )

    async def read(self, source: AgentTurnSource, /) -> AgentTurnInput:
        return AgentTurnInput(
            owner_id=source.owner_id,
            session_id=source.session_id,
            round_number=source.round_number,
            day=source.day,
            user=source.user,
            assistant=source.assistant,
        )


@dataclass(frozen=True, slots=True)
class GitCommitInput:
    owner_id: str
    repository: str
    commit: str
    day: str
    summary: str


@dataclass(frozen=True, slots=True, kw_only=True)
class GitCommitSource(Source):
    owner_id: str
    repository: str
    commit: str
    day: str
    summary: str


class GitCommitAdapter(SourceAdapter[GitCommitInput, GitCommitSource, GitCommitInput]):
    input_class = GitCommitInput
    name = "git-commit"
    source_class = GitCommitSource

    async def resolve(self, value: GitCommitInput, /) -> GitCommitSource:
        return GitCommitSource(
            name=f"{value.repository}/{value.commit}",
            materialization=SourceMaterialization.CAPTURED,
            owner_id=value.owner_id,
            repository=value.repository,
            commit=value.commit,
            day=value.day,
            summary=value.summary,
        )

    async def read(self, source: GitCommitSource, /) -> GitCommitInput:
        return GitCommitInput(
            owner_id=source.owner_id,
            repository=source.repository,
            commit=source.commit,
            day=source.day,
            summary=source.summary,
        )


StoredSourceT = TypeVar("StoredSourceT", bound=Source)


class SQLiteSourceRepository(SourceCatalogBackend, SourceStore[Source]):
    def __init__(self, database: sqlite3.Connection) -> None:
        self._database = database
        self._database.execute(
            """
            CREATE TABLE IF NOT EXISTS sources (
                source_class TEXT NOT NULL,
                name TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (source_class, name)
            )
            """
        )

    async def add(self, source: StoredSourceT, /) -> StoredSourceT:
        source_class = self._source_class_name(source)
        stored = self._find(source_class, source.name)
        if stored is not None:
            if type(stored) is type(source) and stored == source:
                return source
            raise SourceConflictError("identity", (source_class, source.name))

        with self._database:
            self._database.execute(
                "INSERT INTO sources (source_class, name, payload) VALUES (?, ?, ?)",
                (source_class, source.name, self._encode(source)),
            )
        return source

    async def get(self, source: Source, /) -> Source:
        stored = self._find(self._source_class_name(source), source.name)
        if stored is None or type(stored) is not type(source) or stored != source:
            raise SourceNotFoundError(source)
        return stored

    async def list(self) -> tuple[Source, ...]:
        rows = self._database.execute("SELECT source_class, payload FROM sources ORDER BY rowid").fetchall()
        return tuple(self._decode(row[0], row[1]) for row in rows)

    def dump_reference(self, source: Source) -> dict[str, str]:
        return {"source_class": self._source_class_name(source), "name": source.name}

    def load_reference(self, value: object) -> Source:
        if not isinstance(value, dict):
            raise TypeError("Source reference must be an object")
        source_class = value.get("source_class")
        name = value.get("name")
        if not isinstance(source_class, str) or not isinstance(name, str):
            raise TypeError("Source reference must contain string source_class and name fields")
        stored = self._find(source_class, name)
        if stored is None:
            raise SourceNotFoundError((source_class, name))
        return stored

    def _find(self, source_class: str, name: str) -> Source | None:
        row = self._database.execute(
            "SELECT payload FROM sources WHERE source_class = ? AND name = ?",
            (source_class, name),
        ).fetchone()
        return None if row is None else self._decode(source_class, row[0])

    @staticmethod
    def _encode(source: Source) -> str:
        base = {
            "name": source.name,
            "materialization": source.materialization.value,
            "description": source.description,
        }
        if type(source) is AgentTurnSource:
            payload = base | {
                "owner_id": source.owner_id,
                "session_id": source.session_id,
                "round_number": source.round_number,
                "day": source.day,
                "user": source.user,
                "assistant": source.assistant,
            }
        elif type(source) is GitCommitSource:
            payload = base | {
                "owner_id": source.owner_id,
                "repository": source.repository,
                "commit": source.commit,
                "day": source.day,
                "summary": source.summary,
            }
        else:
            raise TypeError(f"unsupported Source type: {type(source).__name__}")
        return json.dumps(payload)

    @staticmethod
    def _decode(source_class: str, payload: str) -> Source:
        value = json.loads(payload)
        common = {
            "name": value["name"],
            "materialization": SourceMaterialization(value["materialization"]),
            "description": value["description"],
        }
        if source_class == AgentTurnSource.__name__:
            return AgentTurnSource(
                **common,
                owner_id=value["owner_id"],
                session_id=value["session_id"],
                round_number=value["round_number"],
                day=value["day"],
                user=value["user"],
                assistant=value["assistant"],
            )
        if source_class == GitCommitSource.__name__:
            return GitCommitSource(
                **common,
                owner_id=value["owner_id"],
                repository=value["repository"],
                commit=value["commit"],
                day=value["day"],
                summary=value["summary"],
            )
        raise TypeError(f"unsupported Source class: {source_class}")

    @staticmethod
    def _source_class_name(source: Source) -> str:
        if type(source) is AgentTurnSource:
            return AgentTurnSource.__name__
        if type(source) is GitCommitSource:
            return GitCommitSource.__name__
        raise TypeError(f"unsupported Source type: {type(source).__name__}")


@dataclass(frozen=True, slots=True)
class MemoryContent:
    owner_id: str
    day: str
    text: str
    keywords: tuple[str, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class MemoryDraft(ArtifactDraft[MemoryContent]):
    family: ClassVar[str] = "memory"


@dataclass(frozen=True, slots=True, kw_only=True)
class Memory(Artifact[MemoryContent]):
    family: ClassVar[str] = "memory"


@dataclass(frozen=True, slots=True)
class HandoffContent:
    scope_id: str
    day: str
    markdown: str


@dataclass(frozen=True, slots=True, kw_only=True)
class HandoffDraft(ArtifactDraft[HandoffContent]):
    family: ClassVar[str] = "handoff"


@dataclass(frozen=True, slots=True, kw_only=True)
class Handoff(Artifact[HandoffContent]):
    family: ClassVar[str] = "handoff"


class SQLiteArtifactRepository(
    ArtifactCatalog[Artifact[object]],
    ArtifactStore[ArtifactDraft[object], Artifact[object]],
):
    def __init__(self, database: sqlite3.Connection, sources: SQLiteSourceRepository) -> None:
        self._database = database
        self._sources = sources
        self._database.execute(
            """
            CREATE TABLE IF NOT EXISTS artifacts (
                artifact_id TEXT NOT NULL,
                revision INTEGER NOT NULL,
                family TEXT NOT NULL,
                content TEXT NOT NULL,
                source_lineage TEXT NOT NULL,
                artifact_lineage TEXT NOT NULL,
                PRIMARY KEY (artifact_id, revision)
            )
            """
        )

    async def add(self, draft: ArtifactDraft[object], /) -> Artifact[object]:
        artifact_id = self._next_artifact_id(draft.family)
        return self._commit(artifact_id, 1, draft)

    async def revise(
        self,
        artifact: Artifact[object],
        draft: ArtifactDraft[object],
        /,
    ) -> Artifact[object]:
        current = await self.latest(artifact)
        if current != artifact:
            raise RevisionConflictError(artifact, current)
        return self._commit(artifact.artifact_id, artifact.revision + 1, draft)

    async def get(self, artifact: Artifact[object], /) -> Artifact[object]:
        row = self._database.execute(
            """
            SELECT artifact_id, revision, family, content, source_lineage, artifact_lineage
            FROM artifacts
            WHERE artifact_id = ? AND revision = ?
            """,
            (artifact.artifact_id, artifact.revision),
        ).fetchone()
        if row is None:
            raise ArtifactNotFoundError(artifact)
        stored = self._decode(row)
        if type(stored) is not type(artifact) or stored != artifact:
            raise ArtifactNotFoundError(artifact)
        return stored

    async def latest(self, artifact: Artifact[object], /) -> Artifact[object]:
        row = self._database.execute(
            """
            SELECT artifact_id, revision, family, content, source_lineage, artifact_lineage
            FROM artifacts
            WHERE artifact_id = ?
            ORDER BY revision DESC
            LIMIT 1
            """,
            (artifact.artifact_id,),
        ).fetchone()
        if row is None:
            raise ArtifactNotFoundError(artifact)
        return self._decode(row)

    async def revisions(self, artifact: Artifact[object], /) -> tuple[Artifact[object], ...]:
        rows = self._database.execute(
            """
            SELECT artifact_id, revision, family, content, source_lineage, artifact_lineage
            FROM artifacts
            WHERE artifact_id = ?
            ORDER BY revision
            """,
            (artifact.artifact_id,),
        ).fetchall()
        if not rows:
            raise ArtifactNotFoundError(artifact)
        return tuple(self._decode(row) for row in rows)

    async def list_family(self, family: str) -> tuple[Artifact[object], ...]:
        rows = self._database.execute(
            """
            SELECT artifact_id, revision, family, content, source_lineage, artifact_lineage
            FROM artifacts
            WHERE family = ?
            ORDER BY artifact_id, revision
            """,
            (family,),
        ).fetchall()
        return tuple(self._decode(row) for row in rows)

    def _next_artifact_id(self, family: str) -> str:
        count = self._database.execute(
            "SELECT COUNT(DISTINCT artifact_id) FROM artifacts WHERE family = ?",
            (family,),
        ).fetchone()[0]
        return f"{family}-{count + 1}"

    def _commit(
        self,
        artifact_id: str,
        revision: int,
        draft: ArtifactDraft[object],
    ) -> Artifact[object]:
        source_lineage = json.dumps([self._sources.dump_reference(source) for source in draft.sources])
        artifact_lineage = json.dumps([
            {"artifact_id": dependency.artifact_id, "revision": dependency.revision} for dependency in draft.artifacts
        ])
        content = self._encode_content(draft)
        with self._database:
            self._database.execute(
                """
                INSERT INTO artifacts (
                    artifact_id,
                    revision,
                    family,
                    content,
                    source_lineage,
                    artifact_lineage
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (artifact_id, revision, draft.family, content, source_lineage, artifact_lineage),
            )
        artifact = self._load(artifact_id, revision)
        if artifact is None:
            raise RuntimeError("committed Artifact could not be loaded")
        return artifact

    @staticmethod
    def _encode_content(draft: ArtifactDraft[object]) -> str:
        if isinstance(draft, MemoryDraft):
            return json.dumps({
                "owner_id": draft.content.owner_id,
                "day": draft.content.day,
                "text": draft.content.text,
                "keywords": draft.content.keywords,
            })
        if isinstance(draft, HandoffDraft):
            return json.dumps({
                "scope_id": draft.content.scope_id,
                "day": draft.content.day,
                "markdown": draft.content.markdown,
            })
        raise TypeError(f"unsupported Artifact family: {draft.family}")

    def _load(self, artifact_id: str, revision: int) -> Artifact[object] | None:
        row = self._database.execute(
            """
            SELECT artifact_id, revision, family, content, source_lineage, artifact_lineage
            FROM artifacts
            WHERE artifact_id = ? AND revision = ?
            """,
            (artifact_id, revision),
        ).fetchone()
        return None if row is None else self._decode(row)

    def _decode(self, row: tuple[object, ...]) -> Artifact[object]:
        artifact_id, revision, family, raw_content, raw_sources, raw_artifacts = row
        source_lineage = tuple(self._sources.load_reference(reference) for reference in json.loads(str(raw_sources)))
        artifact_lineage = tuple(
            ArtifactRef(item["artifact_id"], item["revision"]) for item in json.loads(str(raw_artifacts))
        )
        lineage = ArtifactLineage(sources=source_lineage, artifacts=artifact_lineage)
        content = json.loads(str(raw_content))
        common = {
            "artifact_id": str(artifact_id),
            "revision": int(str(revision)),
            "lineage": lineage,
        }
        if family == Memory.family:
            return Memory(
                **common,
                content=MemoryContent(
                    owner_id=content["owner_id"],
                    day=content["day"],
                    text=content["text"],
                    keywords=tuple(content["keywords"]),
                ),
            )
        if family == Handoff.family:
            return Handoff(
                **common,
                content=HandoffContent(
                    scope_id=content["scope_id"],
                    day=content["day"],
                    markdown=content["markdown"],
                ),
            )
        raise TypeError(f"unsupported Artifact family: {family}")


class MemoryQueries:
    def __init__(self, artifacts: SQLiteArtifactRepository) -> None:
        self._artifacts = artifacts

    async def search(self, query: str, owner_ids: tuple[str, ...]) -> tuple[Memory, ...]:
        terms = set(query.casefold().replace("?", "").split())
        memories = await self._all()
        return tuple(
            memory
            for memory in memories
            if memory.content.owner_id in owner_ids and terms.intersection(memory.content.keywords)
        )

    async def for_day(self, day: str, owner_ids: tuple[str, ...]) -> tuple[Memory, ...]:
        memories = await self._all()
        return tuple(
            memory for memory in memories if memory.content.day == day and memory.content.owner_id in owner_ids
        )

    async def _all(self) -> tuple[Memory, ...]:
        artifacts = await self._artifacts.list_family(Memory.family)
        return tuple(artifact for artifact in artifacts if isinstance(artifact, Memory))


@dataclass(frozen=True, slots=True)
class AgentTurnStored:
    source: AgentTurnSource


@dataclass(frozen=True, slots=True)
class GitCommitStored:
    source: GitCommitSource


@dataclass(frozen=True, slots=True)
class DailyReviewDue:
    scope_id: str
    owner_ids: tuple[str, ...]
    day: str


@dataclass(frozen=True, slots=True)
class PendingTurns:
    sources: tuple[AgentTurnSource, ...] = ()


@dataclass(frozen=True, slots=True)
class UnitState:
    pass


@dataclass(frozen=True, slots=True)
class GenerateMemory:
    day: str
    sources: tuple[Source, ...]


@dataclass(frozen=True, slots=True)
class GenerateHandoff:
    scope_id: str
    owner_ids: tuple[str, ...]
    day: str


class EveryAgentRounds(Trigger[AgentTurnStored, PendingTurns, GenerateMemory]):
    def __init__(self, rounds: int) -> None:
        if rounds < 1:
            raise ValueError("rounds must be positive")
        self._rounds = rounds

    def initial_state(self) -> PendingTurns:
        return PendingTurns()

    def activate(
        self,
        signal: AgentTurnStored,
        state: PendingTurns,
        /,
    ) -> PolicyTransition[PendingTurns, GenerateMemory]:
        sources = (*state.sources, signal.source)
        if len(sources) < self._rounds:
            return PolicyTransition(state=PendingTurns(sources))
        return PolicyTransition(
            state=PendingTurns(),
            actions=(GenerateMemory(day=signal.source.day, sources=sources),),
        )


class EveryGitCommit(Trigger[GitCommitStored, UnitState, GenerateMemory]):
    def initial_state(self) -> UnitState:
        return UnitState()

    def activate(
        self,
        signal: GitCommitStored,
        state: UnitState,
        /,
    ) -> PolicyTransition[UnitState, GenerateMemory]:
        return PolicyTransition(
            state=state,
            actions=(GenerateMemory(day=signal.source.day, sources=(signal.source,)),),
        )


class DailyHandoff(Trigger[DailyReviewDue, UnitState, GenerateHandoff]):
    def initial_state(self) -> UnitState:
        return UnitState()

    def activate(
        self,
        signal: DailyReviewDue,
        state: UnitState,
        /,
    ) -> PolicyTransition[UnitState, GenerateHandoff]:
        return PolicyTransition(
            state=state,
            actions=(
                GenerateHandoff(
                    scope_id=signal.scope_id,
                    owner_ids=signal.owner_ids,
                    day=signal.day,
                ),
            ),
        )


@dataclass(frozen=True, slots=True)
class ContextTriggers:
    agent_memory: Trigger[AgentTurnStored, PendingTurns, GenerateMemory]
    git_memory: Trigger[GitCommitStored, UnitState, GenerateMemory]
    daily_handoff: Trigger[DailyReviewDue, UnitState, GenerateHandoff]


MemoryExtractor = Callable[[tuple[object, ...]], Awaitable[tuple[MemoryContent, ...]]]


class SQLiteWorkflow:
    def __init__(
        self,
        database: sqlite3.Connection,
        context: PowerContext[ContextTriggers, Artifacts],
        source_repository: SQLiteSourceRepository,
        memory_queries: MemoryQueries,
        extract_memories: MemoryExtractor,
    ) -> None:
        self._database = database
        self._context = context
        self._source_repository = source_repository
        self._memory_queries = memory_queries
        self._extract_memories = extract_memories
        self._database.executescript(
            """
            CREATE TABLE IF NOT EXISTS trigger_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger_name TEXT NOT NULL,
                partition_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS trigger_states (
                trigger_name TEXT NOT NULL,
                partition_key TEXT NOT NULL,
                payload TEXT NOT NULL,
                PRIMARY KEY (trigger_name, partition_key)
            );
            CREATE TABLE IF NOT EXISTS pending_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                completed INTEGER NOT NULL DEFAULT 0
            );
            """
        )

    async def enqueue_agent_turn(self, source: AgentTurnSource) -> None:
        self._enqueue_signal(
            "agent_memory",
            f"{source.owner_id}:{source.session_id}",
            {"source": self._source_repository.dump_reference(source)},
        )

    async def enqueue_git_commit(self, source: GitCommitSource) -> None:
        self._enqueue_signal(
            "git_memory",
            f"{source.owner_id}:{source.repository}",
            {"source": self._source_repository.dump_reference(source)},
        )

    async def enqueue_daily_review(
        self,
        scope_id: str,
        owner_ids: tuple[str, ...],
        day: str,
    ) -> None:
        self._enqueue_signal(
            "daily_handoff",
            scope_id,
            {"scope_id": scope_id, "owner_ids": owner_ids, "day": day},
        )

    async def process_signals(self) -> None:
        rows = self._database.execute(
            """
            SELECT id, trigger_name, partition_key, payload
            FROM trigger_signals
            WHERE completed = 0
            ORDER BY id
            """
        ).fetchall()
        for signal_id, trigger_name, partition_key, raw_payload in rows:
            state, actions = self._activate(
                str(trigger_name),
                str(partition_key),
                json.loads(str(raw_payload)),
            )
            with self._database:
                self._database.execute(
                    """
                    INSERT INTO trigger_states (trigger_name, partition_key, payload)
                    VALUES (?, ?, ?)
                    ON CONFLICT(trigger_name, partition_key)
                    DO UPDATE SET payload = excluded.payload
                    """,
                    (trigger_name, partition_key, json.dumps(state)),
                )
                for action_type, payload in actions:
                    self._database.execute(
                        "INSERT INTO pending_actions (action_type, payload) VALUES (?, ?)",
                        (action_type, json.dumps(payload)),
                    )
                self._database.execute(
                    "UPDATE trigger_signals SET completed = 1 WHERE id = ?",
                    (signal_id,),
                )

    async def process_actions(self) -> None:
        rows = self._database.execute(
            "SELECT id, action_type, payload FROM pending_actions WHERE completed = 0 ORDER BY id"
        ).fetchall()
        for action_id, action_type, raw_payload in rows:
            payload = json.loads(str(raw_payload))
            if action_type == "generate_memory":
                await self._generate_memory(payload)
            elif action_type == "generate_handoff":
                await self._generate_handoff(payload)
            else:
                raise TypeError(f"unsupported Action type: {action_type}")
            with self._database:
                self._database.execute(
                    "UPDATE pending_actions SET completed = 1 WHERE id = ?",
                    (action_id,),
                )

    def _activate(
        self,
        trigger_name: str,
        partition_key: str,
        payload: dict[str, object],
    ) -> tuple[dict[str, object], tuple[tuple[str, dict[str, object]], ...]]:
        if trigger_name == "agent_memory":
            trigger = self._context.triggers.agent_memory
            state_payload = self._load_state(trigger_name, partition_key)
            if state_payload is None:
                state = trigger.initial_state()
            else:
                stored_sources = self._load_sources(state_payload["sources"])
                if not all(type(source) is AgentTurnSource for source in stored_sources):
                    raise TypeError("agent_memory state requires AgentTurnSource values")
                state = PendingTurns(cast(tuple[AgentTurnSource, ...], stored_sources))
            source = self._load_source(payload["source"])
            if type(source) is not AgentTurnSource:
                raise TypeError("agent_memory requires an AgentTurnSource")
            transition = trigger.activate(AgentTurnStored(source), state)
            return (
                {"sources": [self._source_repository.dump_reference(item) for item in transition.state.sources]},
                tuple(self._encode_action(action) for action in transition.actions),
            )

        if trigger_name == "git_memory":
            trigger = self._context.triggers.git_memory
            state = trigger.initial_state() if self._load_state(trigger_name, partition_key) is None else UnitState()
            source = self._load_source(payload["source"])
            if type(source) is not GitCommitSource:
                raise TypeError("git_memory requires a GitCommitSource")
            transition = trigger.activate(GitCommitStored(source), state)
            return ({}, tuple(self._encode_action(action) for action in transition.actions))

        if trigger_name == "daily_handoff":
            trigger = self._context.triggers.daily_handoff
            state = trigger.initial_state() if self._load_state(trigger_name, partition_key) is None else UnitState()
            owner_ids = payload["owner_ids"]
            if not isinstance(owner_ids, list):
                raise TypeError("daily_handoff owner_ids must be a list")
            transition = trigger.activate(
                DailyReviewDue(
                    scope_id=str(payload["scope_id"]),
                    owner_ids=tuple(str(owner_id) for owner_id in owner_ids),
                    day=str(payload["day"]),
                ),
                state,
            )
            return ({}, tuple(self._encode_action(action) for action in transition.actions))

        raise TypeError(f"unsupported Trigger name: {trigger_name}")

    async def _generate_memory(self, payload: dict[str, object]) -> None:
        sources = self._load_sources(payload["sources"])
        values = tuple([await self._context.sources.read(source) for source in sources])
        extracted = await self._extract_memories(values)
        for memory in extracted:
            await self._context.artifacts.add(
                MemoryDraft(
                    content=memory,
                    sources=sources,
                )
            )

    async def _generate_handoff(self, payload: dict[str, object]) -> None:
        day = str(payload["day"])
        owner_ids = payload["owner_ids"]
        if not isinstance(owner_ids, list):
            raise TypeError("generate_handoff owner_ids must be a list")
        memories = await self._memory_queries.for_day(
            day,
            tuple(str(owner_id) for owner_id in owner_ids),
        )
        markdown = "\n".join([f"- {memory.content.text}" for memory in memories])
        await self._context.artifacts.add(
            HandoffDraft(
                content=HandoffContent(
                    scope_id=str(payload["scope_id"]),
                    day=day,
                    markdown=markdown,
                ),
                artifacts=memories,
            )
        )

    def _enqueue_signal(
        self,
        trigger_name: str,
        partition_key: str,
        payload: dict[str, object],
    ) -> None:
        with self._database:
            self._database.execute(
                """
                INSERT INTO trigger_signals (trigger_name, partition_key, payload)
                VALUES (?, ?, ?)
                """,
                (trigger_name, partition_key, json.dumps(payload)),
            )

    def _load_state(self, trigger_name: str, partition_key: str) -> dict[str, object] | None:
        row = self._database.execute(
            """
            SELECT payload
            FROM trigger_states
            WHERE trigger_name = ? AND partition_key = ?
            """,
            (trigger_name, partition_key),
        ).fetchone()
        return None if row is None else json.loads(row[0])

    def _load_source(self, value: object) -> Source:
        return self._source_repository.load_reference(value)

    def _load_sources(self, value: object) -> tuple[Source, ...]:
        if not isinstance(value, list):
            raise TypeError("Source references must be a list")
        return tuple(self._load_source(item) for item in value)

    def _encode_action(self, action: GenerateMemory | GenerateHandoff) -> tuple[str, dict[str, object]]:
        if isinstance(action, GenerateMemory):
            return (
                "generate_memory",
                {
                    "day": action.day,
                    "sources": [self._source_repository.dump_reference(source) for source in action.sources],
                },
            )
        return (
            "generate_handoff",
            {
                "scope_id": action.scope_id,
                "owner_ids": action.owner_ids,
                "day": action.day,
            },
        )


class RecordingModel:
    def __init__(self) -> None:
        self.memory_snapshots: list[tuple[str, ...]] = []

    async def __call__(self, user_message: str, memories: tuple[Memory, ...]) -> str:
        self.memory_snapshots.append(tuple(memory.content.text for memory in memories))
        return f"reply to {user_message}"


async def extract_memories(materials: tuple[object, ...]) -> tuple[MemoryContent, ...]:
    extracted: list[MemoryContent] = []
    for material in materials:
        if isinstance(material, AgentTurnInput):
            if material.user == "Remember aisle seats":
                extracted.append(
                    MemoryContent(
                        owner_id=material.owner_id,
                        day=material.day,
                        text="Alice prefers aisle seats.",
                        keywords=("alice", "aisle", "seat", "seats"),
                    )
                )
            elif material.user == "Avoid overnight flights":
                extracted.append(
                    MemoryContent(
                        owner_id=material.owner_id,
                        day=material.day,
                        text="Alice avoids overnight flights.",
                        keywords=("alice", "avoid", "avoids", "overnight", "flight", "flights"),
                    )
                )
            elif material.user == "Remember window seats":
                extracted.append(
                    MemoryContent(
                        owner_id=material.owner_id,
                        day=material.day,
                        text="Bob prefers window seats.",
                        keywords=("bob", "window", "seat", "seats"),
                    )
                )
        elif isinstance(material, GitCommitInput):
            extracted.append(
                MemoryContent(
                    owner_id=material.owner_id,
                    day=material.day,
                    text=f"Commit {material.commit} {material.summary}.",
                    keywords=("commit", material.commit.casefold(), "core", "protocol"),
                )
            )
        else:
            raise TypeError(f"unsupported material type: {type(material).__name__}")
    return tuple(extracted)


async def run_agent_turn(
    context: PowerContext[ContextTriggers, Artifacts],
    workflow: SQLiteWorkflow,
    memory_queries: MemoryQueries,
    model: RecordingModel,
    *,
    owner_id: str,
    context_owner_ids: tuple[str, ...],
    session_id: str,
    round_number: int,
    user_message: str,
) -> AgentTurnSource:
    memories = await memory_queries.search(user_message, context_owner_ids)
    assistant = await model(user_message, memories)
    resolved = await context.sources.resolve(
        AgentTurnInput(
            owner_id=owner_id,
            session_id=session_id,
            round_number=round_number,
            day=DAY,
            user=user_message,
            assistant=assistant,
        )
    )
    source = await context.sources.add(resolved)
    if type(source) is not AgentTurnSource:
        raise TypeError("AgentTurnAdapter returned an unexpected Source")
    await workflow.enqueue_agent_turn(source)
    await workflow.process_signals()
    await workflow.process_actions()
    return source


def test_context_system_derives_scoped_memory_and_scheduled_handoff() -> None:
    async def scenario() -> None:
        database = sqlite3.connect(":memory:")
        source_repository = SQLiteSourceRepository(database)
        artifact_repository = SQLiteArtifactRepository(database, source_repository)
        memory_queries = MemoryQueries(artifact_repository)
        context = PowerContext(
            sources=Sources(
                catalog=SourceCatalog(
                    backend=source_repository,
                    adapters=(AgentTurnAdapter(), GitCommitAdapter()),
                ),
                store=source_repository,
            ),
            artifacts=Artifacts(
                catalog=artifact_repository,
                store=artifact_repository,
            ),
            triggers=ContextTriggers(
                agent_memory=EveryAgentRounds(2),
                git_memory=EveryGitCommit(),
                daily_handoff=DailyHandoff(),
            ),
        )
        alice_model = RecordingModel()
        bob_model = RecordingModel()
        workflow = SQLiteWorkflow(database, context, source_repository, memory_queries, extract_memories)

        first_source = await run_agent_turn(
            context,
            workflow,
            memory_queries,
            alice_model,
            owner_id=ALICE,
            context_owner_ids=ALICE_CONTEXT,
            session_id="alice-session",
            round_number=1,
            user_message="Remember aisle seats",
        )
        assert await memory_queries.search("aisle", ALICE_CONTEXT) == ()

        bob_first_source = await run_agent_turn(
            context,
            workflow,
            memory_queries,
            bob_model,
            owner_id=BOB,
            context_owner_ids=(BOB,),
            session_id="bob-session",
            round_number=1,
            user_message="Remember window seats",
        )
        assert await memory_queries.for_day(DAY, (BOB,)) == ()

        workflow = SQLiteWorkflow(database, context, source_repository, memory_queries, extract_memories)
        second_source = await run_agent_turn(
            context,
            workflow,
            memory_queries,
            alice_model,
            owner_id=ALICE,
            context_owner_ids=ALICE_CONTEXT,
            session_id="alice-session",
            round_number=2,
            user_message="Avoid overnight flights",
        )

        bob_second_source = await run_agent_turn(
            context,
            workflow,
            memory_queries,
            bob_model,
            owner_id=BOB,
            context_owner_ids=(BOB,),
            session_id="bob-session",
            round_number=2,
            user_message="Thanks",
        )

        git_source = await context.sources.add(
            await context.sources.resolve(
                GitCommitInput(
                    owner_id=PROJECT,
                    repository="alice-session",
                    commit="round-1",
                    day=DAY,
                    summary="Define the Core Protocol",
                )
            )
        )
        assert type(git_source) is GitCommitSource
        assert git_source.name == first_source.name
        await workflow.enqueue_git_commit(git_source)
        await workflow.process_signals()
        await workflow.process_actions()
        assert len(await artifact_repository.list_family(Memory.family)) == 4

        third_source = await run_agent_turn(
            context,
            workflow,
            memory_queries,
            alice_model,
            owner_id=ALICE,
            context_owner_ids=ALICE_CONTEXT,
            session_id="alice-session",
            round_number=3,
            user_message="Should I take an overnight flight?",
        )
        fourth_source = await run_agent_turn(
            context,
            workflow,
            memory_queries,
            alice_model,
            owner_id=ALICE,
            context_owner_ids=ALICE_CONTEXT,
            session_id="alice-session",
            round_number=4,
            user_message="What did commit round-1 change?",
        )

        memories = await memory_queries.for_day(DAY, ALICE_CONTEXT)
        bob_memories = await memory_queries.for_day(DAY, (BOB,))
        assert len(await artifact_repository.list_family(Memory.family)) == 4
        assert len(memories) == 3
        assert len(bob_memories) == 1
        assert memories[0].lineage.sources == (first_source, second_source)
        assert memories[1].lineage.sources == (first_source, second_source)
        assert memories[2].lineage.sources == (git_source,)
        assert bob_memories[0].lineage.sources == (bob_first_source, bob_second_source)
        assert alice_model.memory_snapshots == [
            (),
            (),
            ("Alice avoids overnight flights.",),
            ("Commit round-1 Define the Core Protocol.",),
        ]
        assert bob_model.memory_snapshots == [(), ()]

        scheduled = asyncio.Event()

        async def publish_daily_review() -> None:
            await workflow.enqueue_daily_review("alice-daily", ALICE_CONTEXT, DAY)
            scheduled.set()

        scheduler = AsyncIOScheduler(timezone=UTC)
        scheduler.add_job(
            publish_daily_review,
            "date",
            run_date=datetime.now(UTC) + timedelta(milliseconds=100),
        )
        scheduler.start()
        try:
            await asyncio.wait_for(scheduled.wait(), timeout=3)
        finally:
            scheduler.shutdown(wait=False)

        assert await artifact_repository.list_family(Handoff.family) == ()
        workflow = SQLiteWorkflow(database, context, source_repository, memory_queries, extract_memories)
        await workflow.process_signals()
        await workflow.process_actions()

        handoffs = await artifact_repository.list_family(Handoff.family)
        assert len(handoffs) == 1
        handoff = handoffs[0]
        assert isinstance(handoff, Handoff)
        assert handoff.lineage.artifacts == tuple(memory.ref for memory in memories)
        assert handoff.content.scope_id == "alice-daily"
        assert handoff.content.markdown == "\n".join([f"- {memory.content.text}" for memory in memories])

        sources = await context.sources.list()
        assert sources == (
            first_source,
            bob_first_source,
            second_source,
            bob_second_source,
            git_source,
            third_source,
            fourth_source,
        )

    asyncio.run(scenario())
