from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.messages import BaseMessage, HumanMessage
from powermem import AsyncMemory, Memory
from powermem_langchain import PowerMemMiddleware
from pydantic import Field


class CapturingChatModel(SimpleChatModel):
    responses: list[str] = Field(default_factory=lambda: ["ok"])
    calls: list[list[BaseMessage]] = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "capturing-chat-model"

    def _call(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any | None = None,
        **kwargs: Any,
    ) -> str:
        self.calls.append(list(messages))
        index = min(len(self.calls) - 1, len(self.responses) - 1)
        return self.responses[index]


class FailingSearchMemory:
    def search(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("search failed")


class FailingAddMemory:
    """search succeeds, but add raises: exercises write-back fail-open."""

    def search(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"results": []}

    def add(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("add failed")


class FailingAsyncSearchMemory:
    """An async memory whose ``search`` rejects: exercises async fail-open."""

    async def search(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("async search failed")

    async def add(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"results": []}


class FailingAsyncAddMemory:
    """An async memory whose ``add`` rejects: exercises async write-back fail-open."""

    async def search(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return {"results": []}

    async def add(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("async add failed")


class RecordingMemory:
    """Records every search/add call's args/kwargs for exact-argument assertions.

    Returns a canned search result so retrieval produces injectable context; the
    point is not what PowerMem returns but that the middleware calls search/add
    with the precise arguments derived from the run.
    """

    def __init__(self, search_result: dict[str, Any] | None = None) -> None:
        self.search_calls: list[dict[str, Any]] = []
        self.add_calls: list[dict[str, Any]] = []
        self._search_result = search_result or {"results": []}

    def search(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.search_calls.append({"args": args, "kwargs": kwargs})
        return self._search_result

    def add(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.add_calls.append({"args": args, "kwargs": kwargs})
        return {"results": []}


def _message_text(messages: list[BaseMessage]) -> str:
    return "\n".join(str(message.content) for message in messages)


def _stored_memory_text(memory: Memory, user_id: str) -> str:
    result = memory.get_all(user_id=user_id)
    return "\n".join(item["memory"] for item in result["results"])


def _sqlite_memory(tmp_path: Path) -> Memory:
    return Memory(
        config={
            "vector_store": {
                "provider": "sqlite",
                "config": {
                    "database_path": str(tmp_path / "powermem_langchain.db"),
                    "collection_name": f"memories_{uuid.uuid4().hex[:8]}",
                },
            },
            "llm": {
                "provider": "noop",
                "config": {"model": "noop"},
            },
            "embedder": {
                "provider": "mock",
                "config": {"embedding_dims": 16},
            },
        }
    )


def _async_sqlite_memory(tmp_path: Path) -> AsyncMemory:
    return AsyncMemory(
        config={
            "vector_store": {
                "provider": "sqlite",
                "config": {
                    "database_path": str(tmp_path / "powermem_langchain_async.db"),
                    "collection_name": f"memories_{uuid.uuid4().hex[:8]}",
                },
            },
            "llm": {
                "provider": "noop",
                "config": {"model": "noop"},
            },
            "embedder": {
                "provider": "mock",
                "config": {"embedding_dims": 16},
            },
        }
    )


async def _stored_memory_text_async(memory: AsyncMemory, user_id: str) -> str:
    result = await memory.get_all(user_id=user_id)
    return "\n".join(item["memory"] for item in result["results"])


def test_public_import_contract():
    assert callable(PowerMemMiddleware)


def test_retrieves_powermem_memories_before_model_call(tmp_path: Path):
    memory = _sqlite_memory(tmp_path)
    memory.add("User prefers short database answers.", user_id="alice", infer=False)
    memory.add("User works on storage engines.", user_id="alice", infer=False)
    model = CapturingChatModel(responses=["done"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="alice",
                search_limit=2,
                save_interactions=False,
            )
        ],
    )

    agent.invoke(
        {"messages": [HumanMessage(content="How should you answer database questions?")]}
    )

    prompt_text = _message_text(model.calls[0])
    assert "User prefers short database answers." in prompt_text
    assert "User works on storage engines." in prompt_text


def test_persists_interaction_after_agent_run(tmp_path: Path):
    memory = _sqlite_memory(tmp_path)
    model = CapturingChatModel(responses=["Stored response"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="alice",
                save_interactions=True,
            )
        ],
    )

    agent.invoke({"messages": [HumanMessage(content="Remember this preference.")]})

    memory_text = _stored_memory_text(memory, "alice")
    assert "Remember this preference." in memory_text
    assert "Stored response" in memory_text


def test_can_disable_interaction_persistence(tmp_path: Path):
    memory = _sqlite_memory(tmp_path)
    model = CapturingChatModel(responses=["Do not persist this"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="alice",
                save_interactions=False,
            )
        ],
    )

    agent.invoke({"messages": [HumanMessage(content="This should stay transient.")]})

    assert memory.get_all(user_id="alice")["results"] == []


def test_search_failure_is_fail_open_by_default():
    memory = FailingSearchMemory()
    model = CapturingChatModel(responses=["Agent still runs"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="alice",
                save_interactions=False,
            )
        ],
    )

    result = agent.invoke({"messages": [HumanMessage(content="Hello")]})

    assert result["messages"][-1].content == "Agent still runs"


def test_writeback_failure_is_fail_open(tmp_path: Path):
    memory = FailingAddMemory()
    model = CapturingChatModel(responses=["Agent still runs"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="alice",
                save_interactions=True,
            )
        ],
    )

    result = agent.invoke({"messages": [HumanMessage(content="Hello")]})

    assert result["messages"][-1].content == "Agent still runs"


@pytest.mark.asyncio
async def test_async_memory_retrieves_memories_before_model_call(tmp_path: Path):
    """An AsyncMemory is awaited in the async agent path and injects memories."""
    memory = _async_sqlite_memory(tmp_path)
    await memory.add("User prefers short database answers.", user_id="alice", infer=False)
    await memory.add("User works on storage engines.", user_id="alice", infer=False)
    model = CapturingChatModel(responses=["done"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="alice",
                search_limit=2,
                save_interactions=False,
            )
        ],
    )

    await agent.ainvoke(
        {"messages": [HumanMessage(content="How should you answer database questions?")]}
    )

    prompt_text = _message_text(model.calls[0])
    assert "User prefers short database answers." in prompt_text
    assert "User works on storage engines." in prompt_text


@pytest.mark.asyncio
async def test_async_memory_persists_interaction_after_run(tmp_path: Path):
    """An AsyncMemory interaction is awaited back into PowerMem after the run."""
    memory = _async_sqlite_memory(tmp_path)
    model = CapturingChatModel(responses=["Stored response"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="alice",
                save_interactions=True,
            )
        ],
    )

    await agent.ainvoke({"messages": [HumanMessage(content="Remember this preference.")]})

    memory_text = await _stored_memory_text_async(memory, "alice")
    assert "Remember this preference." in memory_text
    assert "Stored response" in memory_text


