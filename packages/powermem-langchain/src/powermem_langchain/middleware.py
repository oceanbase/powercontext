"""LangChain middleware entry point for PowerMem."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable
from typing import Any, NotRequired, TypedDict, TypeVar

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage


T = TypeVar("T")


class PowerMemState(AgentState):
    """State schema reserved for the PowerMem middleware implementation."""

    powermem_context: NotRequired[str]


class PowerMemStateUpdate(TypedDict):
    """State update returned by memory-loading middleware hooks."""

    powermem_context: str
    messages: NotRequired[list[BaseMessage]]


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """Inject PowerMem long-term memories into LangChain agent model calls."""

    state_schema = PowerMemState

    def __init__(
        self,
        *,
        memory: Any,
        user_id: str | None = None,
        search_limit: int = 5,
        save_interactions: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if user_id is None or not user_id.strip():
            raise ValueError("PowerMemMiddleware requires an explicit user_id")
        if search_limit < 1:
            raise ValueError("search_limit must be at least 1")

        self.memory = memory
        self.user_id = user_id
        self.search_limit = search_limit
        self.save_interactions = save_interactions

    def before_agent(self, state: PowerMemState, runtime) -> PowerMemStateUpdate | None:
        return None

    async def abefore_agent(
        self,
        state: PowerMemState,
        runtime,
    ) -> PowerMemStateUpdate | None:
        return None

    def before_model(self, state: PowerMemState, runtime) -> PowerMemStateUpdate | None:
        query = _latest_message_text(state.get("messages", []), HumanMessage)
        if not query:
            return None

        memories = self._search_memories(query)
        context = _format_memory_context(memories)
        if not context:
            return None

        return {
            "powermem_context": context,
            "messages": [SystemMessage(content=context)],
        }

    async def abefore_model(
        self,
        state: PowerMemState,
        runtime,
    ) -> PowerMemStateUpdate | None:
        query = _latest_message_text(state.get("messages", []), HumanMessage)
        if not query:
            return None

        memories = await _maybe_await(self._asearch_memories(query))
        context = _format_memory_context(memories)
        if not context:
            return None

        return {
            "powermem_context": context,
            "messages": [SystemMessage(content=context)],
        }

    def after_agent(self, state: PowerMemState, runtime) -> None:
        if not self.save_interactions:
            return None

        interaction = _interaction_text(state.get("messages", []))
        if not interaction:
            return None

        self._save_interaction(interaction)
        return None

    async def aafter_agent(self, state: PowerMemState, runtime) -> None:
        if not self.save_interactions:
            return None

        interaction = _interaction_text(state.get("messages", []))
        if not interaction:
            return None

        await _maybe_await(self._asave_interaction(interaction))
        return None

    def _search_memories(self, query: str) -> list[str]:
        try:
            result = self.memory.search(
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
        except Exception:
            return []
        return _extract_memory_texts(result, self.search_limit)

    async def _asearch_memories(self, query: str) -> list[str]:
        try:
            result = self.memory.search(
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
            result = await _maybe_await(result)
        except Exception:
            return []
        return _extract_memory_texts(result, self.search_limit)

    def _save_interaction(self, interaction: str) -> None:
        self.memory.add(interaction, user_id=self.user_id, infer=False)

    async def _asave_interaction(self, interaction: str) -> None:
        result = self.memory.add(interaction, user_id=self.user_id, infer=False)
        await _maybe_await(result)


def _latest_message_text(
    messages: list[BaseMessage],
    message_type: type[BaseMessage],
) -> str:
    for message in reversed(messages):
        if isinstance(message, message_type):
            return _content_to_text(message.content)
    return ""


def _interaction_text(messages: list[BaseMessage]) -> str:
    user_text = _latest_message_text(messages, HumanMessage)
    assistant_text = _latest_message_text(messages, AIMessage)
    if not user_text or not assistant_text:
        return ""
    return f"User: {user_text}\nAssistant: {assistant_text}"


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                value = item.get("text") or item.get("content")
                if value is not None:
                    parts.append(str(value))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part).strip()
    return str(content).strip() if content is not None else ""


def _extract_memory_texts(result: Any, limit: int) -> list[str]:
    if isinstance(result, dict):
        results = result.get("results", [])
    elif isinstance(result, list):
        results = result
    else:
        results = []

    memories: list[str] = []
    for item in results:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            text = item.get("memory") or item.get("content") or item.get("text") or ""
        else:
            text = str(item)

        text = text.strip()
        if text:
            memories.append(text)
        if len(memories) >= limit:
            break
    return memories


def _format_memory_context(memories: list[str]) -> str:
    if not memories:
        return ""
    bullets = "\n".join(f"- {memory}" for memory in memories)
    return f"Relevant long-term memories for this user:\n{bullets}"


async def _maybe_await(value: T | Awaitable[T]) -> T:
    if inspect.isawaitable(value):
        return await value
    return value
