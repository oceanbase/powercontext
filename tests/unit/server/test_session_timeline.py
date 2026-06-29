import uuid
from unittest.mock import MagicMock, patch

import pytest

from server.services.memory_service import MemoryService, SESSION_TIMELINE_FETCH_CAP


def _memory(
    memory_id: int,
    content: str,
    run_id: str = "run-1",
    created_at: str = "2026-01-01T00:00:00Z",
    **metadata,
):
    return {
        "id": memory_id,
        "memory_id": memory_id,
        "content": content,
        "user_id": "user-1",
        "agent_id": "agent-1",
        "run_id": run_id,
        "created_at": created_at,
        "metadata": {
            "schema": "powermem.coding_agent_observation.v1",
            "scope": "coding_agent",
            "source": "coding_agent",
            "record_kind": "observation_raw",
            **metadata,
        },
    }


def _service_with_memories(memories):
    service = MemoryService.__new__(MemoryService)
    service.list_memories = MagicMock(return_value=memories)
    return service


def test_list_memories_and_count_memories_pass_run_id_to_core():
    service = MemoryService.__new__(MemoryService)
    service.memory = MagicMock()
    service.memory.get_all.return_value = {"results": []}
    service.memory.count_all.return_value = 2

    assert service.list_memories(user_id="user-1", run_id="run-1") == []
    assert service.count_memories(user_id="user-1", run_id="run-1") == 2

    service.memory.get_all.assert_called_once()
    assert service.memory.get_all.call_args.kwargs["run_id"] == "run-1"
    service.memory.count_all.assert_called_once()
    assert service.memory.count_all.call_args.kwargs["run_id"] == "run-1"


def test_session_summaries_group_by_run_id_and_return_snapshot_precision():
    service = _service_with_memories([
        _memory(
            1,
            "session one started",
            run_id="run-1",
            created_at="2026-01-01T00:00:00Z",
            observation_kind="session_start",
        ),
        _memory(
            2,
            "pytest failed with exit code 1",
            run_id="run-1",
            created_at="2026-01-01T00:03:00Z",
            observation_kind="command_result",
        ),
        _memory(
            3,
            "session two noop",
            run_id="run-2",
            created_at="2026-01-02T00:00:00Z",
            observation_kind="none",
            observation_status="none",
        ),
    ])

    result = service.list_session_summaries(limit=10)

    assert result["precision"] == "memory_snapshot"
    assert result["capabilities"] == {
        "event_log": False,
        "memory_snapshot": True,
        "before_after_diff": False,
        "source_on_demand": True,
    }
    assert result["total"] == 2
    assert result["sessions"][0]["run_id"] == "run-2"
    assert result["sessions"][1]["run_id"] == "run-1"
    assert result["sessions"][1]["event_count"] == 2
    assert result["sessions"][1]["memory_count"] == 2
    assert result["sessions"][1]["latest_preview"] == "pytest failed with exit code 1"


def test_session_stats_count_events_changed_memories_and_noop_rate():
    service = _service_with_memories([
        _memory(1, "session start", observation_kind="session_start"),
        _memory(2, "nothing changed", observation_kind="none", observation_status="none"),
        _memory(
            3,
            "other run command",
            run_id="run-2",
            observation_kind="command_result",
        ),
    ])

    stats = service.get_session_stats()

    assert stats["total_sessions"] == 2
    assert stats["total_events"] == 3
    assert stats["changed_memories"] == 3
    assert stats["no_op_events"] == 1
    assert stats["no_op_rate"] == 0.3333
    assert stats["event_types"] == {
        "session_start": 1,
        "none": 1,
        "command_result": 1,
    }