@pytest.mark.asyncio
async def test_async_memory_search_failure_is_fail_open():
    """An async memory whose search rejects must not break the agent."""
    memory = FailingAsyncSearchMemory()
    model = CapturingChatModel(responses=["Agent still runs"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="alice",
                save_interactions=False,
            )
        ],
    )

    result = await agent.ainvoke({"messages": [HumanMessage(content="Hello")]})

    assert result["messages"][-1].content == "Agent still runs"


@pytest.mark.asyncio
async def test_async_memory_writeback_failure_is_fail_open():
    """An async memory whose add rejects must not break an otherwise-complete run."""
    memory = FailingAsyncAddMemory()
    model = CapturingChatModel(responses=["Agent still runs"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="alice",
                save_interactions=True,
            )
        ],
    )

    result = await agent.ainvoke({"messages": [HumanMessage(content="Hello")]})

    assert result["messages"][-1].content == "Agent still runs"


def test_sync_agent_rejects_async_memory(tmp_path: Path):
    """Passing an AsyncMemory to a sync agent fails loudly instead of leaking a coroutine."""
    memory = _async_sqlite_memory(tmp_path)
    model = CapturingChatModel(responses=["ok"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="alice",
                save_interactions=False,
            )
        ],
    )

    with pytest.raises(TypeError):
        agent.invoke({"messages": [HumanMessage(content="Hello")]})


@pytest.mark.asyncio
async def test_async_agent_uses_powermem_memory(tmp_path: Path):
    memory = _sqlite_memory(tmp_path)
    memory.add("User prefers async examples.", user_id="async-user", infer=False)
    model = CapturingChatModel(responses=["ok"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="async-user",
                search_limit=1,
                save_interactions=False,
            )
        ],
    )

    await agent.ainvoke({"messages": [HumanMessage(content="Use my async profile.")]})

    assert "User prefers async examples." in _message_text(model.calls[0])


# ---------------------------------------------------------------------- #
# Constructor validation (fail fast on misconfiguration).
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "kwargs",
    [
        {"user_id": ""},
        {"user_id": 123},
        {"search_limit": 0},
        {"search_limit": -3},
        {"search_limit": 1.5},
    ],
    ids=["empty-user_id", "non-str-user_id", "zero-limit", "negative-limit", "non-int-limit"],
)
def test_constructor_rejects_invalid_arguments(kwargs: dict[str, Any]) -> None:
    """user_id must be a non-empty string (or None) and search_limit a positive int."""
    with pytest.raises(ValueError):
        PowerMemMiddleware(memory=object(), **kwargs)


