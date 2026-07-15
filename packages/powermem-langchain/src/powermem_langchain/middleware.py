"""PowerMem long-term memory middleware for LangChain agents."""

from __future__ import annotations

import inspect
import logging
from typing import Any, Awaitable, Callable, NotRequired, TypedDict

from langchain.agents.middleware import AgentMiddleware, AgentState, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


logger = logging.getLogger(__name__)

_CONTEXT_HEADER = "Relevant long-term memories for this user:"


class PowerMemState(AgentState):
    """Agent state used to carry retrieved memory between middleware hooks."""

    powermem_context: NotRequired[str]


class PowerMemStateUpdate(TypedDict):
    """State update returned by the memory-loading hooks."""

    powermem_context: str


def _message_text(message: BaseMessage) -> str:
    """Return useful plain text for both string and content-block messages."""
    if isinstance(message.content, str):
        return message.content
    parts: list[str] = []
    for block in message.content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and isinstance(block.get("text"), str):
            parts.append(block["text"])
    return "\n".join(parts)


def _latest_message(messages: list[BaseMessage], kind: type[BaseMessage]) -> BaseMessage | None:
    return next((message for message in reversed(messages) if isinstance(message, kind)), None)


def _format_search_result(result: Any) -> str:
    """Normalize supported PowerMem search response shapes into prompt text."""
    items = result.get("results", []) if isinstance(result, dict) else result
    if not isinstance(items, list):
        return ""

    memories: list[str] = []
    for item in items:
        if isinstance(item, str):
            text = item
        elif isinstance(item, dict):
            value = item.get("memory") or item.get("content") or item.get("text")
            text = value if isinstance(value, str) else ""
        else:
            text = ""
        if text and text not in memories:
            memories.append(text)
    if not memories:
        return ""
    return _CONTEXT_HEADER + "\n" + "\n".join(f"- {memory}" for memory in memories)


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """Retrieve and persist PowerMem memories around a LangChain agent run.

    Search is deliberately fail-open: an unavailable memory service must not
    prevent the agent from answering. Write failures are handled the same way.
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
        super().__init__()
        if not user_id:
            raise ValueError("user_id must be provided explicitly")
        if search_limit < 1:
            raise ValueError("search_limit must be at least 1")
        self.memory = memory
        self.user_id = user_id
        self.search_limit = search_limit
        self.save_interactions = save_interactions

    def _query(self, state: PowerMemState) -> str:
        message = _latest_message(list(state.get("messages", [])), HumanMessage)
        return _message_text(message) if message else ""

    def _search(self, query: str) -> PowerMemStateUpdate:
        if not query:
            return {"powermem_context": ""}
        try:
            result = self.memory.search(
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
            if inspect.isawaitable(result):
                raise TypeError("async memory.search cannot be used in a synchronous agent")
            return {"powermem_context": _format_search_result(result)}
        except Exception:
            logger.warning("PowerMem search failed; continuing without memory", exc_info=True)
            return {"powermem_context": ""}

    async def _asearch(self, query: str) -> PowerMemStateUpdate:
        if not query:
            return {"powermem_context": ""}
        try:
            result = self.memory.search(
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
            if inspect.isawaitable(result):
                result = await result
            return {"powermem_context": _format_search_result(result)}
        except Exception:
            logger.warning("PowerMem search failed; continuing without memory", exc_info=True)
            return {"powermem_context": ""}

    def before_agent(self, state: PowerMemState, runtime: Any) -> PowerMemStateUpdate:
        return self._search(self._query(state))

    async def abefore_agent(
        self,
        state: PowerMemState,
        runtime: Any,
    ) -> PowerMemStateUpdate:
        return await self._asearch(self._query(state))

    @staticmethod
    def _request_with_context(request: ModelRequest) -> ModelRequest:
        context = request.state.get("powermem_context", "")
        if not context:
            return request
        system_prompt = request.system_prompt
        combined = f"{system_prompt}\n\n{context}" if system_prompt else context
        return request.override(system_prompt=combined)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._request_with_context(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._request_with_context(request))

    def _interaction(self, state: PowerMemState) -> list[dict[str, str]] | None:
        messages = list(state.get("messages", []))
        human = _latest_message(messages, HumanMessage)
        assistant = _latest_message(messages, AIMessage)
        if human is None or assistant is None:
            return None
        return [
            {"role": "user", "content": _message_text(human)},
            {"role": "assistant", "content": _message_text(assistant)},
        ]

    def after_agent(self, state: PowerMemState, runtime: Any) -> None:
        interaction = self._interaction(state)
        if not self.save_interactions or interaction is None:
            return None
        try:
            result = self.memory.add(interaction, user_id=self.user_id, infer=False)
            if inspect.isawaitable(result):
                raise TypeError("async memory.add cannot be used in a synchronous agent")
        except Exception:
            logger.warning("PowerMem write-back failed; agent result is preserved", exc_info=True)
        return None

    async def aafter_agent(self, state: PowerMemState, runtime: Any) -> None:
        interaction = self._interaction(state)
        if not self.save_interactions or interaction is None:
            return None
        try:
            result = self.memory.add(interaction, user_id=self.user_id, infer=False)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.warning("PowerMem write-back failed; agent result is preserved", exc_info=True)
        return None
