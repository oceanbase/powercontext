from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import SimpleChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
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


_DEFAULT_SEARCH_RESULT = object()


class RecordingMemory:
    def __init__(
        self,
        search_result: Any = _DEFAULT_SEARCH_RESULT,
        *,
        search_error: Exception | None = None,
        add_error: Exception | None = None,
    ) -> None:
        self.search_result = (
            {"results": []}
            if search_result is _DEFAULT_SEARCH_RESULT
            else search_result
        )
        self.search_error = search_error
        self.add_error = add_error
        self.search_calls: list[tuple[Any, str, int]] = []
        self.add_calls: list[tuple[Any, str, bool]] = []

    def search(self, query: Any, *, user_id: str, limit: int) -> Any:
        self.search_calls.append((query, user_id, limit))
        if self.search_error is not None:
            raise self.search_error
        return self.search_result

    def add(self, messages: Any, *, user_id: str, infer: bool) -> Any:
        self.add_calls.append((messages, user_id, infer))
        if self.add_error is not None:
            raise self.add_error
        return {"results": []}


class AsyncRecordingMemory(RecordingMemory):
    async def search(self, query: Any, *, user_id: str, limit: int) -> Any:
        self.search_calls.append((query, user_id, limit))
        if self.search_error is not None:
            raise self.search_error
        return self.search_result

    async def add(self, messages: Any, *, user_id: str, infer: bool) -> Any:
        self.add_calls.append((messages, user_id, infer))
        if self.add_error is not None:
            raise self.add_error
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


def test_search_failure_is_fail_open_by_default(caplog: pytest.LogCaptureFixture):
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
    assert any(record.levelname == "WARNING" for record in caplog.records)


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


