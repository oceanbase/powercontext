"""LangChain middleware entry point for PowerMem."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, NotRequired, TypedDict, cast

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage


logger = logging.getLogger(__name__)


class PowerMemState(AgentState):
    """State schema reserved for the PowerMem middleware implementation."""

    powermem_context: NotRequired[str]


class PowerMemStateUpdate(TypedDict):
    """State update returned by memory-loading middleware hooks."""

    powermem_context: str


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """Use PowerMem as a long-term memory layer for a LangChain agent."""

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

    @staticmethod
    def _latest_message_text(
        messages: list[BaseMessage],
        message_type: type[BaseMessage],
    ) -> str | None:
        for message in reversed(messages):
            if isinstance(message, message_type) and message.text:
                return message.text
        return None

    @staticmethod
    def _format_search_results(result: Any) -> str | None:
        if not isinstance(result, dict):
            return None

        memories: list[str] = []
        for item in result.get("results", []):
            if isinstance(item, str):
                content = item
            elif isinstance(item, dict):
                content = item.get("memory") or item.get("content")
            else:
                continue

            if content:
                memories.append(str(content))

        if not memories:
            return None
        return "\n".join(f"- {memory}" for memory in memories)

    def _load_memory(self, state: PowerMemState) -> PowerMemStateUpdate | None:
        query = self._latest_message_text(state["messages"], HumanMessage)
        if not query:
            return None

        try:
            result = self.memory.search(
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
        except Exception:
            logger.warning(
                "PowerMem search failed; continuing without memory",
                exc_info=True,
            )
            return None

        context = self._format_search_results(result)
        if context is None:
            return None
        return {"powermem_context": context}

    @staticmethod
    def _inject_memory(request: ModelRequest[Any]) -> ModelRequest[Any]:
        state = request.state or {}
        context = state.get("powermem_context")
        if not context:
            return request

        memory_prompt = f"Relevant long-term memories:\n{context}"
        if request.system_message is None:
            system_message = SystemMessage(content=memory_prompt)
        else:
            content = [
                *request.system_message.content_blocks,
                {"type": "text", "text": f"\n\n{memory_prompt}"},
            ]
            system_message = SystemMessage(
                content=cast("list[str | dict[str, str]]", content)
            )

        return request.override(system_message=system_message)

    def _save_interaction(self, state: PowerMemState) -> None:
        messages = state["messages"]
        user_message = self._latest_message_text(messages, HumanMessage)
        assistant_message = self._latest_message_text(messages, AIMessage)
        if not user_message or not assistant_message:
            return

        self.memory.add(
            [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": assistant_message},
            ],
            user_id=self.user_id,
        )

    def before_agent(self, state: PowerMemState, runtime) -> PowerMemStateUpdate | None:
        return self._load_memory(state)

    async def abefore_agent(
        self,
        state: PowerMemState,
        runtime,
    ) -> PowerMemStateUpdate | None:
        return await asyncio.to_thread(self._load_memory, state)

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(self._inject_memory(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(self._inject_memory(request))

    def after_agent(self, state: PowerMemState, runtime) -> None:
        if self.save_interactions:
            self._save_interaction(state)

    async def aafter_agent(self, state: PowerMemState, runtime) -> None:
        if self.save_interactions:
            await asyncio.to_thread(self._save_interaction, state)
