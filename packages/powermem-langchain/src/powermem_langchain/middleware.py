"""LangChain middleware entry point for PowerMem.

The VLDB 2026 summer school branch intentionally provides only the public entry
point. Students are expected to replace this placeholder with a LangChain
middleware implementation that satisfies the package contract tests.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, NotRequired

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


logger = logging.getLogger(__name__)


class PowerMemState(AgentState):
    """State schema reserved for the PowerMem middleware implementation."""

    powermem_memories: NotRequired[list[str]]


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """LangChain v1 middleware that injects PowerMem retrieval into the model
    context and persists the interaction after the agent run completes."""

    state_schema = PowerMemState

    def __init__(
        self,
        *,
        memory: Any,
        user_id: str | None = None,
        search_limit: int = 5,
        save_interactions: bool = True,
        system_template: str = (
            "<system>\n"
            "The following memories were retrieved from PowerMem and may be "
            "relevant to the user's request. Use them when answering, but do "
            "not mention them explicitly unless asked.\n"
            "{memories}\n"
            "</system>"
        ),
        infer: bool = False,
        **kwargs: Any,
    ) -> None:
        if user_id is None:
            raise ValueError(
                "PowerMemMiddleware requires an explicit user_id; "
                "do not rely on global or runtime-inferred user identity."
            )
        self.memory = memory
        self.user_id = user_id
        self.search_limit = search_limit
        self.save_interactions = save_interactions
        self.system_template = system_template
        self.infer = infer

    def _retrieve(self, query: str) -> list[str]:
        try:
            result = self.memory.search(
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
        except Exception as exc:
            logger.warning(
                "PowerMem search failed for user_id=%s: %s", self.user_id, exc
            )
            return []
        memories = result.get("results", []) if isinstance(result, dict) else []
        return [
            item["memory"]
            for item in memories
            if isinstance(item, dict) and item.get("memory")
        ]

    async def _aretrieve(self, query: str) -> list[str]:
        return await asyncio.to_thread(self._retrieve, query)

    @staticmethod
    def _latest_user_text(messages: list[Any] | None) -> str | None:
        if not messages:
            return None
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                content = message.content
                if isinstance(content, str) and content.strip():
                    return content
                if isinstance(content, list):
                    parts = [
                        part.get("text", "")
                        for part in content
                        if isinstance(part, dict) and part.get("text")
                    ]
                    joined = "".join(parts).strip()
                    if joined:
                        return joined
        return None

    @staticmethod
    def _last_of_type(messages: list[Any] | None, message_type: type) -> Any | None:
        if not messages:
            return None
        for message in reversed(messages):
            if isinstance(message, message_type):
                return message
        return None

    @staticmethod
    def _message_text(message: Any) -> str:
        content = getattr(message, "content", message)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("text")
            ]
            return "".join(parts)
        return str(content)

    def before_agent(self, state: PowerMemState, runtime) -> dict[str, Any] | None:
        query = self._latest_user_text(state.get("messages"))
        if not query:
            return None
        memories = self._retrieve(query)
        return {"powermem_memories": memories}

    async def abefore_agent(
        self,
        state: PowerMemState,
        runtime,
    ) -> dict[str, Any] | None:
        query = self._latest_user_text(state.get("messages"))
        if not query:
            return None
        memories = await self._aretrieve(query)
        return {"powermem_memories": memories}

    def _format_system_message(self, memories: list[str]) -> SystemMessage:
        body = "\n".join(f"- {memory}" for memory in memories)
        return SystemMessage(content=self.system_template.format(memories=body))

    def wrap_model_call(self, request, handler):
        memories = request.state.get("powermem_memories") or []
        if not memories:
            return handler(request)
        system_message = self._format_system_message(memories)
        request = request.override(messages=[system_message, *request.messages])
        return handler(request)

    async def awrap_model_call(self, request, handler):
        memories = request.state.get("powermem_memories") or []
        if not memories:
            return await handler(request)
        system_message = self._format_system_message(memories)
        request = request.override(messages=[system_message, *request.messages])
        return await handler(request)

    def _persist_interaction(self, user_message: Any, assistant_message: Any) -> None:
        try:
            self.memory.add(
                messages=[
                    {"role": "user", "content": self._message_text(user_message)},
                    {
                        "role": "assistant",
                        "content": self._message_text(assistant_message),
                    },
                ],
                user_id=self.user_id,
                infer=self.infer,
            )
        except Exception as exc:
            logger.warning(
                "PowerMem add failed for user_id=%s: %s", self.user_id, exc
            )

    def after_agent(self, state: PowerMemState, runtime) -> None:
        if not self.save_interactions:
            return
        messages = state.get("messages") or []
        user_message = self._last_of_type(messages, HumanMessage)
        assistant_message = self._last_of_type(messages, AIMessage)
        if user_message is None or assistant_message is None:
            return
        self._persist_interaction(user_message, assistant_message)

    async def aafter_agent(self, state: PowerMemState, runtime) -> None:
        if not self.save_interactions:
            return
        messages = state.get("messages") or []
        user_message = self._last_of_type(messages, HumanMessage)
        assistant_message = self._last_of_type(messages, AIMessage)
        if user_message is None or assistant_message is None:
            return
        await asyncio.to_thread(
            self._persist_interaction, user_message, assistant_message
        )