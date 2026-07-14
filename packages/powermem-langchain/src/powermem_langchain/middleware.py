"""LangChain middleware entry point for PowerMem."""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage


class PowerMemState(AgentState):
    powermem_context: NotRequired[str]


class PowerMemStateUpdate(TypedDict):
    powermem_context: NotRequired[str]
    messages: NotRequired[list[BaseMessage]]


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
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
        super().__init__(**kwargs)
        self.memory = memory
        self.user_id = user_id or "default"
        self.search_limit = search_limit
        self.save_interactions = save_interactions

    def before_agent(self, state: PowerMemState, runtime) -> PowerMemStateUpdate | None:
        latest_user_message = self._latest_message_text(state, HumanMessage)
        if not latest_user_message:
            return None

        try:
            result = self.memory.search(
                latest_user_message,
                user_id=self.user_id,
                limit=self.search_limit,
            )
        except Exception:
            return None

        memories = self._extract_memory_texts(result)
        if not memories:
            return None

        context = "Relevant long-term memories:\n" + "\n".join(
            f"- {memory}" for memory in memories
        )

        return {
            "powermem_context": context,
            "messages": [SystemMessage(content=context)],
        }

    async def abefore_agent(
        self,
        state: PowerMemState,
        runtime,
    ) -> PowerMemStateUpdate | None:
        return self.before_agent(state, runtime)

    def after_agent(self, state: PowerMemState, runtime) -> None:
        if not self.save_interactions:
            return

        user_text = self._latest_message_text(state, HumanMessage)
        assistant_text = self._latest_message_text(state, AIMessage)

        if not user_text or not assistant_text:
            return

        interaction = f"User: {user_text}\nAssistant: {assistant_text}"

        try:
            self.memory.add(interaction, user_id=self.user_id, infer=False)
        except Exception:
            return

    @classmethod
    def _latest_message_text(
        cls,
        state: PowerMemState,
        message_type: type[BaseMessage],
    ) -> str:
        for message in reversed(state.get("messages", [])):
            if isinstance(message, message_type):
                return cls._content_to_text(message.content)
        return ""

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if isinstance(content, str):
            return content

        if isinstance(content, list):
            parts = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict):
                    parts.append(str(item.get("text") or item.get("content") or item))
                else:
                    parts.append(str(item))
            return "\n".join(parts)

        return str(content)

    @classmethod
    def _extract_memory_texts(cls, result: Any) -> list[str]:
        if isinstance(result, dict):
            items = result.get("results", [])
        elif isinstance(result, list):
            items = result
        else:
            items = []

        memories: list[str] = []
        for item in items:
            if isinstance(item, dict):
                text = item.get("memory") or item.get("content") or item.get("text")
            else:
                text = getattr(item, "memory", None) or getattr(item, "content", None)

            if text:
                memories.append(cls._content_to_text(text))

        return memories
