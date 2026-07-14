"""LangChain middleware entry point for PowerMem.

The VLDB 2026 summer school branch intentionally provides only the public entry
point. Students are expected to replace this placeholder with a LangChain
middleware implementation that satisfies the package contract tests.
"""

from __future__ import annotations

from typing import Any, NotRequired, TypedDict

from langchain.agents.middleware import AgentMiddleware, AgentState


class PowerMemState(AgentState):
    """可选的状态字典类型，包含可选的 powermem_context 字段。"""

    powermem_context: NotRequired[str]


class PowerMemStateUpdate(TypedDict):
    """可更新状态的字典类型，包含可选的 powermem_context 字段。"""

    powermem_context: str


import logging

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

logger = logging.getLogger(__name__)



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
        self.user_id = user_id
        self.search_limit = search_limit
        self.save_interactions = save_interactions

    # ------------------------------------------------------------------
    #  Hooks – before the agent runs (retrieve & inject memories)
    # ------------------------------------------------------------------

    def before_agent(self, state: PowerMemState, runtime) -> PowerMemStateUpdate | None:
      """在调用agent之前,从PowerMem中检索相关记忆,并将其作为系统消息插入到消息列表的最前面。"""
      context = self._retrieve_memories_sync(state) # 从memory获取
      if context is None:
          return None
      # 把memory作为系统消息插入到消息列表最前面
      state["messages"].insert(0, SystemMessage(content=context))
      return {"powermem_context": context}

    async def abefore_agent(self, state: PowerMemState, runtime) -> PowerMemStateUpdate | None:
      context = await self._retrieve_memories_async(state)
      if context is None:
          return None
      state["messages"].insert(0, SystemMessage(content=context))
      return {"powermem_context": context}

    # ------------------------------------------------------------------
    #  Hooks – after the agent finishes (save interaction)
    # ------------------------------------------------------------------

    def after_agent(self, state: PowerMemState, runtime) -> None:
        """在调用agent之后,将用户和AI的对话保存到PowerMem中。"""
        self._save_interaction_sync(state)

    async def aafter_agent(self, state: PowerMemState, runtime) -> None:
        """Async version of after_agent."""
        await self._save_interaction_async(state)

    # ------------------------------------------------------------------
    #  Internal – memory retrieval
    # ------------------------------------------------------------------

    def _retrieve_memories_sync(self, state: PowerMemState) -> str | None:
        """同步记忆检索。返回格式化的上下文或 None。"""
        user_msg = self._get_last_user_message(state) # 获取最近的用户信息
        if user_msg is None:
          return None

        # 使用用户消息内容作为查询词，在记忆库中搜索与当前用户相关的历史记忆，最多返回 search_limit 条
        try:
          result = self.memory.search(
            query=user_msg.content,
            user_id=self.user_id,
            limit=self.search_limit,
          )
        except Exception:
          logger.warning("PowerMem search failed", exc_info=True)
          return None
        # 提取记忆列表
        memories = result.get("results", []) if isinstance(result, dict) else []
        if not memories:
          return None
        # 将记忆列表格式化为文本块，返回给调用者
        return self._format_memories(memories)

    async def _retrieve_memories_async(self, state: PowerMemState) -> str | None:
        """异步记忆检索。返回格式化的上下文或 None。"""
        user_msg = self._get_last_user_message(state)
        if user_msg is None:
            return None

        try:
            # Use the async search if available; fall back to sync wrapped in a thread.
            if hasattr(self.memory, "asearch"):
                result = await self.memory.asearch(
                    query=user_msg.content,
                    user_id=self.user_id,
                    limit=self.search_limit,
                )
            else:
                import asyncio

                result = await asyncio.to_thread(
                    self.memory.search,
                    query=user_msg.content,
                    user_id=self.user_id,
                    limit=self.search_limit,
                )
        except Exception:
            logger.warning("PowerMem async search failed", exc_info=True)
            return None

        memories = result.get("results", []) if isinstance(result, dict) else []
        if not memories:
            return None

        return self._format_memories(memories)


    @staticmethod
    def _format_memories(memories) -> str:
      """将记忆列表格式化为文本块，返回给调用者。"""
      parts = []
      for m in memories:
          if isinstance(m, dict):
              # 记忆是字典，取 "memory" 字段
              parts.append(f"- {m.get('memory', str(m))}")
          elif hasattr(m, "content"):
              # 记忆是对象，有 .content 属性
              parts.append(f"- {m.content}")
          elif isinstance(m, str):
              # 记忆直接是字符串
              parts.append(f"- {m}")
          else:
              # 后备方案
              parts.append(f"- {str(m)}")
      lines = "\n".join(parts)
      return f"相关记忆：\n{lines}"

    # ------------------------------------------------------------------
    #  Internal – interaction saving
    # ------------------------------------------------------------------

    def _save_interaction_sync(self, state: PowerMemState) -> None:
      """保存用户和AI的对话到PowerMem中。"""
      if not self.save_interactions:
        return
      user_msg, ai_msg = self._get_last_user_ai_pair(state)
      if user_msg is None or ai_msg is None:
          return
      text = f"用户：{user_msg.content}\n助手：{ai_msg.content}"
      try:
          self.memory.add(messages=text, user_id=self.user_id)
      except Exception:
          logger.warning("PowerMem save failed", exc_info=True)

    async def _save_interaction_async(self, state: PowerMemState) -> None:
      """异步保存用户和AI的对话到PowerMem中。"""
      if not self.save_interactions:
          return
      user_msg, ai_msg = self._get_last_user_ai_pair(state)
      if user_msg is None or ai_msg is None:
          return
      text = f"用户：{user_msg.content}\n助手：{ai_msg.content}"
      try:
          if hasattr(self.memory, "aadd"):
              await self.memory.aadd(messages=text, user_id=self.user_id)
          else:
              import asyncio
              await asyncio.to_thread(
                  self.memory.add, messages=text, user_id=self.user_id
              )
      except Exception:
          logger.warning("PowerMem async save failed", exc_info=True)

    # ------------------------------------------------------------------
    #  Helpers – message extraction
    # ------------------------------------------------------------------

    @staticmethod
    def _get_last_user_message(state: PowerMemState) -> HumanMessage | None:
        """Return the most recent HumanMessage, or None."""
        for msg in reversed(state["messages"]):
            if isinstance(msg, HumanMessage):
                return msg
        return None

    @staticmethod
    def _get_last_ai_message(state: PowerMemState) -> AIMessage | None:
        """Return the most recent AIMessage, or None."""
        for msg in reversed(state["messages"]):
            if isinstance(msg, AIMessage):
                return msg
        return None

    @classmethod
    def _get_last_user_ai_pair(
        cls, state: PowerMemState
    ) -> tuple[HumanMessage | None, AIMessage | None]:
        """Return the last user message and the last AI message as a pair."""
        return cls._get_last_user_message(state), cls._get_last_ai_message(state)