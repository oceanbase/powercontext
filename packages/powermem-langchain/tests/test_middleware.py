"""
Tests for PowerMemMiddleware.

Verifies:
- Import from public entry point
- Memory injection into model context
- Interaction saving to PowerMem
- save_interactions=False disables saving
- Graceful degradation on PowerMem failures
- Async agent invocation
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from powermem_langchain import PowerMemMiddleware


# ---------------------------------------------------------------------------
# Mock PowerMem — in-memory stand-in for powermem.Memory
# ---------------------------------------------------------------------------

class MockPowerMem:
    """Simple in-memory PowerMem replacement for testing."""

    def __init__(self) -> None:
        self._records: List[Dict[str, Any]] = []
        self.fail_search: bool = False
        self.fail_add: bool = False

    def add(self, content: str, user_id: Optional[str] = None,
            **kwargs: Any) -> Dict[str, Any]:
        if self.fail_add:
            raise RuntimeError("PowerMem add failed")
        self._records.append({"content": content, "user_id": user_id,
                              "memory": content})
        return {"results": [{"id": len(self._records), "memory": content}]}

    def search(self, query: str, user_id: Optional[str] = None,
               limit: int = 5, **kwargs: Any) -> Dict[str, Any]:
        if self.fail_search:
            raise RuntimeError("PowerMem search failed")
        results = [
            {"memory": r["content"], "score": 0.9}
            for r in self._records
            if r.get("user_id") == user_id
        ]
        return {"results": results[:limit]}


# ---------------------------------------------------------------------------
# Mock agent — captures what it receives, returns canned output
# ---------------------------------------------------------------------------

class MockAgent:
    """Minimal agent stand-in with invoke / ainvoke."""

    def __init__(self) -> None:
        self.received_messages: List[BaseMessage] = []

    def invoke(self, input: Any,
               config: Optional[Any] = None,
               **kwargs: Any) -> Dict[str, Any]:
        msgs = self._extract_messages(input)
        self.received_messages = msgs
        return {
            "output": "This is a test response.",
            "messages": [AIMessage(content="This is a test response.")],
        }

    async def ainvoke(self, input: Any,
                      config: Optional[Any] = None,
                      **kwargs: Any) -> Dict[str, Any]:
        return self.invoke(input, config, **kwargs)

    @staticmethod
    def _extract_messages(input_data: Any) -> List[BaseMessage]:
        if isinstance(input_data, dict):
            return input_data.get("messages", [])
        if isinstance(input_data, list):
            return input_data
        return []


# ===================================================================
# Tests
# ===================================================================

class TestPowerMemMiddleware:

    # -- Import -------------------------------------------------------

    def test_import(self) -> None:
        assert PowerMemMiddleware is not None

    # -- Memory injection ---------------------------------------------

    def test_injects_memories_into_context(self) -> None:
        mem = MockPowerMem()
        mem.add("User prefers Python", user_id="alice")
        mem.add("User likes machine learning", user_id="alice")

        mw = PowerMemMiddleware(memory=mem, user_id="alice")
        agent = MockAgent()
        wrapped = mw(agent)

        wrapped.invoke({
            "messages": [HumanMessage(content="What do you know about me?")]
        })

        # Agent should have received the memory context
        assert len(agent.received_messages) > 1
        system_msgs = [
            m for m in agent.received_messages
            if getattr(m, "type", "") == "system"
        ]
        assert len(system_msgs) >= 1
        combined = "\n".join(str(m.content) for m in system_msgs)
        assert "Python" in combined

    def test_no_memories_when_none_exist(self) -> None:
        mem = MockPowerMem()
        mw = PowerMemMiddleware(memory=mem, user_id="alice")
        agent = MockAgent()
        wrapped = mw(agent)

        wrapped.invoke({
            "messages": [HumanMessage(content="Hello")]
        })

        # Original messages unchanged — no extra system message
        assert len(agent.received_messages) == 1

    # -- Interaction saving -------------------------------------------

    def test_saves_interaction_after_invoke(self) -> None:
        mem = MockPowerMem()
        mw = PowerMemMiddleware(memory=mem, user_id="alice")
        wrapped = mw(MockAgent())

        result = wrapped.invoke({
            "messages": [HumanMessage(content="Tell me about Python")]
        })

        assert result is not None
        assert len(mem._records) == 1
        saved = mem._records[0]["content"]
        assert "Python" in saved
        assert "test response" in saved

    def test_save_interactions_false_skips_saving(self) -> None:
        mem = MockPowerMem()
        mw = PowerMemMiddleware(memory=mem, user_id="alice",
                                save_interactions=False)
        wrapped = mw(MockAgent())

        wrapped.invoke({
            "messages": [HumanMessage(content="Hello")]
        })

        assert len(mem._records) == 0

    # -- Error resistance ---------------------------------------------

    def test_agent_runs_when_search_fails(self) -> None:
        mem = MockPowerMem()
        mem.fail_search = True
        mw = PowerMemMiddleware(memory=mem, user_id="alice")
        agent = MockAgent()
        wrapped = mw(agent)

        result = wrapped.invoke({
            "messages": [HumanMessage(content="Still working?")]
        })

        assert result is not None
        # Original messages only (memories failed)
        assert len(agent.received_messages) == 1

    def test_agent_runs_when_save_fails(self) -> None:
        mem = MockPowerMem()
        mem.fail_add = True
        mw = PowerMemMiddleware(memory=mem, user_id="alice")
        wrapped = mw(MockAgent())

        result = wrapped.invoke({
            "messages": [HumanMessage(content="Will this crash?")]
        })

        assert result is not None

    # -- Async path ---------------------------------------------------

    @pytest.mark.asyncio
    async def test_async_injects_memories(self) -> None:
        mem = MockPowerMem()
        mem.add("User likes async programming", user_id="bob")

        mw = PowerMemMiddleware(memory=mem, user_id="bob")
        agent = MockAgent()
        wrapped = mw(agent)

        await wrapped.ainvoke({
            "messages": [HumanMessage(content="What do you know?")]
        })

        assert len(agent.received_messages) > 1
        system_msgs = [
            m for m in agent.received_messages
            if getattr(m, "type", "") == "system"
        ]
        assert any("async" in str(m.content) for m in system_msgs)

    @pytest.mark.asyncio
    async def test_async_saves_interaction(self) -> None:
        mem = MockPowerMem()
        mw = PowerMemMiddleware(memory=mem, user_id="bob")
        wrapped = mw(MockAgent())

        await wrapped.ainvoke({
            "messages": [HumanMessage(content="Async hello")]
        })

        assert len(mem._records) == 1
        assert "Async hello" in mem._records[0]["content"]

    # -- Public API surface -------------------------------------------

    def test_has_public_hook_methods(self) -> None:
        mem = MockPowerMem()
        mw = PowerMemMiddleware(memory=mem, user_id="tester")

        assert hasattr(mw, "retrieve_memories")
        assert hasattr(mw, "inject_memory_context")
        assert hasattr(mw, "save_interaction")
        assert hasattr(mw, "extract_user_message")
        assert hasattr(mw, "extract_response")
