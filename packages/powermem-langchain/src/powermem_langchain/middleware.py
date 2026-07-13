"""LangChain middleware entry point for PowerMem."""

from __future__ import annotations

import inspect
import logging
from typing import Any, NotRequired, Sequence, TypedDict

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.messages.modifier import RemoveMessage
from langgraph.graph.message import REMOVE_ALL_MESSAGES

logger = logging.getLogger(__name__)

POWERMEM_CONTEXT_MESSAGE_ID = "powermem-langchain-context"


class PowerMemState(AgentState):
    """State schema reserved for the PowerMem middleware implementation."""

    powermem_context: NotRequired[str]


class PowerMemStateUpdate(TypedDict, total=False):
    """State update returned by memory-loading middleware hooks."""

    powermem_context: str
    messages: list[BaseMessage]


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """Connect a LangChain v1 agent to PowerMem long-term memory."""

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
        self.agent_id = kwargs.pop("agent_id", None)
        self.run_id = kwargs.pop("run_id", None)
        self.filters = kwargs.pop("filters", None)
        self.fail_open = kwargs.pop("fail_open", True)
        self.context_message_id = kwargs.pop(
            "context_message_id",
            POWERMEM_CONTEXT_MESSAGE_ID,
        )

    def before_agent(self, state: PowerMemState, runtime) -> PowerMemStateUpdate | None:
        return None

    async def abefore_agent(
        self,
        state: PowerMemState,
        runtime,
    ) -> PowerMemStateUpdate | None:
        return None

    def before_model(
        self,
        state: PowerMemState,
        runtime,
    ) -> PowerMemStateUpdate | None:
        return self._build_memory_context_update(state)

    async def abefore_model(
        self,
        state: PowerMemState,
        runtime,
    ) -> PowerMemStateUpdate | None:
        return await self._abuild_memory_context_update(state)

    def after_agent(self, state: PowerMemState, runtime) -> None:
        self._save_interaction(state)

    async def aafter_agent(self, state: PowerMemState, runtime) -> None:
        await self._asave_interaction(state)

    def _build_memory_context_update(
        self,
        state: PowerMemState,
    ) -> PowerMemStateUpdate | None:
        query = _latest_message_text(state.get("messages", []), HumanMessage)
        if not query:
            return None

        try:
            result = self.memory.search(
                query,
                user_id=self.user_id,
                agent_id=self.agent_id,
                run_id=self.run_id,
                filters=self.filters,
                limit=self.search_limit,
            )
        except Exception:
            if not self.fail_open:
                raise
            logger.exception(
                "PowerMem search failed; continuing without memory context."
            )
            return None

        context = _format_memory_context(result)
        return self._context_update(state, context)

    async def _abuild_memory_context_update(
        self,
        state: PowerMemState,
    ) -> PowerMemStateUpdate | None:
        query = _latest_message_text(state.get("messages", []), HumanMessage)
        if not query:
            return None

        try:
            search = getattr(self.memory, "search")
            result = search(
                query,
                user_id=self.user_id,
                agent_id=self.agent_id,
                run_id=self.run_id,
                filters=self.filters,
                limit=self.search_limit,
            )
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            if not self.fail_open:
                raise
            logger.exception(
                "PowerMem search failed; continuing without memory context."
            )
            return None

        context = _format_memory_context(result)
        return self._context_update(state, context)

    def _context_update(
        self,
        state: PowerMemState,
        context: str,
    ) -> PowerMemStateUpdate | None:
        messages = _without_powermem_context(
            state.get("messages", []),
            self.context_message_id,
        )
        if not context:
            if len(messages) == len(state.get("messages", [])):
                return None
            return {
                "messages": [RemoveMessage(id=REMOVE_ALL_MESSAGES), *messages],
                "powermem_context": "",
            }

        context_message = SystemMessage(
            content=context,
            id=self.context_message_id,
        )
        return {
            "messages": [
                RemoveMessage(id=REMOVE_ALL_MESSAGES),
                context_message,
                *messages,
            ],
            "powermem_context": context,
        }

    def _save_interaction(self, state: PowerMemState) -> None:
        if not self.save_interactions:
            return

        user_text, assistant_text = _interaction_text(state.get("messages", []))
        if not user_text or not assistant_text:
            return

        try:
            self.memory.add(
                _format_interaction(user_text, assistant_text),
                user_id=self.user_id,
                agent_id=self.agent_id,
                run_id=self.run_id,
                metadata={"source": "langchain_agent_interaction"},
                infer=False,
            )
        except Exception:
            if not self.fail_open:
                raise
            logger.exception("PowerMem write-back failed; agent result was preserved.")

    async def _asave_interaction(self, state: PowerMemState) -> None:
        if not self.save_interactions:
            return

        user_text, assistant_text = _interaction_text(state.get("messages", []))
        if not user_text or not assistant_text:
            return

        try:
            result = self.memory.add(
                _format_interaction(user_text, assistant_text),
                user_id=self.user_id,
                agent_id=self.agent_id,
                run_id=self.run_id,
                metadata={"source": "langchain_agent_interaction"},
                infer=False,
            )
            if inspect.isawaitable(result):
                await result
        except Exception:
            if not self.fail_open:
                raise
            logger.exception("PowerMem write-back failed; agent result was preserved.")


def _latest_message_text(
    messages: Sequence[BaseMessage],
    message_type: type[BaseMessage],
) -> str:
    for message in reversed(messages):
        if isinstance(message, message_type):
            return _content_to_text(message.content)
    return ""


def _interaction_text(messages: Sequence[BaseMessage]) -> tuple[str, str]:
    return (
        _latest_message_text(messages, HumanMessage),
        _latest_message_text(messages, AIMessage),
    )


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text is not None:
                    parts.append(str(text))
            elif item is not None:
                parts.append(str(item))
        return "\n".join(parts).strip()
    if content is None:
        return ""
    return str(content).strip()


def _format_memory_context(result: Any) -> str:
    if not isinstance(result, dict):
        return ""

    memories: list[str] = []
    for item in result.get("results", []):
        if not isinstance(item, dict):
            continue
        text = item.get("memory") or item.get("content")
        if text:
            memories.append(str(text).strip())

    if not memories:
        return ""

    lines = ["Relevant long-term memories from PowerMem:"]
    lines.extend(f"- {memory}" for memory in memories if memory)
    return "\n".join(lines)


def _without_powermem_context(
    messages: Sequence[BaseMessage],
    context_message_id: str,
) -> list[BaseMessage]:
    return [message for message in messages if message.id != context_message_id]


def _format_interaction(user_text: str, assistant_text: str) -> str:
    return f"User: {user_text}\nAssistant: {assistant_text}"
