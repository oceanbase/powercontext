"""PowerMem LangChain middleware — VLDB 2026 T1 implementation.

Integrates PowerMem long-term memory into LangChain v1 agents via the
AgentMiddleware extension point:

  before_agent  — searches PowerMem with the latest user message and injects
                  retrieved memories as a SystemMessage (fail-open on error).
  after_agent   — writes the user message and assistant reply back to PowerMem
                  when save_interactions=True.

Async paths (abefore_agent, aafter_agent) delegate to the sync implementations
since the PowerMem SDK surface is synchronous.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


class PowerMemState(AgentState):
    """Agent state extended with a transient memory context string."""

    powermem_context: NotRequired[str]


class PowerMemStateUpdate(TypedDict):
    """State update returned by memory-loading middleware hooks."""

    powermem_context: str


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """LangChain AgentMiddleware that wires PowerMem as a long-term memory layer.

    Args:
        memory:            PowerMem ``Memory`` instance (or any object with
                           ``search`` / ``add`` methods).
        user_id:           Explicit user identity passed to every PowerMem call.
                           Never read from global config.
        search_limit:      Maximum number of memories to retrieve per call.
        save_interactions: When True, the user message and assistant reply are
                           written back to PowerMem after each agent run.
    """

    state_schema = PowerMemState

    def __init__(
        self,
        *,
        memory: Any,
        user_id: str,
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

    def _last_human_text(self, messages: list[Any]) -> str:
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                return str(msg.content)
        return ""

    def _last_ai_text(self, messages: list[Any]) -> str:
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                return str(msg.content)
        return ""

    # ------------------------------------------------------------------
    # Middleware hooks
    # ------------------------------------------------------------------

    def before_agent(self, state: PowerMemState, runtime: Any) -> dict[str, Any] | None:
        """Search PowerMem and prepend retrieved memories to the message list."""
        messages = state.get("messages", [])
        query = self._last_human_text(messages)
        if not query:
            return None

        try:
            result = self._memory.search(
                query, user_id=self._user_id, limit=self._search_limit
            )
            memories = [r["memory"] for r in result.get("results", [])]
        except Exception:
            # Fail-open: a search error must not block the agent.
            return None

        if not memories:
            return None

        ctx = "\n".join(memories)
        # add_messages reducer appends the SystemMessage to the existing list.
        return {
            "messages": [SystemMessage(content=f"[Memory]\n{ctx}")],
            "powermem_context": ctx,
        }

    async def abefore_agent(
        self, state: PowerMemState, runtime: Any
    ) -> dict[str, Any] | None:
        """Async path — delegates to the synchronous implementation."""
        return self.before_agent(state, runtime)

    def after_agent(self, state: PowerMemState, runtime: Any) -> None:
        """Write the user message and assistant reply back to PowerMem."""
        if not self._save_interactions:
            return

        messages = state.get("messages", [])
        user_text = self._last_human_text(messages)
        ai_text = self._last_ai_text(messages)

        if user_text:
            self._memory.add(user_text, user_id=self._user_id, infer=False)
        if ai_text:
            self._memory.add(ai_text, user_id=self._user_id, infer=False)

    async def aafter_agent(self, state: PowerMemState, runtime: Any) -> None:
        """Async path — delegates to the synchronous implementation."""
        return self.after_agent(state, runtime)
