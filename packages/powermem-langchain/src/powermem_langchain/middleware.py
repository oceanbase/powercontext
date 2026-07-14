"""LangChain middleware that backs a v1 agent with PowerMem long-term memory.

The middleware wires PowerMem into the LangChain ``create_agent`` lifecycle so
that relevant memories are retrieved before the model is called, injected into
the model's visible context, and new interactions are written back after the
agent run finishes.

Design -- each hook and the state field has exactly one job:

* ``before_agent``  -> retrieve memories for the latest user message and cache
  the formatted context in ``powermem_context``. Runs once per agent run, so
  memory is not re-fetched on every model call of a tool-using agent.
* ``wrap_model_call`` -> inject ``powermem_context`` into the model request's
  system message so the model actually sees the memories. Runs per model call.
* ``after_agent``   -> optionally write the user/assistant exchange back to
  PowerMem. Runs once at the end of the run.

Retrieval and write-back are fail-open by default (``fail_open=True``): a broken
memory layer logs a warning and never crashes the agent. Set ``fail_open=False``
to re-raise memory-layer errors instead. The memory identity is the explicit
``user_id`` constructor argument -- no global or implicit configuration is read.

Both synchronous (``Memory``) and asynchronous (``AsyncMemory``) PowerMem
backends are supported. The async hooks detect coroutine methods on the memory
object and ``await`` them; the sync hooks call the methods directly. Passing an
``AsyncMemory`` to a synchronous ``agent.invoke`` raises ``TypeError`` rather
than silently leaking an un-awaited coroutine -- use ``agent.ainvoke`` instead.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any, NotRequired, TypedDict

from langchain.agents.middleware import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

_logger = logging.getLogger(__name__)


class PowerMemState(AgentState):
    """Agent state carrying the PowerMem context for the current run.

    ``powermem_context`` holds the memory text produced by ``before_agent`` so
    that ``wrap_model_call`` can inject it without re-retrieving on every model
    call. It is ``str | None``: ``None`` means this run found no injectable
    memories (or retrieval failed fail-open), in which case ``wrap_model_call``
    injects nothing.

    ``before_agent`` overwrites ``powermem_context`` on every run -- including
    with ``None`` -- so the value never leaks from a previous run when a
    checkpointer replays the same thread. No private/ephemeral schema marker
    is used, which keeps the middleware off non-public framework internals.
    """

    powermem_context: NotRequired[str | None]


class PowerMemStateUpdate(TypedDict):
    """State update returned by ``before_agent``.

    ``powermem_context`` is ``None`` when no memories were retrieved, which
    clears any value left by a previous run on the same checkpointed thread.
    """

    powermem_context: str | None


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """LangChain v1 middleware that uses PowerMem as the agent's long-term memory.

    Args:
        memory: A PowerMem ``Memory`` (sync) or ``AsyncMemory`` (async)
            instance. Only its ``search`` and ``add`` methods are used. Sync
            memory works with both ``agent.invoke`` and ``agent.ainvoke``;
            async memory requires ``agent.ainvoke``.
        user_id: Explicit memory identity. Used verbatim for both retrieval and
            write-back; never derived from global/implicit configuration.
            ``None`` is allowed at construction time (deferred) but, if left
            unset, PowerMem itself will reject identity-scoped operations.
        search_limit: Maximum number of memories to retrieve per run. Must be a
            positive integer.
        save_interactions: When ``True`` (default), the user/assistant exchange
            is written back to PowerMem after the run.
        fail_open: When ``True`` (default), retrieval/write-back errors are
            logged at WARNING and swallowed so the agent keeps running. When
            ``False``, those errors are logged at ERROR and re-raised.
    """

    state_schema = PowerMemState

    def __init__(
        self,
        *,
        memory: Any,
        user_id: str | None = None,
        search_limit: int = 5,
        save_interactions: bool = True,
        fail_open: bool = True,
    ) -> None:
        # Fail fast on misconfiguration instead of degrading silently at runtime.
        if user_id is not None and not (isinstance(user_id, str) and user_id.strip()):
            raise ValueError("user_id must be a non-empty string, or None to defer")
        if not isinstance(search_limit, int) or search_limit <= 0:
            raise ValueError("search_limit must be a positive integer")
        self.memory = memory
        self.user_id = user_id
        self.search_limit = search_limit
        self.save_interactions = save_interactions
        self.fail_open = fail_open
        # Unknown keyword arguments are rejected by Python itself (no **kwargs
        # sink): a typo such as ``user_idd=`` surfaces immediately rather than
        # being silently ignored.

    # ------------------------------------------------------------------ #
    # Memory invocation.
    # ------------------------------------------------------------------ #

    def _is_async_method(self, method_name: str) -> bool:
        """Whether ``self.memory.<method_name>`` is an ``async def``.

        Uses ``inspect.iscoroutinefunction`` for duck-typed detection that works
        with ``AsyncMemory`` and any other awaitable-memory-like object, without
        importing or coupling to a concrete class.
        """
        return inspect.iscoroutinefunction(getattr(self.memory, method_name, None))

    async def _acall(self, method_name: str, *args: Any, **kwargs: Any) -> Any:
        """Invoke ``self.memory.<method_name>``, awaiting it if it is async.

        Handles three shapes: an ``async def`` method (awaited directly), a
        regular method that happens to return an awaitable (awaited), and a
        plain synchronous method (returned as-is). The first branch is the
        common ``AsyncMemory`` case; the awaitable fallback makes the helper
        robust to wrapped or callable-object memories.
        """
        method = getattr(self.memory, method_name)
        if inspect.iscoroutinefunction(method):
            return await method(*args, **kwargs)
        result = method(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    def _require_sync(self, method_name: str, hook: str) -> None:
        """Guard the synchronous hooks against an async memory object.

        Calling an ``async def`` method from a sync hook would return an
        un-awaited coroutine and silently do nothing, so fail loudly instead.
        """
        if self._is_async_method(method_name):
            raise TypeError(
                f"PowerMemMiddleware.{hook}: the memory object exposes async "
                f"'{method_name}'. Pass a synchronous PowerMem Memory, or use "
                "the async agent path (agent.ainvoke) with an AsyncMemory."
            )

    def _fail_open(self, exc: BaseException, where: str) -> bool:
        """Apply the fail-open/fail-hard policy to a memory-layer error.

        Returns ``True`` when the error should be suppressed (fail-open) and
        ``False`` when the caller should re-raise it (fail-hard). Either way the
        error is logged with ``exc_info`` so failures are never silently lost.
        """
        if self.fail_open:
            _logger.warning(
                "PowerMem %s failed; continuing (fail-open): %r",
                where,
                exc,
                exc_info=True,
            )
            return True
        _logger.error(
            "PowerMem %s failed; propagating (fail-hard): %r",
            where,
            exc,
            exc_info=True,
        )
        return False

    # ------------------------------------------------------------------ #
    # Retrieval + formatting (shared by sync and async paths).
    # ------------------------------------------------------------------ #

    def _retrieve(self, messages: list[BaseMessage]) -> str | None:
        """Sync retrieval: search PowerMem and format the result, fail-open."""
        self._require_sync("search", "before_agent")
        query = _latest_text(messages, HumanMessage)
        if not query:
            return None
        try:
            result = self.memory.search(
                query=query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
        except Exception as exc:
            if not self._fail_open(exc, "search"):
                raise
            return None
        return _format_results(result)

    async def _aretrieve(self, messages: list[BaseMessage]) -> str | None:
        """Async retrieval: await PowerMem search when the memory is async."""
        query = _latest_text(messages, HumanMessage)
        if not query:
            return None
        try:
            result = await self._acall(
                "search",
                query=query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
        except Exception as exc:
            if not self._fail_open(exc, "search"):
                raise
            return None
        return _format_results(result)

    @staticmethod
    def _inject(request: ModelRequest, context: str) -> ModelRequest:
        """Return a new ``ModelRequest`` with ``context`` appended to the system message."""
        block = {"type": "text", "text": context}
        existing = request.system_message
        if existing is None:
            new_system = SystemMessage(content=[block])
        else:
            new_system = SystemMessage(content=list(existing.content_blocks) + [block])
        return request.override(system_message=new_system)

    # ------------------------------------------------------------------ #
    # Write-back (shared formatting, sync/async variants).
    # ------------------------------------------------------------------ #

    def _writeback(self, messages: list[BaseMessage]) -> None:
        """Sync write-back: persist the exchange, gated + fail-open."""
        self._require_sync("add", "after_agent")
        if not self.save_interactions:
            return
        text = _build_writeback_text(messages)
        if text is None:
            return
        try:
            self.memory.add(text, user_id=self.user_id, infer=False)
        except Exception as exc:
            if not self._fail_open(exc, "write-back"):
                raise

    async def _awriteback(self, messages: list[BaseMessage]) -> None:
        """Async write-back: await PowerMem add when the memory is async."""
        if not self.save_interactions:
            return
        text = _build_writeback_text(messages)
        if text is None:
            return
        try:
            await self._acall("add", text, user_id=self.user_id, infer=False)
        except Exception as exc:
            if not self._fail_open(exc, "write-back"):
                raise

    # ------------------------------------------------------------------ #
    # Sync hooks.
    # ------------------------------------------------------------------ #

    def before_agent(self, state: PowerMemState, runtime) -> PowerMemStateUpdate:
        # Always overwrite (including with None) so a previous run's context
        # cannot leak when a checkpointer replays the same thread.
        context = self._retrieve(state.get("messages", []))
        return {"powermem_context": context}

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        context = request.state.get("powermem_context")
        if context:
            request = self._inject(request, context)
        return handler(request)

    def after_agent(self, state: PowerMemState, runtime) -> None:
        self._writeback(state.get("messages", []))

    # ------------------------------------------------------------------ #
    # Async hooks.
    #
    # ``agent.ainvoke`` runs these. They work with both an ``AsyncMemory``
    # (awaited via ``_acall``) and a synchronous ``Memory`` (``_acall`` calls
    # the method directly and returns the result, since an in-process store
    # does not block). Callers wanting non-blocking I/O against a remote
    # PowerMem should pass an ``AsyncMemory``.
    # ------------------------------------------------------------------ #

    async def abefore_agent(self, state: PowerMemState, runtime) -> PowerMemStateUpdate:
        # Always overwrite (including with None) so a previous run's context
        # cannot leak when a checkpointer replays the same thread.
        context = await self._aretrieve(state.get("messages", []))
        return {"powermem_context": context}

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        context = request.state.get("powermem_context")
        if context:
            request = self._inject(request, context)
        return await handler(request)

    async def aafter_agent(self, state: PowerMemState, runtime) -> None:
        await self._awriteback(state.get("messages", []))


# ---------------------------------------------------------------------- #
# Message + result helpers (module-private).
# ---------------------------------------------------------------------- #


def _as_text(content: Any) -> str | None:
    """Coerce a message ``content`` (str or list of blocks) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        return "".join(parts) or None
    return None


