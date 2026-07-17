"""LangChain middleware entry point for PowerMem.

Provides PowerMemMiddleware, a LangChain v1 AgentMiddleware that integrates
PowerMem as a long-term memory layer for agents.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

logger = logging.getLogger(__name__)


class PowerMemState(AgentState):
    """State schema for the PowerMem middleware.

    Carries the retrieved memory context through the agent graph so that
    downstream hooks (e.g. ``before_model``) can inject it into the prompt.
    """

    powermem_context: str


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """LangChain v1 middleware that connects an agent to PowerMem.

    On every agent invocation the middleware:

    * Searches PowerMem for memories relevant to the latest user message
      **before** the model is called.
    * Injects the retrieved memories into the conversation as a system
      message so the model can see them.
    * After the agent finishes, persists the user message and assistant
      response back to PowerMem (when ``save_interactions=True``).

    Parameters
    ----------
    memory:
        A PowerMem ``Memory`` instance (or any object exposing a compatible
        ``search(query, user_id, limit)`` and ``add(messages, user_id, infer)``
        API).
    user_id:
        Explicit user identity used for both search and persistence.
    search_limit:
        Maximum number of memories to retrieve per search (default 5).
    save_interactions:
        When ``True`` (the default), the user message and assistant response
        are written back to PowerMem after the agent completes.
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
        self._memory = memory
        self._user_id = user_id
        self._search_limit = search_limit
        self._save_interactions = save_interactions

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_latest_user_message(state: PowerMemState) -> str:
        """Return the content of the most recent ``HumanMessage`` in state."""
        messages = state.get("messages", [])
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                return str(msg.content)
        return ""

    def _search_memories(self, query: str) -> str:
        """Search PowerMem and return formatted memory lines.

        Fail-open: returns an empty string when the search fails so the
        agent can continue without interruption.
        """
        try:
            result = self._memory.search(
                query=query,
                user_id=self._user_id,
                limit=self._search_limit,
            )
        except Exception:
            logger.warning("PowerMem search failed; continuing without memories.", exc_info=True)
            return ""

        memories = result.get("results", []) if isinstance(result, dict) else []
        if not memories:
            return ""

        lines: list[str] = []
        for mem in memories:
            memory_text = mem.get("memory", "") if isinstance(mem, dict) else ""
            if memory_text:
                lines.append(f"- {memory_text}")
        return "\n".join(lines)

    def _persist_interaction(self, state: PowerMemState) -> None:
        """Persist user and assistant messages to PowerMem when enabled."""
        if not self._save_interactions:
            return

        messages = state.get("messages", [])
        user_parts: list[str] = []
        assistant_parts: list[str] = []

        for msg in messages:
            if isinstance(msg, HumanMessage):
                user_parts.append(str(msg.content))
            elif isinstance(msg, AIMessage):
                assistant_parts.append(str(msg.content))

        if not user_parts and not assistant_parts:
            return

        combined = (
            "User: "
            + " | ".join(user_parts)
            + "\nAssistant: "
            + " | ".join(assistant_parts)
        )
        try:
            self._memory.add(combined, user_id=self._user_id, infer=False)
        except Exception:
            logger.warning(
                "PowerMem add failed; interaction was not persisted.", exc_info=True
            )

    # ------------------------------------------------------------------
    # Middleware hooks
    # ------------------------------------------------------------------

    def before_agent(
        self, state: PowerMemState, runtime: Any
    ) -> dict[str, Any] | None:
        """Search PowerMem and inject relevant memories before the agent runs.

        Returns a state update that appends a ``SystemMessage`` with the
        retrieved memories so the model can reference them.
        """
        query = self._get_latest_user_message(state)
        if not query:
            return {"powermem_context": ""}

        context = self._search_memories(query)
        updates: dict[str, Any] = {"powermem_context": context}

        if context:
            system_msg = SystemMessage(
                content=f"Relevant information from previous conversations:\n{context}"
            )
            updates["messages"] = [system_msg]

        return updates

    async def abefore_agent(
        self, state: PowerMemState, runtime: Any
    ) -> dict[str, Any] | None:
        """Async variant of :meth:`before_agent`."""
        return self.before_agent(state, runtime)

    def after_agent(self, state: PowerMemState, runtime: Any) -> None:
        """Persist the completed interaction to PowerMem."""
        self._persist_interaction(state)

    async def aafter_agent(self, state: PowerMemState, runtime: Any) -> None:
        """Async variant of :meth:`after_agent`."""
        self.after_agent(state, runtime)
