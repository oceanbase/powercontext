"""LangChain middleware integration for PowerMem long-term memory."""

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
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

logger = logging.getLogger(__name__)

_MEMORY_HEADER = "Relevant long-term memories from PowerMem:"


class PowerMemState(AgentState):
    """Agent state extended with retrieved PowerMem context."""

    powermem_context: NotRequired[str]


class PowerMemStateUpdate(TypedDict):
    """State update returned by memory-loading middleware hooks."""

    powermem_context: str


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """Use PowerMem as a long-term memory layer for LangChain agents.

    The middleware searches memory once at the start of an agent invocation,
    injects the retrieved context into each model request, and optionally saves
    the final user/assistant interaction after the run completes.
    """

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
        del kwargs
        self.memory = memory
        self.user_id = user_id
        self.search_limit = max(1, int(search_limit))
        self.save_interactions = bool(save_interactions)

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "\n".join(part for part in parts if part)
        return str(content) if content is not None else ""

    @classmethod
    def _latest_message_text(cls, state: PowerMemState, role: str) -> str:
        messages = state.get("messages", [])
        for message in reversed(messages):
            if role == "user" and isinstance(message, HumanMessage):
                return cls._content_text(message.content)
            if role == "assistant" and isinstance(message, AIMessage):
                return cls._content_text(message.content)
            if isinstance(message, dict):
                message_role = message.get("role")
                if message_role == role:
                    return cls._content_text(message.get("content"))
        return ""

    @classmethod
    def _format_search_result(cls, result: Any) -> str:
        if not isinstance(result, dict):
            return ""
        memories: list[str] = []
        for item in result.get("results", [])[:]:
            if not isinstance(item, dict):
                continue
            text = item.get("memory") or item.get("content")
            rendered = cls._content_text(text).strip()
            if rendered:
                memories.append(rendered)
        if not memories:
            return ""
        return _MEMORY_HEADER + "\n" + "\n".join(f"- {memory}" for memory in memories)

    def _load_context(self, state: PowerMemState) -> str:
        query = self._latest_message_text(state, "user").strip()
        if not query or not self.user_id:
            return ""
        try:
            result = self.memory.search(
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
        except Exception:
            logger.warning("PowerMem search failed; continuing without memory context", exc_info=True)
            return ""
        return self._format_search_result(result)

    def before_agent(self, state: PowerMemState, runtime: Any) -> PowerMemStateUpdate:
        del runtime
        return {"powermem_context": self._load_context(state)}

    async def abefore_agent(
        self,
        state: PowerMemState,
        runtime: Any,
    ) -> PowerMemStateUpdate:
        del runtime
        context = await asyncio.to_thread(self._load_context, state)
        return {"powermem_context": context}

    @staticmethod
    def _request_with_context(request: ModelRequest[Any]) -> ModelRequest[Any]:
        context = str(request.state.get("powermem_context", "") or "").strip()
        if not context:
            return request

        existing = request.system_message.text if request.system_message else ""
        combined = f"{existing}\n\n{context}".strip() if existing else context
        return request.override(system_message=SystemMessage(content=combined))

    def wrap_model_call(self, request: ModelRequest[Any], handler: Any) -> ModelResponse[Any]:
        return handler(self._request_with_context(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Any,
    ) -> ModelResponse[Any]:
        return await handler(self._request_with_context(request))

    def _save_interaction(self, state: PowerMemState) -> None:
        if not self.save_interactions or not self.user_id:
            return

        user_text = self._latest_message_text(state, "user").strip()
        assistant_text = self._latest_message_text(state, "assistant").strip()
        if not user_text or not assistant_text:
            return

        self.memory.add(
            [
                {"role": "user", "content": user_text},
                {"role": "assistant", "content": assistant_text},
            ],
            user_id=self.user_id,
            infer=False,
        )

    def after_agent(self, state: PowerMemState, runtime: Any) -> None:
        del runtime
        self._save_interaction(state)

    async def aafter_agent(self, state: PowerMemState, runtime: Any) -> None:
        del runtime
        await asyncio.to_thread(self._save_interaction, state)
