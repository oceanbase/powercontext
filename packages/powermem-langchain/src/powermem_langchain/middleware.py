"""PowerMem middleware integration for LangChain agents."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

logger = logging.getLogger(__name__)


class PowerMemMiddleware(AgentMiddleware[AgentState[Any], Any, Any]):
    """Use PowerMem as a long-term memory layer for LangChain agents."""

    state_schema = AgentState

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
        self.search_limit = search_limit
        self.save_interactions = save_interactions

    @staticmethod
    def _content_text(content: Any) -> str:
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return str(content).strip() if content is not None else ""

        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part).strip()

    @classmethod
    def _latest_user_text(cls, messages: list[Any]) -> str:
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                return cls._content_text(message.content)
        return ""

    @staticmethod
    def _memory_texts(result: Any) -> list[str]:
        if isinstance(result, dict):
            items = result.get("results", [])
        elif isinstance(result, list):
            items = result
        else:
            return []

        texts: list[str] = []
        for item in items:
            if isinstance(item, str):
                text = item
            elif isinstance(item, dict):
                value = item.get("memory") or item.get("content")
                text = value if isinstance(value, str) else ""
            else:
                text = ""
            text = text.strip()
            if text:
                texts.append(text)
        return texts

    @staticmethod
    def _format_context(memory_texts: list[str]) -> str:
        if not memory_texts:
            return ""
        memories = "\n".join(f"- {text}" for text in memory_texts)
        return f"Relevant PowerMem memories:\n{memories}"

    @staticmethod
    async def _acall(method: Any, *args: Any, **kwargs: Any) -> Any:
        if inspect.iscoroutinefunction(method):
            return await method(*args, **kwargs)

        result = await asyncio.to_thread(method, *args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    def _search_context(self, messages: list[Any]) -> str:
        query = self._latest_user_text(messages)
        if not query:
            return ""

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
            return ""

        return self._format_context(self._memory_texts(result))

    async def _asearch_context(self, messages: list[Any]) -> str:
        query = self._latest_user_text(messages)
        if not query:
            return ""

        try:
            result = await self._acall(
                self.memory.search,
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
        except Exception:
            logger.warning(
                "PowerMem search failed; continuing without memory",
                exc_info=True,
            )
            return ""

        return self._format_context(self._memory_texts(result))

    @staticmethod
    def _with_context(request: ModelRequest[Any], context: str) -> ModelRequest[Any]:
        if not context:
            return request

        system_message = request.system_message
        if system_message is None:
            system_message = SystemMessage(content=context)
        elif isinstance(system_message.content, str):
            existing = system_message.content.rstrip()
            combined = f"{existing}\n\n{context}" if existing else context
            system_message = system_message.model_copy(update={"content": combined})
        else:
            content = [*system_message.content, {"type": "text", "text": context}]
            system_message = system_message.model_copy(update={"content": content})

        return request.override(system_message=system_message)

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any] | AIMessage:
        context = self._search_context(request.messages)
        return handler(self._with_context(request, context))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any] | AIMessage:
        context = await self._asearch_context(request.messages)
        return await handler(self._with_context(request, context))

    @classmethod
    def _interaction_messages(
        cls,
        state: AgentState[Any],
    ) -> list[dict[str, str]]:
        messages = list(state.get("messages", []))
        user_index = -1
        user_text = ""
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if isinstance(message, HumanMessage):
                user_index = index
                user_text = cls._content_text(message.content)
                break

        if user_index < 0 or not user_text:
            return []

        assistant_text = ""
        for message in reversed(messages[user_index + 1 :]):
            if isinstance(message, AIMessage):
                assistant_text = cls._content_text(message.content)
                if assistant_text:
                    break

        if not assistant_text:
            return []

        return [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ]

    def after_agent(self, state: AgentState[Any], runtime: Any) -> None:
        del runtime
        if not self.save_interactions:
            return

        interaction = self._interaction_messages(state)
        if interaction:
            self.memory.add(
                interaction,
                user_id=self.user_id,
                infer=False,
            )

    async def aafter_agent(self, state: AgentState[Any], runtime: Any) -> None:
        del runtime
        if not self.save_interactions:
            return

        interaction = self._interaction_messages(state)
        if interaction:
            await self._acall(
                self.memory.add,
                interaction,
                user_id=self.user_id,
                infer=False,
            )
