"""LangChain middleware entry point for PowerMem.

The VLDB 2026 summer school branch intentionally provides only the public entry
point. Students are expected to replace this placeholder with a LangChain
middleware implementation that satisfies the package contract tests.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any, NotRequired, TypedDict

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage


logger = logging.getLogger(__name__)


class PowerMemState(AgentState):
    """State schema reserved for the PowerMem middleware implementation."""

    powermem_context: NotRequired[str]


class PowerMemStateUpdate(TypedDict, total=False):
    """State update returned by memory-loading middleware hooks."""

    powermem_context: str
    messages: list[BaseMessage]


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """LangChain middleware that gives an agent access to PowerMem memories."""

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
        self.kwargs = kwargs

    def before_agent(self, state: PowerMemState, runtime) -> PowerMemStateUpdate | None:
        query = self._latest_message_text(state.get("messages", ()), HumanMessage)
        if not query:
            return None

        context = self._search_context(query)
        if not context:
            return None

        return {
            "powermem_context": context,
            "messages": [SystemMessage(content=context)],
        }

    async def abefore_agent(
        self,
        state: PowerMemState,
        runtime,
    ) -> PowerMemStateUpdate | None:
        return self.before_agent(state, runtime)

    def after_agent(self, state: PowerMemState, runtime) -> None:
        if not self.save_interactions:
            return

        messages = state.get("messages", ())
        user_message = self._latest_message_text(messages, HumanMessage)
        assistant_message = self._latest_message_text(messages, AIMessage)
        if not user_message or not assistant_message:
            return

        memory_text = f"User: {user_message}\nAssistant: {assistant_message}"
        self.memory.add(memory_text, user_id=self.user_id, infer=False)

    def _search_context(self, query: str) -> str | None:
        try:
            result = self.memory.search(
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
        except Exception:
            logger.exception("PowerMem search failed; continuing without memories.")
            return None

        memories = self._memory_items(result)
        if not memories:
            return None

        lines = ["Relevant memories from PowerMem:"]
        lines.extend(f"- {memory}" for memory in memories)
        return "\n".join(lines)

    def _memory_items(self, result: Any) -> list[str]:
        if not isinstance(result, dict):
            return []

        items = result.get("results", [])
        if not isinstance(items, Sequence) or isinstance(items, str):
            return []

        memories: list[str] = []
        for item in items[: self.search_limit]:
            if isinstance(item, dict):
                content = item.get("memory") or item.get("content")
            else:
                content = str(item)

            text = self._content_to_text(content)
            if text:
                memories.append(text)

        return memories

    def _latest_message_text(
        self,
        messages: Sequence[Any],
        message_type: type[BaseMessage],
    ) -> str | None:
        for message in reversed(messages):
            if isinstance(message, message_type):
                text = self._content_to_text(message.content)
                if text:
                    return text
            elif isinstance(message, dict):
                role = str(message.get("role") or "").lower()
                if (
                    message_type is HumanMessage
                    and role in {"human", "user"}
                    or message_type is AIMessage
                    and role in {"ai", "assistant"}
                ):
                    text = self._content_to_text(message.get("content"))
                    if text:
                        return text

        return None

    def _content_to_text(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, Sequence) and not isinstance(content, (bytes, bytearray)):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    value = item.get("text") or item.get("content")
                    if value:
                        parts.append(str(value))
                elif item is not None:
                    parts.append(str(item))
            return "\n".join(parts).strip()

        return str(content).strip()