def test_timeline_filters_event_type_uses_cursor_and_hides_source_by_default():
    service = _service_with_memories([
        _memory(
            1,
            "first command source content",
            created_at="2026-01-01T00:00:00Z",
            observation_id="obs-1",
            observation_kind="command_result",
            observation={"observation_id": "obs-1", "content": "first command source content"},
        ),
        _memory(
            2,
            "second command source content",
            created_at="2026-01-01T00:01:00Z",
            observation_id="obs-2",
            observation_kind="command_result",
            observation={"observation_id": "obs-2", "content": "second command source content"},
        ),
        _memory(
            3,
            "session stop",
            created_at="2026-01-01T00:02:00Z",
            observation_id="obs-3",
            observation_kind="session_stop",
        ),
    ])

    first_page = service.list_timeline_events(event_type="command_result", limit=1)

    assert first_page["total"] == 2
    assert first_page["events"][0]["event_id"] == "obs-2"
    assert "source_content" not in first_page["events"][0]
    assert "content" not in first_page["events"][0]["metadata"]["observation"]
    assert first_page["events"][0]["metadata"]["observation"]["content_preview"] == (
        "second command source content"
    )
    assert first_page["next_cursor"]

    second_page = service.list_timeline_events(
        event_type="command_result",
        cursor=first_page["next_cursor"],
        limit=1,
    )

    assert second_page["events"][0]["event_id"] == "obs-1"
    assert second_page["next_cursor"] is None


def test_timeline_include_source_returns_source_content_on_demand():
    service = _service_with_memories([
        _memory(
            1,
            "full source content",
            observation_id="obs-1",
            observation_kind="command_result",
        ),
    ])

    result = service.list_timeline_events(include_source=True)

    assert result["events"][0]["source_preview"] == "full source content"
    assert result["events"][0]["source_content"] == "full source content"


def test_session_timeline_filters_alias_session_ids_after_projection():
    service = _service_with_memories([
        _memory(
            1,
            "legacy hook record",
            run_id=None,
            session_id="session-alias-1",
            observation_kind="session_start",
        ),
        _memory(
            2,
            "other run",
            run_id="run-2",
            observation_kind="command_result",
        ),
    ])

    sessions = service.list_session_summaries()
    assert {session["run_id"] for session in sessions["sessions"]} == {
        "run-2",
        "session-alias-1",
    }

    timeline = service.list_timeline_events(run_id="session-alias-1")
    stats = service.get_session_stats(run_id="session-alias-1")

    assert timeline["total"] == 1
    assert timeline["events"][0]["run_id"] == "session-alias-1"
    assert stats["total_sessions"] == 1
    assert stats["total_events"] == 1


def test_timeline_redacts_source_like_metadata_fields_by_default():
    service = _service_with_memories([
        _memory(
            1,
            "preview safe content",
            observation_id="obs-1",
            observation_kind="command_result",
            observation={
                "observation_id": "obs-1",
                "content": "full observation content",
                "source_content": "full nested source content",
                "nested": {
                    "raw_source": "nested raw source content",
                    "keep": "safe",
                },
            },
            source_content="top level source content",
            raw_source="top level raw source",
        ),
    ])

    result = service.list_timeline_events()
    metadata = result["events"][0]["metadata"]
    observation = metadata["observation"]

    assert "source_content" not in metadata
    assert "raw_source" not in metadata
    assert metadata["source_content_preview"] == "top level source content"
    assert metadata["raw_source_preview"] == "top level raw source"
    assert "content" not in observation
    assert "source_content" not in observation
    assert observation["content_preview"] == "full observation content"
    assert observation["source_content_preview"] == "full nested source content"
    assert "raw_source" not in observation["nested"]
    assert observation["nested"]["raw_source_preview"] == "nested raw source content"
    assert observation["nested"]["keep"] == "safe"


def test_timeline_ignores_malformed_cursor_instead_of_raising():
    service = _service_with_memories([
        _memory(1, "first event", observation_kind="command_result"),
    ])

    result = service.list_timeline_events(cursor="!")

    assert result["total"] == 1
    assert result["events"][0]["memory_id"] == "1"


def test_timeline_orders_by_observation_timestamp_before_storage_time():
    service = _service_with_memories([
        _memory(
            1,
            "stored later occurred earlier",
            created_at="2026-01-02T00:00:00Z",
            observation_kind="command_result",
            observation={
                "observation_id": "obs-1",
                "timestamp": "2026-01-01T00:00:00Z",
            },
        ),
        _memory(
            2,
            "stored earlier occurred later",
            created_at="2026-01-01T00:00:00Z",
            observation_kind="command_result",
            observation={
                "observation_id": "obs-2",
                "timestamp": "2026-01-03T00:00:00Z",
            },
        ),
    ])

    result = service.list_timeline_events(order="asc")

    assert [event["event_id"] for event in result["events"]] == ["obs-1", "obs-2"]


