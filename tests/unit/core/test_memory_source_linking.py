"""Unit tests for the auto source-linking path in ``Memory.add()``.

These tests exercise the helpers
(`_maybe_create_source`, `_link_result_to_source`) and the skill
proxy methods directly on a stub ``Memory`` instance built via
``object.__new__`` -- this lets us verify the contract without standing up
the full Memory pipeline (vector store, LLM, etc.).
"""

import json
import pytest
from unittest.mock import MagicMock, call

from powermem.core.memory import Memory


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _make_memory_stub(source_store=None):
    """Build a minimal Memory instance with just the fields the helpers read."""
    mem = object.__new__(Memory)
    mem.source_store = source_store
    return mem


# --------------------------------------------------------------------------- #
# _maybe_create_source
# --------------------------------------------------------------------------- #


class TestMaybeCreateSource:
    def test_disabled_returns_none(self):
        mem = _make_memory_stub(source_store=None)
        assert mem._maybe_create_source("hello", user_id="u1") is None

    def test_empty_input_returns_none(self):
        mem = _make_memory_stub(source_store=MagicMock())
        assert mem._maybe_create_source("", user_id="u1") is None
        assert mem._maybe_create_source([], user_id="u1") is None
        assert mem._maybe_create_source(None, user_id="u1") is None
        # source_store.create_source should never be hit for empty inputs
        mem.source_store.create_source.assert_not_called()

    def test_string_input_is_text_source(self):
        store = MagicMock()
        store.create_source.return_value = {"id": 42}
        mem = _make_memory_stub(source_store=store)

        sid = mem._maybe_create_source("plain text", user_id="u1", agent_id="a1")

        assert sid == 42
        store.create_source.assert_called_once_with(
            source_type="text",
            content="plain text",
            metadata=None,
            user_id="u1",
            agent_id="a1",
            run_id=None,
            actor_id=None,
        )

    def test_full_scope_propagation(self):
        """All four scope IDs (user/agent/run/actor) must reach the store."""
        store = MagicMock()
        store.create_source.return_value = {"id": 100}
        mem = _make_memory_stub(source_store=store)

        sid = mem._maybe_create_source(
            "hello",
            user_id="u1",
            agent_id="a1",
            run_id="r1",
            actor_id="act1",
        )

        assert sid == 100
        kwargs = store.create_source.call_args.kwargs
        assert kwargs["user_id"] == "u1"
        assert kwargs["agent_id"] == "a1"
        assert kwargs["run_id"] == "r1"
        assert kwargs["actor_id"] == "act1"

    def test_non_dict_source_return_is_handled(self):
        """If a backend returns an object (not a dict), fetch ``.id`` via getattr
        instead of blowing up with AttributeError inside the try/except and
        producing a misleading log line."""
        class Fake:
            id = 55

        store = MagicMock()
        store.create_source.return_value = Fake()
        mem = _make_memory_stub(source_store=store)

        sid = mem._maybe_create_source("x", user_id="u1")
        assert sid == 55

    def test_list_input_is_conversation_source_as_json(self):
        store = MagicMock()
        store.create_source.return_value = {"id": 7}
        mem = _make_memory_stub(source_store=store)

        msgs = [{"role": "user", "content": "你好"}]
        sid = mem._maybe_create_source(msgs, user_id="u1", agent_id=None)

        assert sid == 7
        kwargs = store.create_source.call_args.kwargs
        assert kwargs["source_type"] == "conversation"
        # ensure_ascii=False should keep the CJK characters readable
        assert "你好" in kwargs["content"]
        assert json.loads(kwargs["content"]) == msgs
        assert kwargs["user_id"] == "u1"
        assert kwargs["agent_id"] is None

    def test_dict_input_is_conversation_source(self):
        store = MagicMock()
        store.create_source.return_value = {"id": 9}
        mem = _make_memory_stub(source_store=store)

        msg = {"role": "user", "content": "hi"}
        sid = mem._maybe_create_source(msg, user_id=None, agent_id="a1")

        assert sid == 9
        kwargs = store.create_source.call_args.kwargs
        assert kwargs["source_type"] == "conversation"
        assert json.loads(kwargs["content"]) == msg

    def test_non_serializable_input_is_swallowed(self):
        """bytes / other non-JSON types must not crash -- default=str is a fallback."""
        store = MagicMock()
        store.create_source.return_value = {"id": 1}
        mem = _make_memory_stub(source_store=store)

        class Weird:
            def __repr__(self):
                return "<weird>"

        # list with a non-serializable object; default=str should stringify it
        sid = mem._maybe_create_source([{"blob": Weird()}], user_id="u1")
        assert sid == 1
        content = store.create_source.call_args.kwargs["content"]
        # Not strictly required to be JSON-decodable, but it must be a str
        assert isinstance(content, str)

    def test_store_exception_is_swallowed(self):
        store = MagicMock()
        store.create_source.side_effect = RuntimeError("db down")
        mem = _make_memory_stub(source_store=store)

        sid = mem._maybe_create_source("hello", user_id="u1")
        assert sid is None  # do not propagate -- add() must stay unaffected

    def test_store_returns_none_means_no_source_id(self):
        store = MagicMock()
        store.create_source.return_value = None
        mem = _make_memory_stub(source_store=store)

        sid = mem._maybe_create_source("hello", user_id="u1")
        assert sid is None


