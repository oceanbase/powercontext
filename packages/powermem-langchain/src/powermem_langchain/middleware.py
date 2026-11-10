"""PowerMem middleware integration for LangChain agents."""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    RemoveMessage,
    SystemMessage,
)
from langgraph.graph.message import REMOVE_ALL_MESSAGES

logger = logging.getLogger(__name__)

_CONTEXT_MESSAGE_ID = "powermem-context"


class PowerMemState(AgentState):
    """State schema used by the PowerMem middleware."""


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """Load relevant PowerMem memories into a LangChain agent run."""

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
    def _latest_user_text(cls, state: PowerMemState) -> str:
        for message in reversed(state.get("messages", [])):
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

    @staticmethod
    def _replace_context_message(
        state: PowerMemState,
        context: str,
    ) -> dict[str, Any] | None:
        messages = list(state.get("messages", []))
        had_context_message = any(
            isinstance(message, SystemMessage) and message.id == _CONTEXT_MESSAGE_ID
            for message in messages
        )
        messages = [
            message
            for message in messages
            if not (
                isinstance(message, SystemMessage) and message.id == _CONTEXT_MESSAGE_ID
            )
        ]

        if not context and not had_context_message:
            return None

        replacement: list[Any] = [RemoveMessage(id=REMOVE_ALL_MESSAGES)]
        if context:
            replacement.append(SystemMessage(content=context, id=_CONTEXT_MESSAGE_ID))
        replacement.extend(messages)
        return {"messages": replacement}

    def _load_context(self, state: PowerMemState) -> dict[str, Any] | None:
        query = self._latest_user_text(state)
        if not query:
            return self._replace_context_message(state, "")

        try:
            result = self.memory.search(
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
        except Exception:
            logger.warning(
                "PowerMem search failed; continuing without memory", exc_info=True
            )
            return self._replace_context_message(state, "")

        context = self._format_context(self._memory_texts(result))
        return self._replace_context_message(state, context)

    async def _aload_context(
        self,
        state: PowerMemState,
    ) -> dict[str, Any] | None:
        query = self._latest_user_text(state)
        if not query:
            return self._replace_context_message(state, "")

        try:
            result = await self._acall(
                self.memory.search,
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
        except Exception:
            logger.warning(
                "PowerMem search failed; continuing without memory", exc_info=True
            )
            return self._replace_context_message(state, "")

        context = self._format_context(self._memory_texts(result))
        return self._replace_context_message(state, context)

    def before_model(
        self,
        state: PowerMemState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        del runtime
        return self._load_context(state)

    async def abefore_model(
        self,
        state: PowerMemState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        del runtime
        return await self._aload_context(state)

    @classmethod
    def _interaction_messages(cls, state: PowerMemState) -> list[dict[str, str]]:
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

    def after_agent(self, state: PowerMemState, runtime: Any) -> None:
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

    async def aafter_agent(self, state: PowerMemState, runtime: Any) -> None:
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
