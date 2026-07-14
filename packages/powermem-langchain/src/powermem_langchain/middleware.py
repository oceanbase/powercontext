"""LangChain middleware that provides PowerMem-backed long-term memory."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, NotRequired, TypedDict

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage


logger = logging.getLogger(__name__)


class PowerMemState(AgentState):
    """Agent state extended with formatted long-term-memory context."""

    powermem_context: NotRequired[str]


class PowerMemStateUpdate(TypedDict):
    """State update returned after loading memories."""

    powermem_context: str


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """Retrieve PowerMem context and persist completed agent interactions."""

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
        if memory is None:
            raise ValueError("memory must not be None")
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id must be a non-empty string")
        if not isinstance(search_limit, int) or isinstance(search_limit, bool) or search_limit <= 0:
            raise ValueError("search_limit must be a positive integer")
        if kwargs:
            names = ", ".join(sorted(kwargs))
            raise TypeError(f"unexpected middleware arguments: {names}")

        self.memory = memory
        self.user_id = user_id
        self.search_limit = search_limit
        self.save_interactions = save_interactions

    @staticmethod
    def _message_text(message: BaseMessage) -> str:
        return message.text.strip()

    @classmethod
    def _latest_message(cls, state: PowerMemState, message_type: type[BaseMessage]) -> str | None:
        for message in reversed(state.get("messages", [])):
            if isinstance(message, message_type):
                text = cls._message_text(message)
                if text:
                    return text
        return None

    @staticmethod
    def _format_memories(result: Any) -> str:
        if not isinstance(result, dict):
            return ""
        memories: list[str] = []
        for item in result.get("results", []):
            if isinstance(item, dict):
                value = item.get("memory") or item.get("content")
                if value is not None and str(value).strip():
                    memories.append(str(value).strip())
        if not memories:
            return ""
        body = "\n".join(f"- {memory}" for memory in memories)
        return f"Relevant long-term memories for this user:\n{body}"

    def _load_context(self, state: PowerMemState) -> str:
        query = self._latest_message(state, HumanMessage)
        if not query:
            return ""
        try:
            result = self.memory.search(
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
        except Exception:
            logger.warning("PowerMem search failed; continuing without memory", exc_info=True)
            return ""
        return self._format_memories(result)

    def before_agent(self, state: PowerMemState, runtime: Any) -> PowerMemStateUpdate:
        return {"powermem_context": self._load_context(state)}

    async def abefore_agent(
        self,
        state: PowerMemState,
        runtime: Any,
    ) -> PowerMemStateUpdate:
        context = await asyncio.to_thread(self._load_context, state)
        return {"powermem_context": context}

    @staticmethod
    def _request_with_context(request: ModelRequest[Any]) -> ModelRequest[Any]:
        context = request.state.get("powermem_context", "")
        if not context:
            return request
        return request.override(messages=[SystemMessage(content=context), *request.messages])

    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> ModelResponse:
        return handler(self._request_with_context(request))

    async def awrap_model_call(self, request: ModelRequest[Any], handler: Any) -> ModelResponse:
        return await handler(self._request_with_context(request))

    def _save_interaction(self, state: PowerMemState) -> None:
        if not self.save_interactions:
            return
        user_text = self._latest_message(state, HumanMessage)
        assistant_text = self._latest_message(state, AIMessage)
        if not user_text or not assistant_text:
            return
        try:
            self.memory.add(
                [
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": assistant_text},
                ],
                user_id=self.user_id,
                infer=False,
            )
        except Exception:
            logger.warning("PowerMem write-back failed; returning agent result", exc_info=True)

    def after_agent(self, state: PowerMemState, runtime: Any) -> None:
        self._save_interaction(state)

    async def aafter_agent(self, state: PowerMemState, runtime: Any) -> None:
        await asyncio.to_thread(self._save_interaction, state)
