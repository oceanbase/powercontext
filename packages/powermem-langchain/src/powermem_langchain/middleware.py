"""LangChain middleware that provides PowerMem-backed long-term memory."""

from __future__ import annotations

import inspect
import logging
from asyncio import to_thread
from collections.abc import Awaitable, Callable, Mapping, Sequence
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
    """Agent state used to carry retrieved memory into model-call hooks."""

    powermem_context: NotRequired[str]


class PowerMemStateUpdate(TypedDict):
    """State update returned after loading relevant memories."""

    powermem_context: str


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """Use a PowerMem client as a LangChain agent's long-term memory layer."""

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
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected PowerMemMiddleware arguments: {unexpected}")
        if memory is None:
            raise ValueError("memory must be a PowerMem client")
        if not isinstance(user_id, str) or not user_id.strip():
            raise ValueError("user_id must be a non-empty explicit user identity")
        if isinstance(search_limit, bool) or not isinstance(search_limit, int):
            raise TypeError("search_limit must be an integer")
        if search_limit < 1:
            raise ValueError("search_limit must be at least 1")

        self.memory = memory
        self.user_id = user_id
        self.search_limit = search_limit
        self.save_interactions = save_interactions

    def before_agent(self, state: PowerMemState, runtime) -> PowerMemStateUpdate | None:
        del runtime
        query = _latest_message_text(state.get("messages", []), HumanMessage)
        if not query:
            return None

        try:
            result = self.memory.search(
                query=query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
            if inspect.isawaitable(result):
                _close_awaitable(result)
                raise TypeError(
                    "The configured PowerMem client is asynchronous; use agent.ainvoke()"
                )
        except Exception:
            logger.warning("PowerMem search failed; continuing without memory", exc_info=True)
            return None

        context = _format_search_result(result)
        return {"powermem_context": context} if context else None

    async def abefore_agent(
        self,
        state: PowerMemState,
        runtime,
    ) -> PowerMemStateUpdate | None:
        del runtime
        query = _latest_message_text(state.get("messages", []), HumanMessage)
        if not query:
            return None

        try:
            result = await _call_async_compatible(
                self.memory.search,
                query=query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
        except Exception:
            logger.warning("PowerMem search failed; continuing without memory", exc_info=True)
            return None

        context = _format_search_result(result)
        return {"powermem_context": context} if context else None

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        return handler(_inject_memory_context(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        return await handler(_inject_memory_context(request))

    def after_agent(self, state: PowerMemState, runtime) -> None:
        del runtime
        if not self.save_interactions:
            return

        interaction = _final_interaction(state.get("messages", []))
        if interaction is None:
            return

        result = self.memory.add(
            messages=interaction,
            user_id=self.user_id,
            infer=False,
        )
        if inspect.isawaitable(result):
            _close_awaitable(result)
            raise TypeError(
                "The configured PowerMem client is asynchronous; use agent.ainvoke()"
            )

    async def aafter_agent(self, state: PowerMemState, runtime) -> None:
        del runtime
        if not self.save_interactions:
            return

        interaction = _final_interaction(state.get("messages", []))
        if interaction is None:
            return

        await _call_async_compatible(
            self.memory.add,
            messages=interaction,
            user_id=self.user_id,
            infer=False,
        )


async def _call_async_compatible(function: Callable[..., Any], **kwargs: Any) -> Any:
    """Call native async methods directly and move sync SDK calls off the event loop."""

    if inspect.iscoroutinefunction(function):
        return await function(**kwargs)

    result = await to_thread(function, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _close_awaitable(awaitable: Any) -> None:
    close = getattr(awaitable, "close", None)
    if callable(close):
        close()


def _latest_message_text(
    messages: Sequence[Any],
    message_type: type[BaseMessage],
) -> str | None:
    for message in reversed(messages):
        if isinstance(message, message_type):
            text = _message_text(message)
            if text:
                return text
    return None


def _message_text(message: BaseMessage) -> str:
    text = message.text
    if isinstance(text, str):
        return text.strip()
    return str(text).strip()


def _format_search_result(result: Any) -> str:
    if not isinstance(result, Mapping):
        return ""

    raw_memories = result.get("results", [])
    if not isinstance(raw_memories, Sequence) or isinstance(raw_memories, (str, bytes)):
        return ""

    memories: list[str] = []
    for item in raw_memories:
        if isinstance(item, Mapping):
            value = item.get("memory") or item.get("content")
        else:
            value = item
        if value is not None and (text := str(value).strip()):
            memories.append(text)

    if not memories:
        return ""

    lines = [
        "Relevant long-term memories from PowerMem:",
        "Use these only when they are relevant to the current request.",
    ]
    lines.extend(f"- {memory}" for memory in memories)
    return "\n".join(lines)


def _inject_memory_context(request: ModelRequest[Any]) -> ModelRequest[Any]:
    context = request.state.get("powermem_context")
    if not context:
        return request

    existing = request.system_message
    if existing is None:
        system_message = SystemMessage(content=context)
    elif isinstance(existing.content, str):
        separator = "\n\n" if existing.content else ""
        system_message = existing.model_copy(
            update={"content": f"{existing.content}{separator}{context}"}
        )
    else:
        content = [*existing.content, {"type": "text", "text": context}]
        system_message = existing.model_copy(update={"content": content})

    return request.override(system_message=system_message)


def _final_interaction(messages: Sequence[Any]) -> list[dict[str, str]] | None:
    assistant_index: int | None = None
    assistant_text: str | None = None
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if isinstance(message, AIMessage) and (text := _message_text(message)):
            assistant_index = index
            assistant_text = text
            break

    if assistant_index is None or assistant_text is None:
        return None

    user_text = _latest_message_text(messages[:assistant_index], HumanMessage)
    if not user_text:
        return None

    return [
        {"role": "user", "content": user_text},
        {"role": "assistant", "content": assistant_text},
    ]