def test_constructor_allows_none_user_id() -> None:
    """None is the documented default; identity is deferred to PowerMem."""
    middleware = PowerMemMiddleware(memory=object(), user_id=None)
    assert middleware.user_id is None


# ---------------------------------------------------------------------- #
# Exact argument propagation (RecordingMemory).
# ---------------------------------------------------------------------- #


def test_search_uses_latest_user_message_explicit_user_id_and_limit() -> None:
    memory = RecordingMemory(
        search_result={"results": [{"memory": "prefers concise answers"}]}
    )
    model = CapturingChatModel(responses=["ok"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="explicit-user",
                search_limit=3,
                save_interactions=False,
            )
        ],
    )

    agent.invoke({"messages": [HumanMessage(content="Newest question")]})

    assert memory.search_calls == [
        {"args": (), "kwargs": {"query": "Newest question", "user_id": "explicit-user", "limit": 3}}
    ]


def test_writeback_calls_add_with_explicit_user_id_and_infer_false() -> None:
    memory = RecordingMemory()
    model = CapturingChatModel(responses=["Assistant reply"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="explicit-user",
                save_interactions=True,
            )
        ],
    )

    agent.invoke({"messages": [HumanMessage(content="Human line")]})

    assert len(memory.add_calls) == 1
    call = memory.add_calls[0]
    text = call["args"][0]
    assert text == "User: Human line\nAssistant: Assistant reply"
    assert call["kwargs"] == {"user_id": "explicit-user", "infer": False}


# ---------------------------------------------------------------------- #
# Boundary behavior: system-prompt preservation and malformed/dup results.
# ---------------------------------------------------------------------- #


def test_injection_preserves_existing_system_prompt() -> None:
    """Injected memories append to the system prompt, they do not replace it."""
    memory = RecordingMemory(
        search_result={"results": [{"memory": "prefers concise answers"}]}
    )
    model = CapturingChatModel(responses=["ok"])
    agent = create_agent(
        model=model,
        tools=[],
        system_prompt="BASE INSTRUCTION",
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="alice",
                save_interactions=False,
            )
        ],
    )

    agent.invoke({"messages": [HumanMessage(content="How should you answer?")]})

    prompt_text = _message_text(model.calls[0])
    assert "BASE INSTRUCTION" in prompt_text
    assert "prefers concise answers" in prompt_text


# ---------------------------------------------------------------------- #
# fail_open=False: memory-layer errors propagate instead of being swallowed.
# ---------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("memory", "save_interactions"),
    [
        (FailingSearchMemory(), False),
        (FailingAddMemory(), True),
    ],
    ids=["search-error", "writeback-error"],
)
def test_fail_open_disabled_propagates_sync_error(
    memory: Any, save_interactions: bool
) -> None:
    model = CapturingChatModel(responses=["ok"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="alice",
                save_interactions=save_interactions,
                fail_open=False,
            )
        ],
    )

    with pytest.raises(RuntimeError):
        agent.invoke({"messages": [HumanMessage(content="Hello")]})


@pytest.mark.asyncio
async def test_fail_open_disabled_propagates_async_search_error() -> None:
    memory = FailingAsyncSearchMemory()
    model = CapturingChatModel(responses=["ok"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="alice",
                save_interactions=False,
                fail_open=False,
            )
        ],
    )

    with pytest.raises(RuntimeError):
        await agent.ainvoke({"messages": [HumanMessage(content="Hello")]})


# ---------------------------------------------------------------------- #
# Checkpointer safety: a previous run's context must not leak into a later
# run on the same thread.
# ---------------------------------------------------------------------- #


def test_context_does_not_leak_across_runs_with_checkpointer() -> None:
    """A checkpointed agent must not replay a previous run's memory context into
    a later, memory-less run on the same thread."""
    pytest.importorskip("langgraph.checkpoint.memory")
    from langgraph.checkpoint.memory import MemorySaver

    class OnDemandMemory:
        """Returns a memory only for the ``seed`` query; empty otherwise."""

        def search(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            if kwargs.get("query") == "seed":
                return {"results": [{"memory": "SEEDED MEMORY"}]}
            return {"results": []}

        def add(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
            return {"results": []}

    memory = OnDemandMemory()
    model = CapturingChatModel(responses=["ok"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="alice",
                save_interactions=False,
            )
        ],
        checkpointer=MemorySaver(),
    )
    config: dict[str, Any] = {"configurable": {"thread_id": "leak-probe"}}

    agent.invoke({"messages": [HumanMessage(content="seed")]}, config=config)
    assert "SEEDED MEMORY" in _message_text(model.calls[0])

    model.calls.clear()
    agent.invoke({"messages": [HumanMessage(content="plain")]}, config=config)
    assert "SEEDED MEMORY" not in _message_text(model.calls[0])
