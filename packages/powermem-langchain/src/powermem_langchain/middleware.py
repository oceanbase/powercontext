"""
PowerMem middleware for LangChain agents.

Provides PowerMemMiddleware — a callable class that wraps a LangChain runnable
(agent, chain, or LLM) with PowerMem-backed long-term memory.  On each
invocation it retrieves relevant memories, injects them into the model context,
and (optionally) saves the interaction back to PowerMem.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import BaseMessage, SystemMessage
from langchain_core.runnables import Runnable, RunnableConfig

logger = logging.getLogger(__name__)


class PowerMemMiddleware(BaseCallbackHandler):
    """LangChain middleware that integrates PowerMem as a long-term memory layer.

    Usage::

        agent = create_react_agent(model, tools)
        middleware = PowerMemMiddleware(memory=mem, user_id="alice")
        agent_with_mem = middleware(agent)
        result = agent_with_mem.invoke({"messages": [...]})

    Parameters
    ----------
    memory : powermem.Memory or powermem.AsyncMemory
        A PowerMem memory instance.
    user_id : str
        User identity scoping all memory operations.
    save_interactions : bool
        When *True* (default) write user/assistant exchanges back to PowerMem
        after each agent invocation.
    memory_limit : int
        Max number of memory items retrieved per query (default 5).
    """

    def __init__(
        self,
        memory: Any,
        user_id: str,
        *,
        save_interactions: bool = True,
        memory_limit: int = 5,
    ) -> None:
        super().__init__()
        self.memory = memory
        self.user_id = user_id
        self.save_interactions = save_interactions
        self.memory_limit = memory_limit

    # ------------------------------------------------------------------
    # Public hook methods (overridable for custom behaviour)
    # ------------------------------------------------------------------

    def retrieve_memories(self, query: str) -> str:
        """Search PowerMem for memories relevant to *query*.

        Returns a plain-text summary (one item per line, prefixed with ``- ``)
        or an empty string on failure / no results.
        """
        try:
            result = self.memory.search(query, user_id=self.user_id,
                                        limit=self.memory_limit)
            memories = result.get("results", [])
            if not memories:
                return ""
            lines: List[str] = []
            for mem in memories:
                content = mem.get("memory", mem.get("content", ""))
                if content:
                    lines.append(f"- {content}")
            return "\n".join(lines)
        except Exception:
            logger.warning("PowerMem memory retrieval failed", exc_info=True)
            return ""

    def inject_memory_context(
        self, messages: List[BaseMessage], query: str
    ) -> List[BaseMessage]:
        """Prepend a ``SystemMessage`` with relevant memories to *messages*."""
        memories_text = self.retrieve_memories(query)
        if memories_text:
            context = (
                "Here is some relevant information from past conversations:\n"
                f"{memories_text}"
            )
            return [SystemMessage(content=context)] + messages
        return messages

    def save_interaction(
        self, user_message: str, assistant_response: str
    ) -> None:
        """Write a single user↔assistant turn to PowerMem."""
        if not self.save_interactions:
            return
        try:
            content = f"User: {user_message}\nAssistant: {assistant_response}"
            self.memory.add(content, user_id=self.user_id)
        except Exception:
            logger.warning("Failed to save interaction to PowerMem",
                           exc_info=True)

    # ------------------------------------------------------------------
    # Input / output helpers
    # ------------------------------------------------------------------

    @staticmethod
    def extract_user_message(input_data: Any) -> Optional[str]:
        """Walk *input_data* and return the last ``human`` message content."""
        try:
            if isinstance(input_data, dict):
                msgs = (input_data.get("messages") or input_data.get("input")
                        or input_data.get("content"))
                return PowerMemMiddleware._last_human_text(msgs)
            if isinstance(input_data, str):
                return input_data
            return PowerMemMiddleware._last_human_text(input_data)
        except Exception:
            return None

    @staticmethod
    def extract_response(output_data: Any) -> Optional[str]:
        """Walk *output_data* and return the final assistant text."""
        try:
            if isinstance(output_data, dict):
                out = output_data.get("output", "")
                if isinstance(out, str) and out:
                    return out
                msgs = output_data.get("messages", [])
                if msgs:
                    return PowerMemMiddleware._msg_text(msgs[-1])
                return None
            if isinstance(output_data, str):
                return output_data
            return PowerMemMiddleware._msg_text(output_data)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _last_human_text(data: Any) -> Optional[str]:
        if data is None:
            return None
        if isinstance(data, str):
            return data
        if isinstance(data, list):
            for item in reversed(data):
                role = getattr(item, "type", None) or (
                    item.get("role") if isinstance(item, dict) else None
                )
                if role in ("human", "user"):
                    return PowerMemMiddleware._msg_text(item)
        return None

    @staticmethod
    def _msg_text(msg: Any) -> Optional[str]:
        if hasattr(msg, "content"):
            return str(msg.content or "")
        if isinstance(msg, dict):
            return str(msg.get("content", ""))
        return str(msg)

    # ------------------------------------------------------------------
    # Agent wrapping (the core middleware pattern)
    # ------------------------------------------------------------------

    def __call__(self, agent: Runnable) -> "_WrappedAgent":
        """Wrap *agent* so that memory is injected before each call and
        interactions are saved afterwards."""
        return _WrappedAgent(agent, self)


class _WrappedAgent:
    """Thin wrapper that injects PowerMem context into an agent runnable."""

    def __init__(self, agent: Runnable, mw: PowerMemMiddleware) -> None:
        self._agent = agent
        self._mw = mw

    def invoke(
        self,
        input: Any,
        config: Optional[RunnableConfig] = None,
        **kwargs: Any,
    ) -> Any:
        user_msg = self._mw.extract_user_message(input)
        modified = (
            self._inject_context(input, user_msg) if user_msg else input
        )
        config = self._with_callbacks(config)
        output = self._agent.invoke(modified, config, **kwargs)
        self._maybe_save(user_msg, output)
        return output

    async def ainvoke(
        self,
        input: Any,
        config: Optional[RunnableConfig] = None,
        **kwargs: Any,
    ) -> Any:
        user_msg = self._mw.extract_user_message(input)
        modified = (
            self._inject_context(input, user_msg) if user_msg else input
        )
        config = self._with_callbacks(config)
        output = await self._agent.ainvoke(modified, config, **kwargs)
        self._maybe_save(user_msg, output)
        return output

    # ------------------------------------------------------------------

    def _inject_context(self, input_data: Any, user_msg: str) -> Any:
        if isinstance(input_data, dict) and "messages" in input_data:
            msgs = self._mw.inject_memory_context(
                input_data["messages"], user_msg
            )
            return {**input_data, "messages": msgs}
        return input_data

    def _with_callbacks(
        self, config: Optional[RunnableConfig]
    ) -> RunnableConfig:
        config = config or {}
        cbs = list(config.get("callbacks") or [])
        cbs.append(self._mw)
        return {**config, "callbacks": cbs}

    def _maybe_save(self, user_msg: Optional[str], output: Any) -> None:
        if not user_msg or not self._mw.save_interactions:
            return
        resp = self._mw.extract_response(output)
        if resp:
            self._mw.save_interaction(user_msg, resp)