def test_searches_only_the_latest_nonempty_human_message():
    memory = RecordingMemory()
    model = CapturingChatModel(responses=["ok"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[
            PowerMemMiddleware(
                memory=memory,
                user_id="alice",
                search_limit=3,
                save_interactions=False,
            )
        ],
    )

    agent.invoke(
        {
            "messages": [
                HumanMessage(content="older question"),
                AIMessage(content="older answer"),
                HumanMessage(content="latest question"),
                HumanMessage(content="   "),
            ]
        }
    )

    assert memory.search_calls == [("latest question", "alice", 3)]
    assert memory.add_calls == []


def test_preserves_system_blocks_and_keeps_memory_context_transient():
    memory = RecordingMemory(
        {"results": [{"memory": "User prefers concise explanations."}]}
    )
    model = CapturingChatModel(responses=["A concise answer"])
    original_block = {"type": "text", "text": "Follow the application policy."}
    agent = create_agent(
        model=model,
        tools=[],
        system_prompt=SystemMessage(content=[original_block]),
        middleware=[PowerMemMiddleware(memory=memory, user_id="alice")],
    )

    result = agent.invoke(
        {"messages": [HumanMessage(content="How should this be explained?")]}
    )

    system_messages = [
        message for message in model.calls[0] if isinstance(message, SystemMessage)
    ]
    assert len(system_messages) == 1
    assert isinstance(system_messages[0].content, list)
    assert original_block in system_messages[0].content
    assert "User prefers concise explanations." in _message_text(model.calls[0])
    assert "User prefers concise explanations." not in _message_text(result["messages"])
    assert memory.add_calls == [
        (
            [
                {"role": "user", "content": "How should this be explained?"},
                {"role": "assistant", "content": "A concise answer"},
            ],
            "alice",
            True,
        )
    ]


def test_persists_only_latest_turn_and_final_ai_message():
    memory = RecordingMemory()
    model = CapturingChatModel(responses=["final answer"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[PowerMemMiddleware(memory=memory, user_id="alice")],
    )

    agent.invoke(
        {
            "messages": [
                HumanMessage(content="old question"),
                AIMessage(content="old answer"),
                HumanMessage(content="current question"),
            ]
        }
    )

    assert memory.add_calls == [
        (
            [
                {"role": "user", "content": "current question"},
                {"role": "assistant", "content": "final answer"},
            ],
            "alice",
            True,
        )
    ]


@pytest.mark.parametrize("user_id", [None, "", "   "])
def test_requires_nonempty_user_id(user_id: str | None):
    with pytest.raises(ValueError):
        PowerMemMiddleware(memory=RecordingMemory(), user_id=user_id)


@pytest.mark.parametrize("search_limit", [0, -1, True, 1.5, "5"])
def test_requires_positive_integer_search_limit(search_limit: Any):
    with pytest.raises((TypeError, ValueError)):
        PowerMemMiddleware(
            memory=RecordingMemory(),
            user_id="alice",
            search_limit=search_limit,
        )


def test_rejects_unknown_constructor_arguments():
    with pytest.raises(TypeError):
        PowerMemMiddleware(
            memory=RecordingMemory(),
            user_id="alice",
            unsupported_option=True,
        )


def test_extracts_content_fallback_and_ignores_malformed_search_results():
    memory = RecordingMemory(
        {
            "results": [
                {"memory": "primary memory"},
                {"content": "fallback memory"},
                {"memory": "   "},
                {"content": 42},
                {},
                None,
                "invalid",
            ]
        }
    )
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

    agent.invoke({"messages": [HumanMessage(content="question")]})

    prompt = _message_text(model.calls[0])
    assert "primary memory" in prompt
    assert "fallback memory" in prompt
    assert "invalid" not in prompt


@pytest.mark.parametrize(
    "search_result",
    [None, {}, {"results": None}, {"results": []}, {"results": "invalid"}],
)
def test_empty_search_results_do_not_add_a_system_message(search_result: Any):
    memory = RecordingMemory(search_result)
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

    result = agent.invoke({"messages": [HumanMessage(content="question")]})

    assert result["messages"][-1].content == "ok"
    assert not any(isinstance(message, SystemMessage) for message in model.calls[0])


def test_add_failure_is_fail_open(caplog: pytest.LogCaptureFixture):
    memory = RecordingMemory(add_error=RuntimeError("add failed"))
    model = CapturingChatModel(responses=["answer survives"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[PowerMemMiddleware(memory=memory, user_id="alice")],
    )

    result = agent.invoke({"messages": [HumanMessage(content="question")]})

    assert result["messages"][-1].content == "answer survives"
    assert len(memory.add_calls) == 1
    assert any(record.levelname == "WARNING" for record in caplog.records)


@pytest.mark.asyncio
async def test_async_agent_awaits_async_memory_search_and_writeback():
    memory = AsyncRecordingMemory({"results": [{"memory": "async context"}]})
    model = CapturingChatModel(responses=["async answer"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[PowerMemMiddleware(memory=memory, user_id="async-user")],
    )

    result = await agent.ainvoke({"messages": [HumanMessage(content="async question")]})

    assert result["messages"][-1].content == "async answer"
    assert memory.search_calls == [("async question", "async-user", 5)]
    assert memory.add_calls == [
        (
            [
                {"role": "user", "content": "async question"},
                {"role": "assistant", "content": "async answer"},
            ],
            "async-user",
            True,
        )
    ]
    assert "async context" in _message_text(model.calls[0])


@pytest.mark.asyncio
async def test_async_memory_failures_are_fail_open(caplog: pytest.LogCaptureFixture):
    memory = AsyncRecordingMemory(
        search_error=RuntimeError("async search failed"),
        add_error=RuntimeError("async add failed"),
    )
    model = CapturingChatModel(responses=["answer survives"])
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[PowerMemMiddleware(memory=memory, user_id="async-user")],
    )

    result = await agent.ainvoke({"messages": [HumanMessage(content="async question")]})

    assert result["messages"][-1].content == "answer survives"
    assert len(memory.search_calls) == 1
    assert len(memory.add_calls) == 1
    assert sum(record.levelname == "WARNING" for record in caplog.records) >= 2
