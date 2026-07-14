"""LangChain middleware entry point for PowerMem."""

from __future__ import annotations

import inspect
from typing import Any, NotRequired, TypedDict

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
)
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage


class PowerMemState(AgentState):
    """State schema reserved for the PowerMem middleware implementation."""

    powermem_context: NotRequired[str]
    powermem_context_injected: NotRequired[bool]


class PowerMemStateUpdate(TypedDict):
    """State update returned by memory-loading middleware hooks."""

    powermem_context: str


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """PowerMem-backed long-term memory middleware for LangChain agents."""

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
        self.search_limit = max(1, int(search_limit))
        self.save_interactions = save_interactions
        self._kwargs = kwargs

    def before_agent(
        self,
        state: PowerMemState,
        runtime: Any,
    ) -> PowerMemStateUpdate | None:
        user_text = self._latest_human_text(state.get("messages", []))
        if not user_text:
            return None

        context = self._format_memories(self._search_memories(user_text))
        if not context:
            return None

        return {"powermem_context": context}

    async def abefore_agent(
        self,
        state: PowerMemState,
        runtime: Any,
    ) -> PowerMemStateUpdate | None:
        user_text = self._latest_human_text(state.get("messages", []))
        if not user_text:
            return None

        context = self._format_memories(await self._asearch_memories(user_text))
        if not context:
            return None

        return {"powermem_context": context}

    def before_model(self, state: PowerMemState, runtime: Any) -> dict[str, Any] | None:
        return self._context_message_update(state)

    async def abefore_model(
        self,
        state: PowerMemState,
        runtime: Any,
    ) -> dict[str, Any] | None:
        return self._context_message_update(state)

    def after_agent(self, state: PowerMemState, runtime: Any) -> None:
        if not self.save_interactions:
            return None

        user_text = self._latest_human_text(state.get("messages", []))
        assistant_text = self._latest_assistant_text(state.get("messages", []))
        if not user_text or not assistant_text:
            return None

        self._save_interaction(user_text, assistant_text)
        return None

    async def aafter_agent(self, state: PowerMemState, runtime: Any) -> None:
        if not self.save_interactions:
            return None

        user_text = self._latest_human_text(state.get("messages", []))
        assistant_text = self._latest_assistant_text(state.get("messages", []))
        if not user_text or not assistant_text:
            return None

        await self._asave_interaction(user_text, assistant_text)
        return None

    def _context_message_update(self, state: PowerMemState) -> dict[str, Any] | None:
        context = state.get("powermem_context")
        if not context or state.get("powermem_context_injected"):
            return None

        return {
            "messages": [SystemMessage(content=context)],
            "powermem_context_injected": True,
        }

    def _search_memories(self, query: str) -> list[Any]:
        try:
            result = self.memory.search(
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
        except Exception:
            return []

        return self._extract_results(result)

    async def _asearch_memories(self, query: str) -> list[Any]:
        try:
            result = self.memory.search(
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            return []

        return self._extract_results(result)

    def _extract_results(self, result: Any) -> list[Any]:
        if isinstance(result, dict):
            results = result.get("results", [])
            return results if isinstance(results, list) else []
        if isinstance(result, list):
            return result
        return []

    def _format_memories(self, results: list[Any]) -> str:
        lines: list[str] = []
        seen: set[str] = set()

        for item in results:
            text = self._memory_item_to_text(item).strip()
            if not text or text in seen:
                continue

            seen.add(text)
            lines.append(f"- {text}")
            if len(lines) >= self.search_limit:
                break

        if not lines:
            return ""

        return (
            "Relevant long-term memories for this user:\n"
            + "\n".join(lines)
            + "\n\nUse these memories when relevant. Ignore them if they are "
            "unrelated to the current request."
        )

    def _memory_item_to_text(self, item: Any) -> str:
        if isinstance(item, dict):
            return self._content_to_text(
                item.get("memory") or item.get("content") or item.get("text")
            )
        return self._content_to_text(item)

    def _latest_human_text(self, messages: list[BaseMessage]) -> str:
        for message in reversed(messages):
            if isinstance(message, HumanMessage) or message.type == "human":
                return self._content_to_text(message.content).strip()
        return ""

    def _latest_assistant_text(self, messages: list[BaseMessage]) -> str:
        for message in reversed(messages):
            if isinstance(message, AIMessage) or message.type == "ai":
                return self._content_to_text(message.content).strip()
        return ""

    def _content_to_text(self, content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, dict):
            return self._content_to_text(
                content.get("text") or content.get("content") or ""
            )
        if isinstance(content, list):
            parts = [self._content_to_text(part).strip() for part in content]
            return "\n".join(part for part in parts if part)
        return str(content)

    def _save_interaction(self, user_text: str, assistant_text: str) -> None:
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
            return

    async def _asave_interaction(self, user_text: str, assistant_text: str) -> None:
        try:
            result = self.memory.add(
                [
                    {"role": "user", "content": user_text},
                    {"role": "assistant", "content": assistant_text},
                ],
                user_id=self.user_id,
                infer=False,
            )
            if inspect.isawaitable(result):
                await result
        except Exception:
            return