# --------------------------------------------------------------------------- #
# _link_result_to_source
# --------------------------------------------------------------------------- #


class TestLinkResultToSource:
    def test_links_each_memory_in_result(self):
        store = MagicMock()
        store.link_memory.return_value = True
        mem = _make_memory_stub(source_store=store)

        result = {"results": [{"id": 101}, {"id": 102}, {"id": 103}]}
        mem._link_result_to_source(source_id=1, result=result)

        assert store.link_memory.call_count == 3
        store.link_memory.assert_has_calls(
            [call(1, 101), call(1, 102), call(1, 103)],
            any_order=False,
        )

    def test_skips_entries_without_id(self):
        store = MagicMock()
        mem = _make_memory_stub(source_store=store)

        result = {"results": [{"id": 10}, {"no_id": "x"}, {"id": None}, {"id": 20}]}
        mem._link_result_to_source(source_id=5, result=result)

        store.link_memory.assert_has_calls(
            [call(5, 10), call(5, 20)],
            any_order=False,
        )
        assert store.link_memory.call_count == 2

    def test_empty_result_is_noop(self):
        store = MagicMock()
        mem = _make_memory_stub(source_store=store)

        mem._link_result_to_source(source_id=1, result={"results": []})
        mem._link_result_to_source(source_id=1, result={})
        mem._link_result_to_source(source_id=1, result=None)

        store.link_memory.assert_not_called()

    def test_per_link_exception_is_swallowed(self):
        """A failing link must not stop subsequent links nor bubble out."""
        store = MagicMock()
        store.link_memory.side_effect = [RuntimeError("boom"), True]
        mem = _make_memory_stub(source_store=store)

        result = {"results": [{"id": 1}, {"id": 2}]}
        # Must not raise
        mem._link_result_to_source(source_id=99, result=result)
        assert store.link_memory.call_count == 2

    def test_supports_object_memories_with_id_attr(self):
        """Some code paths return objects (not dicts); .id should still work."""
        store = MagicMock()
        mem = _make_memory_stub(source_store=store)

        class Rec:
            def __init__(self, id):
                self.id = id

        result = {"results": [Rec(77), Rec(88)]}
        mem._link_result_to_source(source_id=3, result=result)

        store.link_memory.assert_has_calls([call(3, 77), call(3, 88)])


# --------------------------------------------------------------------------- #
# Source CRUD proxy
# --------------------------------------------------------------------------- #


