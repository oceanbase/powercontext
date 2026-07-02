"""Unit tests for Snowflake ID precision handling in the MCP tool layer.

Guards against issue #1098: 64-bit Snowflake IDs exceed JavaScript's
``Number.MAX_SAFE_INTEGER`` (2^53 - 1). MCP tool inputs and outputs must
treat ``memory_id`` / ``id`` as strings end-to-end so V8-based clients
(e.g. Claude Code) cannot round them.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from powermem.mcp import server as mcp_server
from powermem.mcp.server import (
    _fmt,
    _stringify_ids,
    delete_memory,
    delete_memory_with_profile,
    get_memory_by_id,
    mcp,
    update_memory,
)

SNOWFLAKE = 725653563933458432
SNOWFLAKE_STR = "725653563933458432"


# ---------------------------------------------------------------------------
# A. Schema
# ---------------------------------------------------------------------------


def _tool_schema(tool_name: str) -> Dict[str, Any]:
    tools = asyncio.run(mcp.list_tools())
    for t in tools:
        if t.name == tool_name:
            return t.parameters
    raise AssertionError(f"tool {tool_name} not registered")


@pytest.mark.parametrize(
    "tool_name",
    ["get_memory_by_id", "update_memory", "delete_memory", "delete_memory_with_profile"],
)
def test_memory_id_schema_is_string(tool_name: str) -> None:
    """Scenario 1: memory_id declared as JSON string, not integer / anyOf."""
    props = _tool_schema(tool_name).get("properties", {})
    assert props["memory_id"]["type"] == "string"
    assert "anyOf" not in props["memory_id"]


def test_non_id_params_keep_original_types() -> None:
    """Scenario 2: limit stays integer, delete_profile stays boolean."""
    search_props = _tool_schema("search_memories").get("properties", {})
    assert search_props["limit"]["type"] == "integer"
    dmp_props = _tool_schema("delete_memory_with_profile").get("properties", {})
    assert dmp_props["delete_profile"]["type"] == "boolean"


# ---------------------------------------------------------------------------
# B. Input coercion
# ---------------------------------------------------------------------------


def test_get_memory_by_id_passes_int_to_memory() -> None:
    """Scenario 5: string memory_id coerced to int, no precision loss."""
    mock_mem = MagicMock()
    mock_mem.get.return_value = {"id": SNOWFLAKE, "content": "x"}
    with patch.object(mcp_server, "get_memory", return_value=mock_mem):
        get_memory_by_id(memory_id=SNOWFLAKE_STR)
    mock_mem.get.assert_called_once_with(memory_id=SNOWFLAKE, user_id=None, agent_id=None)


def test_update_memory_passes_int_to_memory() -> None:
    """Scenario 3."""
    mock_mem = MagicMock()
    mock_mem.update.return_value = {"id": SNOWFLAKE, "content": "x"}
    with patch.object(mcp_server, "get_memory", return_value=mock_mem):
        update_memory(memory_id=SNOWFLAKE_STR, content="x")
    mock_mem.update.assert_called_once_with(
        memory_id=SNOWFLAKE, content="x", user_id=None, agent_id=None, metadata=None
    )


def test_delete_memory_passes_int_to_memory() -> None:
    """Scenario 4."""
    mock_mem = MagicMock()
    mock_mem.delete.return_value = True
    with patch.object(mcp_server, "get_memory", return_value=mock_mem):
        delete_memory(memory_id=SNOWFLAKE_STR)
    mock_mem.delete.assert_called_once_with(memory_id=SNOWFLAKE, user_id=None, agent_id=None)


def test_delete_memory_with_profile_passes_int_to_user_memory() -> None:
    """Scenario 6."""
    mock_um = MagicMock()
    mock_um.delete.return_value = True
    with patch.object(mcp_server, "get_user_memory", return_value=mock_um):
        delete_memory_with_profile(memory_id=SNOWFLAKE_STR, user_id="u1")
    mock_um.delete.assert_called_once_with(
        memory_id=SNOWFLAKE, user_id="u1", agent_id=None, delete_profile=False
    )


@pytest.mark.parametrize(
    "tool_name, kwargs, target",
    [
        ("get_memory_by_id", {"memory_id": "abc"}, "get_memory"),
        ("update_memory", {"memory_id": "abc", "content": "x"}, "get_memory"),
        ("delete_memory", {"memory_id": "abc"}, "get_memory"),
        ("delete_memory_with_profile", {"memory_id": "abc", "user_id": "u1"}, "get_user_memory"),
    ],
)
def test_invalid_memory_id_returns_error(tool_name: str, kwargs: dict, target: str) -> None:
    """Scenario 7: non-numeric memory_id caught, returns success=False."""
    fn = {"get_memory_by_id": get_memory_by_id, "update_memory": update_memory,
          "delete_memory": delete_memory,
          "delete_memory_with_profile": delete_memory_with_profile}[tool_name]
    mock = MagicMock()
    with patch.object(mcp_server, target, return_value=mock):
        out = fn(**kwargs)
    payload = json.loads(out)
    assert payload["success"] is False
    assert "numeric string" in payload["error"]
    mock.assert_not_called()


# ---------------------------------------------------------------------------
# C. Output serialization
# ---------------------------------------------------------------------------


def test_fmt_stringifies_top_level_id() -> None:
    """Scenario 8."""
    out = json.loads(_fmt({"id": SNOWFLAKE, "content": "x"}))
    assert out["id"] == SNOWFLAKE_STR


def test_fmt_stringifies_id_in_results_list() -> None:
    """Scenario 9."""
    out = json.loads(_fmt({"results": [
        {"id": SNOWFLAKE, "memory": "a"},
        {"id": SNOWFLAKE + 1, "memory": "b"},
    ]}))
    assert out["results"][0]["id"] == SNOWFLAKE_STR
    assert out["results"][1]["id"] == str(SNOWFLAKE + 1)


def test_fmt_stringifies_memory_id_key_in_delete_response() -> None:
    """Scenario 10."""
    out = json.loads(_fmt({"success": True, "memory_id": SNOWFLAKE}))
    assert out["memory_id"] == SNOWFLAKE_STR


def test_fmt_leaves_non_id_ints_untouched() -> None:
    """Scenario 11."""
    out = json.loads(_fmt({"count": 5, "limit": 10, "threshold": 0.5}))
    assert out["count"] == 5
    assert out["limit"] == 10
    assert out["threshold"] == 0.5


def test_fmt_leaves_string_user_ids_untouched() -> None:
    """Scenario 12."""
    out = json.loads(_fmt({"user_id": "u1", "agent_id": "a1", "run_id": "r1"}))
    assert out == {"user_id": "u1", "agent_id": "a1", "run_id": "r1"}


def test_fmt_recursive_nested_id() -> None:
    """Scenario 13: id nested under results is stringified, sibling ints preserved."""
    out = json.loads(_fmt({"results": [{"id": SNOWFLAKE, "metadata": {"nesting": 3}}]}))
    assert out["results"][0]["id"] == SNOWFLAKE_STR
    assert out["results"][0]["metadata"]["nesting"] == 3


def test_fmt_skips_metadata_subdict_ids() -> None:
    """Scenario H(c): user metadata id-like fields are not rewritten."""
    out = json.loads(_fmt({"id": SNOWFLAKE, "metadata": {"id": 42, "tag": "x"}}))
    assert out["id"] == SNOWFLAKE_STR
    assert out["metadata"]["id"] == 42
    assert out["metadata"]["tag"] == "x"


def test_stringify_ids_handles_scalars_and_bool() -> None:
    """bool is a subclass of int — must not be stringified."""
    assert _stringify_ids(True) is True
    assert _stringify_ids(5) == 5
    assert _stringify_ids("x") == "x"
    assert _stringify_ids(None) is None


# ---------------------------------------------------------------------------
# D. End-to-end round-trip
# ---------------------------------------------------------------------------


def test_add_then_update_round_trip_preserves_id() -> None:
    """Scenario 14: add returns string id; passing it back to update keeps precision."""
    mock_mem = MagicMock()
    mock_mem.add.return_value = {"results": [{"id": SNOWFLAKE, "memory": "m"}]}
    mock_mem.update.return_value = {"id": SNOWFLAKE, "content": "m2"}

    with patch.object(mcp_server, "get_memory", return_value=mock_mem):
        add_out = json.loads(mcp_server.add_memory("m", user_id="u1"))
        returned_id = add_out["results"][0]["id"]
        assert returned_id == SNOWFLAKE_STR  # add path stringifies

        update_out = json.loads(update_memory(memory_id=returned_id, content="m2", user_id="u1"))
        assert update_out["id"] == SNOWFLAKE_STR

    mock_mem.update.assert_called_once_with(
        memory_id=SNOWFLAKE, content="m2", user_id="u1", agent_id=None, metadata=None
    )


# ---------------------------------------------------------------------------
# F. delete_memory_with_profile response
# ---------------------------------------------------------------------------


def test_delete_memory_with_profile_response_stringifies_memory_id() -> None:
    mock_um = MagicMock()
    mock_um.delete.return_value = True
    with patch.object(mcp_server, "get_user_memory", return_value=mock_um):
        out = json.loads(delete_memory_with_profile(
            memory_id=SNOWFLAKE_STR, user_id="u1", delete_profile=True
        ))
    assert out["success"] is True
    assert out["memory_id"] == SNOWFLAKE_STR
    assert out["user_id"] == "u1"
    assert out["profile_deleted"] is True