def test_timeline_query_filters_preview_and_metadata():
    service = _service_with_memories([
        _memory(1, "pytest failed", observation_kind="command_result", tool_name="pytest"),
        _memory(2, "npm passed", run_id="run-2", observation_kind="command_result"),
    ])

    result = service.list_timeline_events(q="pytest")

    assert result["total"] == 1
    assert result["events"][0]["memory_id"] == "1"


def test_load_session_memories_pushes_run_id_to_storage_when_filtering():
    service = MemoryService.__new__(MemoryService)
    service.list_memories = MagicMock(return_value=[])

    service._load_session_memories(user_id="user-1", agent_id="agent-1", run_id="target-run")

    assert service.list_memories.call_count == 3
    kwargs_list = [inv.kwargs for inv in service.list_memories.call_args_list]
    assert any(kwargs.get("run_id") == "target-run" for kwargs in kwargs_list)
    assert any(kwargs.get("filters") == {"session_id": "target-run"} for kwargs in kwargs_list)
    assert any(kwargs.get("filters") == {"thread_id": "target-run"} for kwargs in kwargs_list)
    run_kwargs = next(kwargs for kwargs in kwargs_list if kwargs.get("run_id") == "target-run")
    assert run_kwargs.get("limit") == SESSION_TIMELINE_FETCH_CAP
    alias_kwargs = [
        kwargs
        for kwargs in kwargs_list
        if kwargs.get("filters") in ({"session_id": "target-run"}, {"thread_id": "target-run"})
    ]
    assert len(alias_kwargs) == 2
    for kwargs in alias_kwargs:
        assert kwargs.get("limit") == SESSION_TIMELINE_FETCH_CAP
        assert kwargs.get("user_id") == "user-1"
        assert kwargs.get("agent_id") == "agent-1"


def test_fetch_session_memories_for_run_id_merges_alias_records_when_under_fetch_cap():
    target_run = "target-run"
    canonical_memory = _memory(
        1,
        "canonical run_id match",
        run_id=target_run,
        observation_kind="session_start",
    )
    alias_only_memory = _memory(
        2,
        "alias-only legacy record",
        run_id=None,
        session_id=target_run,
        observation_kind="command_result",
    )

    def list_memories_side_effect(**kwargs):
        if kwargs.get("run_id") == target_run:
            return [canonical_memory]
        filters = kwargs.get("filters") or {}
        if filters.get("session_id") == target_run:
            return [alias_only_memory]
        if filters.get("thread_id") == target_run:
            return []
        return []

    service = MemoryService.__new__(MemoryService)
    service.list_memories = MagicMock(side_effect=list_memories_side_effect)

    merged = service._fetch_session_memories_for_run_id(run_id=target_run)

    assert len(merged) == 2
    assert {service._memory_id(memory) for memory in merged} == {"1", "2"}


def test_merge_memories_by_id_prefers_later_batch_for_same_memory_id():
    service = MemoryService.__new__(MemoryService)
    alias_copy = _memory(42, "alias copy", run_id=None, session_id="target-run")
    canonical_copy = _memory(42, "canonical copy", run_id="target-run")

    merged = service._merge_memories_by_id([alias_copy], [canonical_copy])

    assert len(merged) == 1
    assert merged[0]["content"] == "canonical copy"


def test_alias_only_records_not_dropped_when_run_id_at_cap():
    target_run = "target-run"
    fillers = [
        _memory(index, f"filler {index}", run_id=target_run)
        for index in range(SESSION_TIMELINE_FETCH_CAP)
    ]
    alias_only = _memory(
        5001,
        "legacy alias",
        run_id=None,
        session_id=target_run,
        observation_kind="command_result",
    )

    def side_effect(**kwargs):
        if kwargs.get("run_id") == target_run:
            return fillers
        filters = kwargs.get("filters") or {}
        if filters.get("session_id") == target_run:
            return [alias_only]
        return []

    service = MemoryService.__new__(MemoryService)
    service.list_memories = MagicMock(side_effect=side_effect)

    result = service._fetch_session_memories_for_run_id(run_id=target_run)

    assert service.list_memories.call_count == 3
    assert any(service._memory_id(memory) == "5001" for memory in result)
    assert len(result) == SESSION_TIMELINE_FETCH_CAP


