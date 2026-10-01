"""LangChain middleware entry point for PowerMem.

The VLDB 2026 summer school branch intentionally provides only the public entry
point. Students are expected to replace this placeholder with a LangChain
middleware implementation that satisfies the package contract tests.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from powermem_langchain.retriever import SafeRetriever


class PowerMemState(AgentState):
    """State schema reserved for the PowerMem middleware implementation."""

    powermem_context: NotRequired[str]


class PowerMemStateUpdate(TypedDict):
    """State update returned by memory-loading middleware hooks."""

    powermem_context: str


def _content_to_text(content: Any) -> str:
    """Normalize a message content field (str or list of content blocks) to text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text" and block.get("text"):
                    parts.append(str(block["text"]))
        return "\n".join(parts)
    return str(content)


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """LangChain agent middleware that loads and persists PowerMem memories.

    - Before the agent runs it retrieves the most relevant memories for the
      latest user message and injects them into the conversation as a system
      message (fail-open: a search error never blocks the agent).
    - After the agent runs it persists the user/assistant interaction unless
      ``save_interactions=False``.
    """

    state_schema = PowerMemState

    def __init__(
        self,
        *,
        memory: Any,
        user_id: str | None = None,
        search_limit: int = 5,
        save_interactions: bool = True,
        enhanced: bool = True,
        retriever_kwargs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        self.memory = memory
        self.user_id = user_id
        self.search_limit = search_limit
        self.save_interactions = save_interactions
        # Best-effort embed capability discovery; None disables divergence
        # penalties but keeps the anchor guarantee and fail-back intact.
        embed_fn = getattr(memory, "embed", None) or getattr(
            getattr(memory, "embedder", None), "embed", None
        )
        retriever_kwargs = dict(retriever_kwargs or {})
        retriever_kwargs.setdefault("embed_fn", embed_fn)
        self.retriever = (
            SafeRetriever(
                memory,
                user_id=user_id,
                top_k=search_limit,
                **retriever_kwargs,
            )
            if enhanced
            else None
        )
        super().__init__(**kwargs)

    # ------------------------------------------------------------------ utils

    def _latest_user_text(self, state: PowerMemState) -> str:
        messages = state.get("messages", [])
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                return _content_to_text(message.content)
        return ""

    def _retrieve_context(self, query: str) -> str:
        """Retrieve relevant memories. Fail-open: never raise on search errors."""
        try:
            if self.retriever is not None:
                result = self.retriever.search(query)
            else:
                result = self.memory.search(
                    query=query,
                    user_id=self.user_id,
                    limit=self.search_limit,
                )
        except Exception:
            return ""
        if not isinstance(result, dict):
            return ""
        lines: list[str] = []
        for item in result.get("results", []):
            if isinstance(item, dict) and item.get("memory"):
                lines.append(str(item["memory"]))
        return "\n".join(lines)

    def _persist_interaction(self, state: PowerMemState) -> None:
        if not self.save_interactions:
            return
        messages = state.get("messages", [])
        user_text: str | None = None
        assistant_text: str | None = None
        for message in reversed(messages):
            text = _content_to_text(message.content)
            if not text:
                continue
            if assistant_text is None and isinstance(message, AIMessage):
                assistant_text = text
            elif user_text is None and isinstance(message, HumanMessage):
                user_text = text
            if user_text is not None and assistant_text is not None:
                break
        parts: list[str] = []
        if user_text:
            parts.append(f"User: {user_text}")
        if assistant_text:
            parts.append(f"Assistant: {assistant_text}")
        if not parts:
            return
        try:
            self.memory.add("\n".join(parts), user_id=self.user_id, infer=False)
        except Exception:
            # Persistence is best-effort; never break the agent because of it.
            pass

    # ------------------------------------------------------------- hooks

    def before_agent(self, state: PowerMemState, runtime) -> PowerMemStateUpdate | None:
        query = self._latest_user_text(state)
        context = self._retrieve_context(query)
        if not context:
            return None
        return {
            "powermem_context": context,
            "messages": [SystemMessage(content=f"Relevant memories:\n{context}")],
        }

    async def abefore_agent(
        self,
        state: PowerMemState,
        runtime,
    ) -> PowerMemStateUpdate | None:
        return self.before_agent(state, runtime)

    def after_agent(self, state: PowerMemState, runtime) -> None:
        self._persist_interaction(state)
        return None

    async def aafter_agent(self, state: PowerMemState, runtime) -> None:
        self._persist_interaction(state)
        return None
