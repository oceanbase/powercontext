#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
PowerMem MCP Server

MCP server based on FastMCP framework, supporting three transport methods:
stdio, sse, and streamable-http.

Provides 13 tools for memory and user profile management:
- 7 core memory tools: add, search, get, update, delete, delete_all, list
- 6 user profile tools: add_with_profile, search_with_profile, get_profile,
  list_profiles, delete_profile, delete_memory_with_profile

Requires: pip install 'powermem[mcp]'
"""

import sys as _sys


def _require_mcp_deps() -> None:
    missing = []
    try:
        import fastmcp  # noqa: F401
    except ImportError:
        missing.append("fastmcp")
    if missing:
        _sys.stderr.write(
            f"Missing dependencies: {', '.join(missing)}.\n"
            "Run: pip install 'powermem[mcp]'\n"
        )
        _sys.exit(1)


_require_mcp_deps()

import json
import sys
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Union

from fastmcp import FastMCP

from powermem import auto_config, create_memory
from powermem.user_memory import UserMemory

# ============================================================================
# MCP Server instance
# ============================================================================

mcp = FastMCP("PowerMem MCP Server")

# Singletons (lazy init)
_memory_instance = None
_user_memory_instance = None


def get_memory():
    """Return shared Memory instance, creating it on first call."""
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = create_memory()
    return _memory_instance


def get_user_memory():
    """Return shared UserMemory instance, creating it on first call."""
    global _user_memory_instance
    if _user_memory_instance is None:
        config = auto_config()
        _user_memory_instance = UserMemory(config=config)
    return _user_memory_instance


# ============================================================================
# Helpers
# ============================================================================

def _convert_datetime(obj: Any) -> Any:
    """Recursively convert datetime/date objects to ISO strings."""
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _convert_datetime(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_convert_datetime(i) for i in obj]
    if isinstance(obj, tuple):
        return tuple(_convert_datetime(i) for i in obj)
    return obj


class _DateTimeEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)


def _fmt(data: Any) -> str:
    """Serialize memory result to a JSON string safe for LLM consumption."""
    return json.dumps(_convert_datetime(data), ensure_ascii=False, indent=2, cls=_DateTimeEncoder)


def _normalise_messages(messages: Union[str, Dict, List[Dict]]) -> Union[str, List[Dict], None]:
    """
    Validate and normalise messages into the format Memory.add() expects.
    Returns None (with error dict set) if messages are empty/invalid.
    """
    if not messages:
        return None
    if isinstance(messages, str):
        return messages if messages.strip() else None
    if isinstance(messages, list):
        valid = []
        for msg in messages:
            if isinstance(msg, dict):
                content = msg.get("content", "")
                if content and str(content).strip():
                    valid.append(msg if "role" in msg else {**msg, "role": "user"})
            elif isinstance(msg, str) and msg.strip():
                valid.append({"role": "user", "content": msg})
        return valid if valid else None
    return messages


# ============================================================================
# Core memory tools (7)
# ============================================================================

@mcp.tool()
def add_memory(
    messages: Union[str, Dict, List[Dict]],
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    infer: bool = True,
) -> str:
    """
    Add new memory to storage.

    Args:
        messages: Memory content — string, a single ``{"role", "content"}`` dict,
                  or a list of such dicts.
        user_id: User identifier.
        agent_id: Agent identifier.
        run_id: Run/session identifier.
        metadata: Arbitrary key-value metadata.
        infer: Use intelligent processing (default True).

    Returns:
        JSON string with the result of the add operation.

    Examples::

        add_memory("User likes Python", user_id="u1")
        add_memory([{"role": "user", "content": "I like watermelon"}], user_id="u1")
    """
    normed = _normalise_messages(messages)
    if normed is None:
        return json.dumps({"success": False, "error": "messages cannot be empty"}, ensure_ascii=False)
    try:
        result = get_memory().add(
            messages=normed,
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            metadata=metadata,
            infer=infer,
        )
        return _fmt(result)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def search_memories(
    query: str,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    limit: int = 10,
    threshold: Optional[float] = None,
    filters: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Search memories by semantic similarity.

    Args:
        query: Natural-language search query.
        user_id: User identifier.
        agent_id: Agent identifier.
        run_id: Run/session identifier.
        limit: Maximum results to return (default 10).
        threshold: Minimum similarity score 0.0–1.0.
        filters: Metadata filters.

    Returns:
        JSON string with matched memories.
    """
    result = get_memory().search(
        query=query,
        user_id=user_id,
        agent_id=agent_id,
        run_id=run_id,
        limit=limit,
        threshold=threshold,
        filters=filters,
    )
    return _fmt(result)