def test_fetch_session_memories_for_run_id_uses_full_cap_for_alias_when_run_at_cap():
    target_run = "target-run"
    fillers = [
        _memory(index, f"filler {index}", run_id=target_run)
        for index in range(SESSION_TIMELINE_FETCH_CAP)
    ]

    def side_effect(**kwargs):
        if kwargs.get("run_id") == target_run:
            return fillers
        return []

    service = MemoryService.__new__(MemoryService)
    service.list_memories = MagicMock(side_effect=side_effect)

    service._fetch_session_memories_for_run_id(run_id=target_run)

    kwargs_list = [inv.kwargs for inv in service.list_memories.call_args_list]
    alias_kwargs = [
        kwargs
        for kwargs in kwargs_list
        if kwargs.get("filters") in ({"session_id": target_run}, {"thread_id": target_run})
    ]
    assert len(alias_kwargs) == 2
    for kwargs in alias_kwargs:
        assert kwargs.get("limit") == SESSION_TIMELINE_FETCH_CAP


def test_fetch_session_memories_for_run_id_uses_remaining_limit_for_alias_queries():
    target_run = "target-run"
    canonical_memory = _memory(1, "canonical run_id match", run_id=target_run)

    def list_memories_side_effect(**kwargs):
        if kwargs.get("run_id") == target_run:
            return [canonical_memory]
        return []

    service = MemoryService.__new__(MemoryService)
    service.list_memories = MagicMock(side_effect=list_memories_side_effect)

    service._fetch_session_memories_for_run_id(run_id=target_run)

    assert service.list_memories.call_count == 3
    kwargs_list = [inv.kwargs for inv in service.list_memories.call_args_list]
    alias_kwargs = [
        kwargs
        for kwargs in kwargs_list
        if kwargs.get("filters") in ({"session_id": target_run}, {"thread_id": target_run})
    ]
    assert len(alias_kwargs) == 2
    for kwargs in alias_kwargs:
        assert kwargs.get("limit") == SESSION_TIMELINE_FETCH_CAP - 1


def test_timeline_run_id_filter_uses_storage_side_query_beyond_global_snapshot():
    target_run = "target-run"
    target_memory = _memory(
        999,
        "older targeted session event",
        run_id=target_run,
        created_at="2025-01-01T00:00:00Z",
        observation_kind="session_start",
    )
    noise_memories = [
        _memory(
            index,
            f"newer noise {index}",
            run_id=f"run-{index}",
            created_at="2026-06-01T00:00:00Z",
            observation_kind="command_result",
        )
        for index in range(5001)
    ]

    def list_memories_side_effect(**kwargs):
        if kwargs.get("run_id") == target_run:
            return [target_memory]
        filters = kwargs.get("filters") or {}
        if filters.get("session_id") == target_run or filters.get("thread_id") == target_run:
            return []
        if kwargs.get("run_id") is None and not filters:
            return noise_memories
        return []

    service = MemoryService.__new__(MemoryService)
    service.list_memories = MagicMock(side_effect=list_memories_side_effect)

    global_memories = service._load_session_memories(run_id=None)
    assert sum(
        1 for memory in global_memories if service._memory_run_id(memory) == target_run
    ) == 0

    targeted_memories = service._load_session_memories(run_id=target_run)
    assert len(targeted_memories) == 1
    assert service._memory_id(targeted_memories[0]) == "999"
    assert service._memory_run_id(targeted_memories[0]) == target_run

    timeline = service.list_timeline_events(run_id=target_run)
    assert timeline["total"] == 1
    assert timeline["events"][0]["run_id"] == target_run
    assert timeline["events"][0]["memory_id"] == "999"


