from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.messages import BaseMessage, HumanMessage
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


@pytest.mark.asyncio
async def test_async_agent_persists_interaction(tmp_path: Path):
    memory = _sqlite_memory(tmp_path)
    model = CapturingChatModel(responses=["Async stored response"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="async-user",
                save_interactions=True,
            )
        ],
    )

    await agent.ainvoke(
        {"messages": [HumanMessage(content="Remember this async note.")]}
    )

    memory_text = _stored_memory_text(memory, "async-user")
    assert "Remember this async note." in memory_text
    assert "Async stored response" in memory_text
