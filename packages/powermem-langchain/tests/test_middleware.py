from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from powermem import Memory
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


class RecordingMemory:
    def __init__(self, memories: list[str] | None = None) -> None:
        self.memories = memories or []
        self.search_calls: list[dict[str, Any]] = []
        self.add_calls: list[dict[str, Any]] = []

    def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.search_calls.append({"query": query, **kwargs})
        return {"results": [{"memory": item} for item in self.memories]}

    def add(self, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        self.add_calls.append({"messages": messages, **kwargs})
        return {"results": []}


class AsyncRecordingMemory(RecordingMemory):
    async def search(self, query: str, **kwargs: Any) -> dict[str, Any]:
        self.search_calls.append({"query": query, **kwargs})
        return {"results": [{"memory": item} for item in self.memories]}

    async def add(
        self, messages: list[dict[str, str]], **kwargs: Any
    ) -> dict[str, Any]:
        self.add_calls.append({"messages": messages, **kwargs})
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
        {
            "messages": [
                HumanMessage(content="How should you answer database questions?")
            ]
        }
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


def test_uses_latest_user_message_and_preserves_system_prompt():
    memory = RecordingMemory(["User is working on query optimization."])
    model = CapturingChatModel(responses=["Final answer"])
    agent = create_agent(
        model=model,
        tools=[],
        system_prompt="Keep the original system instruction.",
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="explicit-user",
                search_limit=3,
            )
        ],
    )

    result = agent.invoke(
        {
            "messages": [
                HumanMessage(content="An earlier question"),
                AIMessage(content="An earlier answer"),
                HumanMessage(content="The current question"),
            ]
        }
    )

    assert memory.search_calls == [
        {
            "query": "The current question",
            "user_id": "explicit-user",
            "limit": 3,
        }
    ]
    assert memory.add_calls == [
        {
            "messages": [
                {"role": "user", "content": "The current question"},
                {"role": "assistant", "content": "Final answer"},
            ],
            "user_id": "explicit-user",
        }
    ]
    prompt_text = _message_text(model.calls[0])
    assert "Keep the original system instruction." in prompt_text
    assert "User is working on query optimization." in prompt_text
    assert "powermem_context" not in result


def test_search_failure_clears_previous_powermem_context():
    middleware = PowerMemMiddleware(
        memory=FailingSearchMemory(),
        user_id="alice",
        save_interactions=False,
    )

    update = middleware.before_agent(
        {
            "messages": [HumanMessage(content="A new request")],
            "powermem_context": "stale memory context",
        },
        runtime=None,
    )

    assert update == {"powermem_context": ""}


@pytest.mark.asyncio
async def test_async_agent_supports_async_memory_and_writes_interaction():
    memory = AsyncRecordingMemory(["User prefers non-blocking APIs."])
    model = CapturingChatModel(responses=["Async response"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[PowerMemMiddleware(memory=memory, user_id="async-user")],
    )

    await agent.ainvoke(
        {"messages": [HumanMessage(content="Show me the async version.")]}
    )

    assert "User prefers non-blocking APIs." in _message_text(model.calls[0])
    assert memory.search_calls == [
        {
            "query": "Show me the async version.",
            "user_id": "async-user",
            "limit": 5,
        }
    ]
    assert memory.add_calls == [
        {
            "messages": [
                {"role": "user", "content": "Show me the async version."},
                {"role": "assistant", "content": "Async response"},
            ],
            "user_id": "async-user",
        }
    ]


@pytest.mark.parametrize(
    ("kwargs", "error_type"),
    [
        ({"user_id": ""}, ValueError),
        ({"user_id": "alice", "search_limit": 0}, ValueError),
        ({"user_id": "alice", "search_limit": True}, TypeError),
    ],
)
def test_validates_constructor_arguments(
    kwargs: dict[str, Any], error_type: type[Exception]
):
    with pytest.raises(error_type):
        PowerMemMiddleware(memory=RecordingMemory(), **kwargs)