@mcp.tool()
def get_memory_by_id(
    memory_id: int,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> str:
    """
    Retrieve a single memory by its ID.

    Args:
        memory_id: Numeric memory ID.
        user_id: User identifier.
        agent_id: Agent identifier.

    Returns:
        JSON string with the memory record, or an error if not found.
    """
    result = get_memory().get(memory_id=memory_id, user_id=user_id, agent_id=agent_id)
    if result is None:
        return _fmt({"error": f"Memory {memory_id} not found"})
    return _fmt(result)


@mcp.tool()
def update_memory(
    memory_id: int,
    content: str,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Update the content of an existing memory.

    Args:
        memory_id: Numeric memory ID.
        content: New content string.
        user_id: User identifier.
        agent_id: Agent identifier.
        metadata: Metadata to update.

    Returns:
        JSON string with the update result.
    """
    result = get_memory().update(
        memory_id=memory_id,
        content=content,
        user_id=user_id,
        agent_id=agent_id,
        metadata=metadata,
    )
    return _fmt(result)


@mcp.tool()
def delete_memory(
    memory_id: int,
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> str:
    """
    Delete a single memory by ID.

    Args:
        memory_id: Numeric memory ID.
        user_id: User identifier.
        agent_id: Agent identifier.

    Returns:
        JSON string with ``{"success": bool, "memory_id": int}``.
    """
    success = get_memory().delete(memory_id=memory_id, user_id=user_id, agent_id=agent_id)
    return _fmt({"success": success, "memory_id": memory_id})


@mcp.tool()
def delete_all_memories(
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
) -> str:
    """
    Delete all memories matching the given scope.

    Args:
        user_id: User identifier.
        agent_id: Agent identifier.
        run_id: Run/session identifier.

    Returns:
        JSON string with ``{"success": bool}``.
    """
    success = get_memory().delete_all(user_id=user_id, agent_id=agent_id, run_id=run_id)
    return _fmt({"success": success})


@mcp.tool()
def list_memories(
    user_id: Optional[str] = None,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    filters: Optional[Dict[str, Any]] = None,
) -> str:
    """
    List memories with optional pagination.

    Args:
        user_id: User identifier.
        agent_id: Agent identifier.
        run_id: Run/session identifier.
        limit: Maximum records to return (default 100).
        offset: Pagination offset (default 0).
        filters: Metadata filters.

    Returns:
        JSON string with the list of memories.
    """
    result = get_memory().get_all(
        user_id=user_id,
        agent_id=agent_id,
        run_id=run_id,
        limit=limit,
        offset=offset,
    )
    return _fmt(result)


# ============================================================================
# User profile tools (6)
# ============================================================================

@mcp.tool()
def add_memory_with_profile(
    messages: Union[str, Dict, List[Dict]],
    user_id: str,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
    infer: bool = True,
    profile_type: str = "content",
    custom_topics: Optional[str] = None,
    strict_mode: bool = False,
) -> str:
    """
    Add memory and extract user profile from the conversation.

    Stores messages as memories **and** runs LLM-based profile extraction,
    saving structured or unstructured profile data for the user.

    Args:
        messages: Conversation content (string, dict, or list of dicts).
        user_id: User identifier (required).
        agent_id: Agent identifier.
        run_id: Run/session identifier.
        metadata: Arbitrary metadata.
        infer: Intelligent processing (default True).
        profile_type: ``"content"`` for free-text profile (default) or
                      ``"topics"`` for structured JSON topics.
        custom_topics: JSON string defining topic schema; only used when
                       ``profile_type="topics"``.
                       Format: ``{"main_topic": {"sub_topic": "description"}}``.
        strict_mode: Restrict extraction to provided topics only (default False).

    Returns:
        JSON string with memory add results and extracted profile data.
    """
    normed = _normalise_messages(messages)
    if normed is None:
        return json.dumps({"success": False, "error": "messages cannot be empty"}, ensure_ascii=False)
    try:
        result = get_user_memory().add(
            messages=normed,
            user_id=user_id,
            agent_id=agent_id,
            run_id=run_id,
            metadata=metadata,
            infer=infer,
            profile_type=profile_type,
            custom_topics=custom_topics,
            strict_mode=strict_mode,
        )
        return _fmt(result)
    except Exception as exc:
        return json.dumps({"success": False, "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def search_memories_with_profile(
    query: str,
    user_id: str,
    agent_id: Optional[str] = None,
    run_id: Optional[str] = None,
    limit: int = 10,
    threshold: Optional[float] = None,
    filters: Optional[Dict[str, Any]] = None,
    add_profile: bool = True,
) -> str:
    """
    Search memories and optionally include the user's profile.

    Args:
        query: Search query text.
        user_id: User identifier (required).
        agent_id: Agent identifier.
        run_id: Run/session identifier.
        limit: Maximum results (default 10).
        threshold: Similarity threshold 0.0–1.0.
        filters: Metadata filters.
        add_profile: Append profile data to results (default True).

    Returns:
        JSON string with memories and, when ``add_profile=True``, profile fields.
    """
    result = get_user_memory().search(
        query=query,
        user_id=user_id,
        agent_id=agent_id,
        run_id=run_id,
        limit=limit,
        threshold=threshold,
        filters=filters,
        add_profile=add_profile,
    )
    return _fmt(result)


@mcp.tool()
def get_user_profile(user_id: str) -> str:
    """
    Retrieve the full profile for a user.

    Args:
        user_id: User identifier (required).

    Returns:
        JSON string with profile fields, or an error if not found.
    """
    result = get_user_memory().profile(user_id=user_id)
    if result is None:
        return _fmt({"error": f"Profile for user {user_id} not found"})
    return _fmt(result)


@mcp.tool()
def list_user_profiles(
    user_id: Optional[str] = None,
    main_topic: Optional[List[str]] = None,
    sub_topic: Optional[List[str]] = None,
    topic_value: Optional[List[str]] = None,
    limit: int = 100,
    offset: int = 0,
) -> str:
    """
    List user profiles with optional topic filtering.

    Args:
        user_id: Filter by a specific user.
        main_topic: Filter by main topic names, e.g. ``["interests"]``.
        sub_topic: Filter by ``"main.sub"`` paths, e.g. ``["interests.sport"]``.
        topic_value: Filter by exact topic values, e.g. ``["Basketball"]``.
        limit: Maximum profiles to return (default 100).
        offset: Pagination offset (default 0).

    Returns:
        JSON string with ``{"profiles": [...], "count": int}``.
    """
    result = get_user_memory().profile_list(
        user_id=user_id,
        main_topic=main_topic,
        sub_topic=sub_topic,
        topic_value=topic_value,
        limit=limit,
        offset=offset,
    )
    return _fmt({"profiles": result, "count": len(result)})


@mcp.tool()
def delete_user_profile(user_id: str) -> str:
    """
    Delete a user's profile (does not delete their memories).

    Args:
        user_id: User identifier (required).

    Returns:
        JSON string with ``{"success": bool, "user_id": str, "message": str}``.
    """
    success = get_user_memory().delete_profile(user_id=user_id)
    return _fmt({
        "success": success,
        "user_id": user_id,
        "message": (
            f"Profile for user {user_id} deleted"
            if success
            else f"Profile for user {user_id} not found"
        ),
    })


@mcp.tool()
def delete_memory_with_profile(
    memory_id: int,
    user_id: str,
    agent_id: Optional[str] = None,
    delete_profile: bool = False,
) -> str:
    """
    Delete a memory and optionally the user's profile.

    Args:
        memory_id: Numeric memory ID.
        user_id: User identifier (required).
        agent_id: Agent identifier.
        delete_profile: Also delete the user's profile when True (default False).

    Returns:
        JSON string with success status.
    """
    success = get_user_memory().delete(
        memory_id=memory_id,
        user_id=user_id,
        agent_id=agent_id,
        delete_profile=delete_profile,
    )
    result: Dict[str, Any] = {"success": success, "memory_id": memory_id, "user_id": user_id}
    if delete_profile:
        result["profile_deleted"] = True
    return _fmt(result)


# ============================================================================
# Entry point
# ============================================================================

def main() -> None:
    """
    Start the PowerMem MCP server.

    Transport is selected via the first CLI argument (default: ``streamable-http``).
    Port is the second argument (default: ``8000``).

    Usage::

        powermem-mcp                          # streamable-http, port 8000
        powermem-mcp sse                      # SSE, port 8000
        powermem-mcp sse 8001                 # SSE, port 8001
        powermem-mcp stdio                    # stdio / JSON-RPC
        powermem-mcp streamable-http 8001
    """
    transport = sys.argv[1] if len(sys.argv) > 1 else "streamable-http"
    port = 8000
    if len(sys.argv) > 2:
        try:
            port = int(sys.argv[2])
        except ValueError:
            print(f"Invalid port '{sys.argv[2]}', using 8000", file=sys.stderr)

    path = "/mcp"

    if transport == "stdio":
        print("Starting PowerMem MCP Server [stdio]...", file=sys.stderr)
        mcp.run(transport="stdio")
    elif transport == "sse":
        print(f"Starting PowerMem MCP Server [SSE] on port {port}...", file=sys.stderr)
        mcp.run(transport="sse", host="0.0.0.0", port=port, path=path)
    else:
        print(f"Starting PowerMem MCP Server [streamable-http] on port {port}...", file=sys.stderr)
        mcp.run(transport="streamable-http", host="0.0.0.0", port=port, path=path)


if __name__ == "__main__":
    main()
