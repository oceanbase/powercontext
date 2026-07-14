"""为LangChain agent提供PowerMem长期记忆能力的中间件"""

from __future__ import annotations

import asyncio
import inspect
import logging
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

_CONTEXT_HEADER = "Relevant long-term memory for this user:"
_CONTEXT_GUIDANCE = "Use this memory only when relevant to the current request."


class PowerMemState(AgentState):
    """扩展agent状态，用于保存从PowerMem检索出的上下文"""

    powermem_context: NotRequired[str]


class PowerMemStateUpdate(TypedDict):
    """记忆加载hook返回的状态更新。"""

    powermem_context: str


def _content_text(content: Any) -> str:
    """从LangChain消息内容中提取文本部分"""
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str): # 普通字符串
                parts.append(block)
            elif isinstance(block, Mapping): # 列表
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part.strip() for part in parts if part.strip())

    return ""


def _latest_message_text(
    messages: Sequence[Any],
    message_type: type[BaseMessage],
    role: str,
) -> str:
    """从标准消息对象或字典消息中查找最新的指定角色消息"""
    for message in reversed(messages):
        if isinstance(message, message_type):
            return _content_text(message.content)
        if isinstance(message, Mapping) and message.get("role") == role:
            return _content_text(message.get("content"))
    return ""


def _format_search_result(result: Any) -> str:
    """把PowerMem检索结果格式化为模型可见的上下文"""
    if isinstance(result, Mapping):
        items = result.get("results", [])
    elif isinstance(result, Sequence) and not isinstance(result, (str, bytes)):
        items = result
    else:
        return ""

    memories: list[str] = []
    seen_memories: set[str] = set()
    for item in items:
        if isinstance(item, Mapping):
            value = item.get("memory") or item.get("content")
            if isinstance(value, str) and value.strip():
                normalized = value.strip()
                # set仅用于判断是否重复，list负责保留检索结果的原始顺序
                if normalized not in seen_memories:
                    seen_memories.add(normalized)
                    memories.append(normalized)

    if not memories:
        return ""

    bullets = "\n".join(f"- {memory}" for memory in memories)
    return f"{_CONTEXT_HEADER}\n{bullets}\n\n{_CONTEXT_GUIDANCE}"


async def _call_async(method: Callable[..., Any], /, **kwargs: Any) -> Any:
    """在异步路径中安全调用PowerMem的异步或同步方法"""
    if inspect.iscoroutinefunction(method):
        return await method(**kwargs)

    result = await asyncio.to_thread(method, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """为agent增加PowerMem记忆检索和交互写回能力

    记忆检索采用fail-open策略：记忆服务不可用时不阻止agent回答
    写回发生在agent成功运行之后，因此同样采用fail-open策略
    """

    state_schema = PowerMemState

    def __init__(
        self,
        *,
        memory: Any,
        user_id: str,
        search_limit: int = 5,
        save_interactions: bool = True,
    ) -> None:
        if not user_id or not user_id.strip():
            raise ValueError("user_id must be a non-empty string")
        if search_limit < 1:
            raise ValueError("search_limit must be at least 1")

        self.memory = memory
        self.user_id = user_id
        self.search_limit = search_limit
        self.save_interactions = save_interactions

    def before_agent(
        self,
        state: PowerMemState,
        runtime: Any,
    ) -> PowerMemStateUpdate:
        """在同步agent开始运行前检索一次相关记忆"""
        query = _latest_message_text(state["messages"], HumanMessage, "user")
        if not query:
            return {"powermem_context": ""}

        try:
            result = self.memory.search(
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
            if inspect.isawaitable(result):
                result = asyncio.run(result)
            return {"powermem_context": _format_search_result(result)}
        except Exception:
            logger.warning("PowerMem search failed; continuing without memory", exc_info=True)
            return {"powermem_context": ""}

    async def abefore_agent(
        self,
        state: PowerMemState,
        runtime: Any,
    ) -> PowerMemStateUpdate:
        """在异步agent开始运行前检索一次相关记忆"""
        query = _latest_message_text(state["messages"], HumanMessage, "user")
        if not query:
            return {"powermem_context": ""}

        try:
            result = await _call_async(
                self.memory.search,
                query=query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
            return {"powermem_context": _format_search_result(result)}
        except Exception:
            logger.warning("PowerMem search failed; continuing without memory", exc_info=True)
            return {"powermem_context": ""}

    @staticmethod
    def _request_with_memory(request: ModelRequest) -> ModelRequest:
        """把检索到的记忆追加到请求原有的系统消息中"""
        context = request.state.get("powermem_context", "")
        if not context:
            return request

        # 调用方可能没有为agent配置系统消息，此时从空内容开始构造
        # 如果已有系统消息，则保留原内容，只在末尾追加PowerMem上下文
        if request.system_message is None:
            content_blocks = []
        else:
            content_blocks = list(request.system_message.content_blocks)
        content_blocks.append({"type": "text", "text": context})
        return request.override(system_message=SystemMessage(content=content_blocks))

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        """为agent运行期间的每次同步模型调用注入记忆"""
        return handler(self._request_with_memory(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        """为agent运行期间的每次异步模型调用注入记忆"""
        return await handler(self._request_with_memory(request))

    def _interaction(self, state: PowerMemState) -> list[dict[str, str]] | None:
        """提取最新用户请求和最终助手回复"""
        messages = state["messages"]
        user_text = _latest_message_text(messages, HumanMessage, "user")
        assistant_text = _latest_message_text(messages, AIMessage, "assistant")
        if not user_text or not assistant_text:
            return None
        return [
            {"role": "user", "content": user_text},
            {"role": "assistant", "content": assistant_text},
        ]

    def after_agent(self, state: PowerMemState, runtime: Any) -> None:
        """启用写回时，保存已完成的同步交互"""
        if not self.save_interactions:
            return None

        interaction = self._interaction(state)
        if interaction is None:
            return None

        try:
            result = self.memory.add(
                interaction,
                user_id=self.user_id,
                infer=False,
            )
            if inspect.isawaitable(result):
                asyncio.run(result)
        except Exception:
            logger.warning("PowerMem write-back failed; agent result is unchanged", exc_info=True)
        return None

    async def aafter_agent(self, state: PowerMemState, runtime: Any) -> None:
        """启用写回时，保存已完成的异步交互"""
        if not self.save_interactions:
            return None

        interaction = self._interaction(state)
        if interaction is None:
            return None

        try:
            await _call_async(
                self.memory.add,
                messages=interaction,
                user_id=self.user_id,
                infer=False,
            )
        except Exception:
            logger.warning("PowerMem write-back failed; agent result is unchanged", exc_info=True)
        return None