def test_fetch_session_memories_for_run_id_deduplicates_run_and_alias_results():
    target_run = "target-run"
    shared_memory = _memory(
        42,
        "shared session record",
        run_id=target_run,
        observation_kind="session_start",
    )
    filler_memories = [
        _memory(
            index + 1000,
            f"fill {index}",
            run_id=target_run,
            observation_kind="command_result",
        )
        for index in range(SESSION_TIMELINE_FETCH_CAP - 1)
    ]

    def list_memories_side_effect(**kwargs):
        if kwargs.get("run_id") == target_run:
            return filler_memories + [shared_memory]
        filters = kwargs.get("filters") or {}
        if filters.get("session_id") == target_run:
            return [shared_memory]
        if filters.get("thread_id") == target_run:
            return []
        return []

    service = MemoryService.__new__(MemoryService)
    service.list_memories = MagicMock(side_effect=list_memories_side_effect)

    merged = service._fetch_session_memories_for_run_id(run_id=target_run)

    assert len(merged) == SESSION_TIMELINE_FETCH_CAP
    assert sum(1 for memory in merged if service._memory_id(memory) == "42") == 1
    assert service.list_memories.call_count == 3


def test_timeline_run_id_filter_drops_conflicting_explicit_run_id():
    service = _service_with_memories([
        _memory(
            1,
            "wrong run alias match",
            run_id="other-run",
            session_id="target-run",
            observation_kind="command_result",
        ),
        _memory(
            2,
            "correct target run",
            run_id="target-run",
            observation_kind="session_start",
        ),
    ])

    timeline = service.list_timeline_events(run_id="target-run")

    assert timeline["total"] == 1
    assert timeline["events"][0]["memory_id"] == "2"
    assert timeline["events"][0]["run_id"] == "target-run"


def _session_observation_metadata(**metadata):
    return {
        "schema": "powermem.coding_agent_observation.v1",
        "scope": "coding_agent",
        "source": "coding_agent",
        "record_kind": "observation_raw",
        **metadata,
    }


def _insert_storage_session_memory(storage, content, run_id, created_at, **metadata):
    return storage.add_memory(
        {
            "content": content,
            "user_id": "user-1",
            "agent_id": "agent-1",
            "run_id": run_id,
            "created_at": created_at,
            "updated_at": created_at,
            "metadata": _session_observation_metadata(**metadata),
        }
    )


@pytest.fixture
def sqlite_timeline_service():
    config = {
        "vector_store": {
            "provider": "sqlite",
            "config": {
                "database_path": ":memory:",
                "collection_name": f"timeline_test_{uuid.uuid4().hex[:8]}",
            },
        },
        "llm": {
            "provider": "openai",
            "config": {
                "model": "gpt-4o-mini",
                "api_key": "mock-key",
            },
        },
        "embedder": {
            "provider": "mock",
            "config": {},
        },
    }
    patcher = patch("powermem.integrations.llm.factory.LLMFactory.create")
    mock_llm_factory = patcher.start()
    mock_llm = MagicMock()
    mock_llm.generate_response.return_value = {"content": "Test memory content"}
    mock_llm_factory.return_value = mock_llm
    try:
        yield MemoryService(config=config)
    finally:
        patcher.stop()


def test_timeline_run_id_filter_finds_old_session_with_sqlite_storage(sqlite_timeline_service):
    service = sqlite_timeline_service
    storage = service.memory.storage

    for index in range(5001):
        _insert_storage_session_memory(
            storage,
            f"noise {index}",
            run_id=f"noise-run-{index}",
            created_at="2026-06-01T00:00:00Z",
            observation_kind="command_result",
        )
    _insert_storage_session_memory(
        storage,
        "older targeted session event",
        run_id="target-run",
        created_at="2020-01-01T00:00:00Z",
        observation_kind="session_start",
    )
    _insert_storage_session_memory(
        storage,
        "second older targeted session event",
        run_id="target-run",
        created_at="2020-01-02T00:00:00Z",
        observation_kind="command_result",
    )

    global_timeline = service.list_timeline_events()
    assert sum(
        1 for event in global_timeline["events"] if event.get("run_id") == "target-run"
    ) == 0

    targeted_timeline = service.list_timeline_events(run_id="target-run")

    assert targeted_timeline["total"] == 2
    assert {event["run_id"] for event in targeted_timeline["events"]} == {"target-run"}
    assert {event["content_preview"] for event in targeted_timeline["events"]} == {
        "older targeted session event",
        "second older targeted session event",
    }
