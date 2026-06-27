from unittest.mock import MagicMock

from server.services.memory_service import MemoryService


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
