"""LangChain middleware entry point for PowerMem.

The VLDB 2026 summer school branch intentionally provides only the public entry
point. Students are expected to replace this placeholder with a LangChain
middleware implementation that satisfies the package contract tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, NotRequired, TypedDict

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


class PowerMemState(AgentState):
    """State schema reserved for the PowerMem middleware implementation."""

    powermem_context: NotRequired[str]


class PowerMemStateUpdate(TypedDict):
    """State update returned by memory-loading middleware hooks."""

    powermem_context: str


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """Connect a LangChain agent to a PowerMem memory instance."""

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
        """Store the configuration used by the middleware hooks.

        Args:
            memory: PowerMem-compatible memory instance.
            user_id: Explicit business user identity used for search and write-back.
            search_limit: Maximum number of memories retrieved for one request.
            save_interactions: Whether completed interactions should be written back.
            **kwargs: Reserved for future middleware options.
        """
        if memory is None:
            raise ValueError("memory must not be None")
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id must be a non-empty string")
        if not isinstance(search_limit, int) or isinstance(search_limit, bool):
            raise TypeError("search_limit must be an integer")
        if search_limit <= 0:
            raise ValueError("search_limit must be greater than zero")
        if not isinstance(save_interactions, bool):
            raise TypeError("save_interactions must be a boolean")

        self.memory = memory
        self.user_id = user_id
        self.search_limit = search_limit
        self.save_interactions = save_interactions
        self.options = dict(kwargs)

    @staticmethod
    def _latest_user_text(state: PowerMemState) -> str | None:
        """Return the text of the latest user message."""
        for message in reversed(state.get("messages", [])):
            if not isinstance(message, HumanMessage):
                continue

            if isinstance(message.content, str):
                text = message.content.strip()
                return text or None

        return None
    
    @staticmethod
    def _latest_assistant_text(state: PowerMemState) -> str | None:
        """Return the text of the final non-empty assistant message."""
        for message in reversed(state.get("messages", [])):
            if not isinstance(message, AIMessage):
                continue

            if isinstance(message.content, str):
                text = message.content.strip()

                if text:
                    return text

        return None

    def _search_context(self, query: str) -> str:
        """Search PowerMem and format retrieved memories for the model."""
        try:
            result = self.memory.search(
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
        except Exception:
            # PowerMem is an enhancement. Search failures must not stop the agent.
            return ""

        if not isinstance(result, dict):
            return ""

        memories: list[str] = []

        for item in result.get("results", []):
            if not isinstance(item, dict):
                continue

            memory_text = item.get("memory")

            if isinstance(memory_text, str) and memory_text.strip():
                memories.append(memory_text.strip())

        if not memories:
            return ""

        formatted_memories = "\n".join(
            f"- {memory_text}" for memory_text in memories
        )

        return (
            "Relevant long-term memories from PowerMem:\n"
            f"{formatted_memories}"
        )
    
    @staticmethod
    def _request_with_context(request: ModelRequest) -> ModelRequest:
        """Return a model request containing the retrieved PowerMem context."""
        context = request.state.get("powermem_context", "")

        if not context:
            return request

        current_system_message = request.system_message

        if current_system_message is None:
            new_system_message = SystemMessage(content=context)

        elif isinstance(current_system_message.content, str):
            original_content = current_system_message.content.strip()

            if original_content:
                new_content = f"{original_content}\n\n{context}"
            else:
                new_content = context

            new_system_message = SystemMessage(content=new_content)

        else:
            new_content = list(current_system_message.content)
            new_content.append(
                {
                    "type": "text",
                    "text": context,
                }
            )

            new_system_message = SystemMessage(content=new_content)

        return request.override(
            system_message=new_system_message,
        )
    
    def before_agent(
        self,
        state: PowerMemState,
        runtime,
    ) -> PowerMemStateUpdate:
        """Load memories related to the latest user message."""
        query = self._latest_user_text(state)

        if query is None:
            return {"powermem_context": ""}

        context = self._search_context(query)

        return {
            "powermem_context": context,
        }

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Inject PowerMem context and execute the synchronous model call."""
        new_request = self._request_with_context(request)
        return handler(new_request)
    
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[
            [ModelRequest],
            Awaitable[ModelResponse],
        ],
    ) -> ModelResponse:
        """Inject PowerMem context and execute the asynchronous model call."""
        new_request = self._request_with_context(request)
        return await handler(new_request)

    async def abefore_agent(
        self,
        state: PowerMemState,
        runtime,
    ) -> PowerMemStateUpdate:
        """Asynchronously load memories related to the latest user message."""
        query = self._latest_user_text(state)

        if query is None:
            return {"powermem_context": ""}

        context = await asyncio.to_thread(
            self._search_context,
            query,
        )

        return {
            "powermem_context": context,
        }

    def after_agent(self, state: PowerMemState, runtime) -> None:
        """Write the completed interaction back to PowerMem."""
        if not self.save_interactions:
            return None

        user_text = self._latest_user_text(state)
        assistant_text = self._latest_assistant_text(state)

        if user_text is None or assistant_text is None:
            return None

        interaction = (
            f"User: {user_text}\n"
            f"Assistant: {assistant_text}"
        )

        try:
            self.memory.add(
                interaction,
                user_id=self.user_id,
                infer=False,
            )
        except Exception:
            # 写回失败不能破坏已经成功生成的 Agent 回复。
            return None

        return None