class TestSourceCRUDProxy:
    def test_create_source_returns_none_when_disabled(self):
        mem = _make_memory_stub(source_store=None)
        assert mem.create_source(source_type="text", content="x") is None

    def test_create_source_delegates(self):
        store = MagicMock()
        store.create_source.return_value = {"id": 1}
        mem = _make_memory_stub(source_store=store)

        result = mem.create_source(
            source_type="conversation",
            content="hello",
            user_id="u1",
        )
        assert result == {"id": 1}
        store.create_source.assert_called_once()

    def test_get_source_returns_none_when_disabled(self):
        mem = _make_memory_stub(source_store=None)
        assert mem.get_source(1) is None

    def test_get_source_delegates(self):
        store = MagicMock()
        store.get_source.return_value = {"id": 1, "content": "x"}
        mem = _make_memory_stub(source_store=store)

        assert mem.get_source(1) == {"id": 1, "content": "x"}
        store.get_source.assert_called_once_with(1)

    def test_delete_source_returns_none_when_disabled(self):
        mem = _make_memory_stub(source_store=None)
        assert mem.delete_source(1) is None

    def test_delete_source_delegates(self):
        store = MagicMock()
        store.delete_source.return_value = True
        mem = _make_memory_stub(source_store=store)

        assert mem.delete_source(1) is True
        store.delete_source.assert_called_once_with(1)


# --------------------------------------------------------------------------- #
# Memory <-> source proxy
# --------------------------------------------------------------------------- #


class TestMemorySourceProxies:
    def test_link_memory_to_source_returns_none_when_disabled(self):
        mem = _make_memory_stub(source_store=None)
        assert mem.link_memory_to_source(1, 10) is None

    def test_link_memory_to_source_delegates(self):
        store = MagicMock()
        store.link_memory.return_value = True
        mem = _make_memory_stub(source_store=store)

        assert mem.link_memory_to_source(1, 10) is True
        store.link_memory.assert_called_once_with(1, 10)

    def test_unlink_memory_from_source_delegates(self):
        store = MagicMock()
        store.unlink_memory.return_value = True
        mem = _make_memory_stub(source_store=store)

        assert mem.unlink_memory_from_source(1, 10) is True
        store.unlink_memory.assert_called_once_with(1, 10)

    def test_get_sources_for_memory_returns_none_when_disabled(self):
        mem = _make_memory_stub(source_store=None)
        assert mem.get_sources_for_memory(10) is None

    def test_get_sources_for_memory_delegates(self):
        store = MagicMock()
        store.get_sources_for_memory.return_value = [{"id": 1}]
        mem = _make_memory_stub(source_store=store)

        assert mem.get_sources_for_memory(10) == [{"id": 1}]
        store.get_sources_for_memory.assert_called_once_with(10)


# --------------------------------------------------------------------------- #
# Skill <-> source proxy
# --------------------------------------------------------------------------- #


class TestSkillSourceProxies:
    def test_link_skill_to_source_returns_none_when_disabled(self):
        mem = _make_memory_stub(source_store=None)
        assert mem.link_skill_to_source(1, 10) is None

    def test_link_skill_to_source_delegates(self):
        store = MagicMock()
        store.link_skill.return_value = True
        mem = _make_memory_stub(source_store=store)

        assert mem.link_skill_to_source(7, 42) is True
        store.link_skill.assert_called_once_with(7, 42)

    def test_unlink_skill_from_source_delegates(self):
        store = MagicMock()
        store.unlink_skill.return_value = True
        mem = _make_memory_stub(source_store=store)

        assert mem.unlink_skill_from_source(7, 42) is True
        store.unlink_skill.assert_called_once_with(7, 42)

    def test_get_sources_for_skill_delegates(self):
        store = MagicMock()
        store.get_sources_for_skill.return_value = [{"id": 1}]
        mem = _make_memory_stub(source_store=store)

        assert mem.get_sources_for_skill(42) == [{"id": 1}]
        store.get_sources_for_skill.assert_called_once_with(42)

    def test_get_sources_for_skill_returns_none_when_disabled(self):
        mem = _make_memory_stub(source_store=None)
        assert mem.get_sources_for_skill(42) is None


# --------------------------------------------------------------------------- #
# Reverse queries
# --------------------------------------------------------------------------- #


class TestReverseQueriesProxies:
    def test_returns_none_when_disabled(self):
        mem = _make_memory_stub(source_store=None)
        assert mem.get_memories_for_source(1) is None
        assert mem.get_skills_for_source(1) is None

    def test_delegates_when_enabled(self):
        store = MagicMock()
        store.get_memories_for_source.return_value = [1, 2]
        store.get_skills_for_source.return_value = [3, 4]
        mem = _make_memory_stub(source_store=store)

        assert mem.get_memories_for_source(99) == [1, 2]
        assert mem.get_skills_for_source(99) == [3, 4]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
