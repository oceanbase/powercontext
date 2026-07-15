"""LangChain middleware for loading and storing PowerMem memories."""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, NotRequired, TypedDict

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


class PowerMemState(AgentState):
    """State schema used by the PowerMem middleware."""

    powermem_context: NotRequired[str]


class PowerMemStateUpdate(TypedDict):
    """State update returned by memory-loading middleware hooks."""

    powermem_context: str
    messages: NotRequired[list]


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """Add relevant user memories to an agent prompt and persist its reply."""

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
        self.memory = memory
        self.user_id = user_id
        self.search_limit = search_limit
        self.save_interactions = save_interactions

        if not hasattr(memory, "search"):
            raise TypeError("memory must provide a search method")
        if search_limit < 1:
            raise ValueError("search_limit must be positive")

    def before_agent(self, state: PowerMemState, runtime) -> PowerMemStateUpdate | None:
        query = self._latest_human_text(state)
        if self.user_id is None or not query:
            return None
        try:
            result = self.memory.search(query, user_id=self.user_id, limit=self.search_limit)
        except Exception:
            return None
        return self._memory_update(result)

    async def abefore_agent(
        self,
        state: PowerMemState,
        runtime,
    ) -> PowerMemStateUpdate | None:
        query = self._latest_human_text(state)
        if self.user_id is None or not query:
            return None
        try:
            search = getattr(self.memory, "asearch", None)
            if callable(search):
                result = search(query, user_id=self.user_id, limit=self.search_limit)
            elif inspect.iscoroutinefunction(self.memory.search):
                result = self.memory.search(
                    query, user_id=self.user_id, limit=self.search_limit
                )
            else:
                result = await asyncio.to_thread(
                    self.memory.search,
                    query,
                    user_id=self.user_id,
                    limit=self.search_limit,
                )
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            return None
        return self._memory_update(result)

    def after_agent(self, state: PowerMemState, runtime) -> None:
        if not self.save_interactions or self.user_id is None:
            return
        messages = self._interaction_messages(state)
        if not messages:
            return
        try:
            self.memory.add(messages=messages, user_id=self.user_id)
        except Exception:
            return

    async def aafter_agent(self, state: PowerMemState, runtime) -> None:
        if not self.save_interactions or self.user_id is None:
            return
        messages = self._interaction_messages(state)
        if not messages:
            return
        try:
            if inspect.iscoroutinefunction(self.memory.add):
                result = self.memory.add(messages=messages, user_id=self.user_id)
            else:
                result = await asyncio.to_thread(
                    self.memory.add, messages=messages, user_id=self.user_id
                )
            if inspect.isawaitable(result):
                await result
        except Exception:
            return

    @classmethod
    def _memory_update(cls, result: Any) -> PowerMemStateUpdate | None:
        context = cls._context_from_search(result)
        if not context:
            return None
        return {
            "powermem_context": context,
            "messages": [SystemMessage(content=context)],
        }

    @staticmethod
    def _latest_human_text(state: PowerMemState) -> str:
        for message in reversed(state.get("messages", [])):
            if isinstance(message, HumanMessage):
                return str(message.content)
        return ""

    @staticmethod
    def _context_from_search(result: Any) -> str:
        if not isinstance(result, dict):
            return ""
        memories = result.get("results", [])
        if not isinstance(memories, list):
            return ""
        texts = [
            str(item["memory"])
            for item in memories
            if isinstance(item, dict) and item.get("memory")
        ]
        if not texts:
            return ""
        return "Relevant memories from PowerMem:\n" + "\n".join(f"- {text}" for text in texts)

    @staticmethod
    def _interaction_messages(state: PowerMemState) -> list[dict[str, str]]:
        human = next(
            (
                message
                for message in reversed(state.get("messages", []))
                if isinstance(message, HumanMessage)
            ),
            None,
        )
        assistant = next(
            (message for message in reversed(state.get("messages", [])) if isinstance(message, AIMessage)),
            None,
        )
        if human is None or assistant is None:
            return []
        return [
            {"role": "user", "content": str(human.content)},
            {"role": "assistant", "content": str(assistant.content)},
        ]
