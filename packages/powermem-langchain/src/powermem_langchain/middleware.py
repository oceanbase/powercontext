"""LangChain middleware entry point for PowerMem.

The VLDB 2026 summer school branch intentionally provides only the public entry
point. Students are expected to replace this placeholder with a LangChain
middleware implementation that satisfies the package contract tests.
"""

from __future__ import annotations

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

    def before_agent(self, state: PowerMemState, runtime) -> PowerMemStateUpdate | None:
        return self._load_memory_context(state)

    async def abefore_agent(
        self,
        state: PowerMemState,
        runtime,
    ) -> PowerMemStateUpdate | None:
        return self._load_memory_context(state)

    def after_agent(self, state: PowerMemState, runtime) -> None:
        if not self.save_interactions:
            return

        user_message = self._latest_message(state, HumanMessage)
        assistant_message = self._latest_message(state, AIMessage)
        if user_message is None or assistant_message is None:
            return

        interaction = (
            f"User: {self._message_text(user_message)}\n"
            f"Assistant: {self._message_text(assistant_message)}"
        )
        self.memory.add(interaction, user_id=self.user_id, infer=False)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """Add memories loaded by ``before_agent`` to the model system message."""
        return handler(self._request_with_memory_context(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """Async counterpart of :meth:`wrap_model_call`."""
        return await handler(self._request_with_memory_context(request))

    def _load_memory_context(
        self, state: PowerMemState
    ) -> PowerMemStateUpdate | None:
        user_message = self._latest_message(state, HumanMessage)
        if user_message is None:
            return None

        query = self._message_text(user_message)
        if not query:
            return None

        try:
            result = self.memory.search(
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
        except Exception:
            # Memory retrieval must not prevent the agent from answering.
            return None

        memories = [
            item["memory"]
            for item in result.get("results", [])
            if isinstance(item, dict) and item.get("memory")
        ]
        if not memories:
            return None

        return {"powermem_context": "\n".join(memories)}

    @staticmethod
    def _latest_message(state: PowerMemState, message_type: type):
        for message in reversed(state["messages"]):
            if isinstance(message, message_type):
                return message
        return None

    @staticmethod
    def _message_text(message: HumanMessage | AIMessage) -> str:
        return message.content if isinstance(message.content, str) else str(message.content)

    @staticmethod
    def _request_with_memory_context(request: ModelRequest) -> ModelRequest:
        context = request.state.get("powermem_context")
        if not context:
            return request

        memory_prompt = f"Relevant memories from PowerMem:\n{context}"
        if request.system_message is not None:
            memory_prompt = f"{request.system_message.text}\n\n{memory_prompt}"

        return request.override(
            system_message=SystemMessage(content=memory_prompt),
        )