def _latest_text(messages: list[BaseMessage], message_type: type[BaseMessage]) -> str | None:
    """Text of the most recent message of ``message_type``, or ``None``."""
    for message in reversed(messages):
        if isinstance(message, message_type):
            return _as_text(message.content)
    return None


def _first_text(messages: list[BaseMessage], message_type: type[BaseMessage]) -> str | None:
    """Text of the first message of ``message_type``, or ``None``."""
    for message in messages:
        if isinstance(message, message_type):
            return _as_text(message.content)
    return None


def _format_results(result: Any) -> str | None:
    """Turn a PowerMem search result into an injectable context string.

    Returns ``None`` when there is nothing to inject (no results / empty text),
    so the caller can skip injection entirely. Malformed items (non-dict,
    missing keys, non-string values) are skipped rather than crashing.
    """
    results = (result or {}).get("results", []) if isinstance(result, dict) else []
    lines: list[str] = []
    for item in results:
        text = item.get("memory") if isinstance(item, dict) else None
        if not text:
            text = item.get("content") if isinstance(item, dict) else None
        if isinstance(text, str) and text.strip():
            lines.append(f"- {text.strip()}")
    if not lines:
        return None
    return "Relevant memories from prior interactions:\n" + "\n".join(lines)


def _build_writeback_text(messages: list[BaseMessage]) -> str | None:
    """Build the interaction text to write back, or ``None`` if nothing to store."""
    human = _first_text(messages, HumanMessage)
    assistant = _latest_text(messages, AIMessage)
    if human is None and assistant is None:
        return None
    parts: list[str] = []
    if human is not None:
        parts.append(f"User: {human}")
    if assistant is not None:
        parts.append(f"Assistant: {assistant}")
    return "\n".join(parts)
