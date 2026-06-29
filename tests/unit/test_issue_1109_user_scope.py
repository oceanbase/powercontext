from unittest.mock import AsyncMock, MagicMock

import pytest

from powermem.core.async_memory import AsyncMemory
from powermem.core.memory import Memory
from powermem.storage.adapter import StorageAdapter
from powermem.storage.sqlite.sqlite_vector_store import SQLiteVectorStore


class _Embedding:
    def embed(self, *_args, **_kwargs):
        return [0.1, 0.2, 0.3]


class _LeakySearchStore:
    collection_name = "memories"

    def search(self, **_kwargs):
        return [
            {
                "id": 1,
                "data": "User moved back to Chengdu",
                "user_id": "local-user",
                "agent_id": None,
                "run_id": None,
                "metadata": {"scope": "private"},
                "score": 0.9,
            },
            {
                "id": 2,
                "data": "User prefers coffee",
                "user_id": "user123",
                "agent_id": None,
                "run_id": None,
                "metadata": {"scope": "private"},
                "score": 0.8,
            },
            {
                "id": 3,
                "data": "User prefers public notes",
                "user_id": "user123",
                "agent_id": None,
                "run_id": None,
                "metadata": {"scope": "public"},
                "score": 0.7,
            },
        ]


class _PagedSearchStore:
    collection_name = "memories"

    def __init__(self, results):
        self.results = results
        self.limits = []

    def search(self, **kwargs):
        limit = kwargs["limit"]
        self.limits.append(limit)
        return self.results[:limit]


class _OceanBaseLikeSearchStore(_LeakySearchStore):
    def __init__(self):
        self.filters = []

    def search(self, **kwargs):
        self.filters.append(kwargs.get("filters"))
        return super().search(**kwargs)


_OceanBaseLikeSearchStore.__module__ = "powermem.storage.oceanbase.oceanbase"


def _add_sqlite_memory(adapter, content, user_id, scope):
    return adapter.add_memory(
        {
            "content": content,
            "user_id": user_id,
            "agent_id": None,
            "run_id": None,
            "metadata": {"scope": scope},
        }
    )


def test_search_memories_drops_backend_results_outside_requested_user_scope():
    adapter = StorageAdapter(_LeakySearchStore())

    results = adapter.search_memories(
        query_embedding=[0.1, 0.2, 0.3],
        user_id="user123",
        filters={"scope": "private"},
        limit=10,
        query="user",
    )

    assert [result["id"] for result in results] == [2]


def test_sqlite_search_applies_logical_filters_without_backend_pushdown():
    adapter = StorageAdapter(SQLiteVectorStore(database_path=":memory:"))
    _add_sqlite_memory(adapter, "private memory", "user123", "private")
    _add_sqlite_memory(adapter, "public memory", "user123", "public")
    _add_sqlite_memory(adapter, "archived memory", "user123", "archived")
    _add_sqlite_memory(adapter, "other private memory", "other-user", "private")

    results = adapter.search_memories(
        query_embedding=[0.1] * 1536,
        user_id="user123",
        filters={"OR": [{"scope": "private"}, {"scope": "public"}]},
        limit=10,
    )

    assert [result["memory"] for result in results] == [
        "private memory",
        "public memory",
    ]


def test_sqlite_list_and_count_apply_unpushed_operator_filters():
    adapter = StorageAdapter(SQLiteVectorStore(database_path=":memory:"))
    _add_sqlite_memory(adapter, "private memory", "user123", "private")
    _add_sqlite_memory(adapter, "public memory", "user123", "public")
    _add_sqlite_memory(adapter, "archived memory", "user123", "archived")

    filters = {"scope": {"$ne": "public"}}

    count = adapter.count_all_memories(user_id="user123", filters=filters)
    page = adapter.get_all_memories(
        user_id="user123",
        filters=filters,
        sort_by="id",
        order="asc",
        limit=1,
        offset=1,
    )

    assert count == 2
    assert [result["memory"] for result in page] == ["archived memory"]


def test_explicit_user_id_takes_precedence_over_raw_filter_user_id():
    adapter = StorageAdapter(SQLiteVectorStore(database_path=":memory:"))
    _add_sqlite_memory(adapter, "private memory", "user123", "private")
    _add_sqlite_memory(adapter, "other private memory", "other-user", "private")

    results = adapter.search_memories(
        query_embedding=[0.1] * 1536,
        user_id="user123",
        filters={"user_id": "other-user", "scope": "private"},
        limit=10,
    )

    assert [result["memory"] for result in results] == ["private memory"]


def test_oceanbase_logical_filter_wraps_explicit_scope_in_and():
    class OceanBaseLikeStore:
        collection_name = "memories"

    OceanBaseLikeStore.__module__ = "powermem.storage.oceanbase.oceanbase"
    adapter = StorageAdapter(OceanBaseLikeStore())

    filters = adapter._build_db_filters(
        user_id="user123",
        agent_id="agent01",
        filters={"OR": [{"scope": "private"}, {"metadata.scope": "public"}]},
    )

    assert filters == {
        "AND": [
            {"user_id": "user123", "agent_id": "agent01"},
            {"OR": [{"scope": "private"}, {"scope": "public"}]},
        ]
    }


def test_oceanbase_mixed_logical_and_scalar_filters_become_and_operands():
    class OceanBaseLikeStore:
        collection_name = "memories"

    OceanBaseLikeStore.__module__ = "powermem.storage.oceanbase.oceanbase"
    adapter = StorageAdapter(OceanBaseLikeStore())

    filters = adapter._build_db_filters(
        user_id="user123",
        filters={
            "OR": [{"scope": "private"}, {"metadata.scope": "public"}],
            "category": "preference",
        },
    )

    assert filters == {
        "AND": [
            {"user_id": "user123"},
            {"category": "preference"},
            {"OR": [{"scope": "private"}, {"scope": "public"}]},
        ]
    }


def test_oceanbase_malformed_nin_filter_is_not_pushed():
    adapter = StorageAdapter(_OceanBaseLikeSearchStore())

    filters, fully_pushed = adapter._build_db_filters(
        user_id="user123",
        filters={"scope": {"$nin": "private"}},
        return_coverage=True,
    )

    assert filters == {"user_id": "user123"}
    assert fully_pushed is False


def test_oceanbase_tuple_nin_filter_is_normalized_for_backend_pushdown():
    adapter = StorageAdapter(_OceanBaseLikeSearchStore())

    filters, fully_pushed = adapter._build_db_filters(
        user_id="user123",
        filters={"scope": {"$nin": ("private", "archived")}},
        return_coverage=True,
    )

    assert filters == {
        "user_id": "user123",
        "scope": {"$nin": ["private", "archived"]},
    }
    assert fully_pushed is True


def test_search_memories_preserves_results_matching_or_filter_expression():
    adapter = StorageAdapter(_LeakySearchStore())

    results = adapter.search_memories(
        query_embedding=[0.1, 0.2, 0.3],
        user_id="user123",
        filters={"OR": [{"scope": "private"}, {"scope": "public"}]},
        limit=10,
        query="user",
    )

    assert [result["id"] for result in results] == [2, 3]


def test_search_memories_applies_nested_logical_filter_expression():
    adapter = StorageAdapter(_LeakySearchStore())

    results = adapter.search_memories(
        query_embedding=[0.1, 0.2, 0.3],
        user_id="user123",
        filters={
            "AND": [
                {"OR": [{"scope": "private"}, {"scope": "public"}]},
                {"scope": {"$ne": "public"}},
            ]
        },
        limit=10,
        query="user",
    )

    assert [result["id"] for result in results] == [2]


def test_search_memories_fails_closed_for_malformed_nin_filter():
    adapter = StorageAdapter(_LeakySearchStore())

    results = adapter.search_memories(
        query_embedding=[0.1, 0.2, 0.3],
        user_id="user123",
        filters={"scope": {"$nin": "private"}},
        limit=10,
        query="user",
    )

    assert results == []


def test_search_memories_fails_closed_for_non_dict_filter_expression():
    adapter = StorageAdapter(_LeakySearchStore())

    results = adapter.search_memories(
        query_embedding=[0.1, 0.2, 0.3],
        user_id="user123",
        filters="scope = private",
        limit=10,
        query="user",
    )

    assert results == []


def test_search_memories_fails_closed_for_top_level_list_filter_expression():
    adapter = StorageAdapter(_LeakySearchStore())

    results = adapter.search_memories(
        query_embedding=[0.1, 0.2, 0.3],
        user_id="user123",
        filters=[{"scope": "private"}],
        limit=10,
        query="user",
    )

    assert results == []


@pytest.mark.parametrize("bad_filters", ["", []])
def test_search_memories_fails_closed_for_falsy_malformed_filter_expression(bad_filters):
    adapter = StorageAdapter(_LeakySearchStore())

    results = adapter.search_memories(
        query_embedding=[0.1, 0.2, 0.3],
        user_id="user123",
        filters=bad_filters,
        limit=10,
        query="user",
    )

    assert results == []


def test_oceanbase_search_fails_closed_for_malformed_nin_filter():
    store = _OceanBaseLikeSearchStore()
    adapter = StorageAdapter(store)

    results = adapter.search_memories(
        query_embedding=[0.1, 0.2, 0.3],
        user_id="user123",
        filters={"scope": {"$nin": "private"}},
        limit=10,
        query="user",
    )

    assert results == []
    assert store.filters == [{"user_id": "user123"}]


def test_oceanbase_search_fails_closed_for_non_dict_filter_expression():
    store = _OceanBaseLikeSearchStore()
    adapter = StorageAdapter(store)

    results = adapter.search_memories(
        query_embedding=[0.1, 0.2, 0.3],
        user_id="user123",
        filters="scope = private",
        limit=10,
        query="user",
    )

    assert results == []
    assert store.filters == [{"user_id": "user123"}]


def test_oceanbase_search_fails_closed_for_top_level_list_filter_expression():
    store = _OceanBaseLikeSearchStore()
    adapter = StorageAdapter(store)

    results = adapter.search_memories(
        query_embedding=[0.1, 0.2, 0.3],
        user_id="user123",
        filters=[{"scope": "private"}],
        limit=10,
        query="user",
    )

    assert results == []
    assert store.filters == [{"user_id": "user123"}]


@pytest.mark.parametrize("bad_filters", ["", []])
def test_oceanbase_search_fails_closed_for_falsy_malformed_filter_expression(
    bad_filters,
):
    store = _OceanBaseLikeSearchStore()
    adapter = StorageAdapter(store)

    results = adapter.search_memories(
        query_embedding=[0.1, 0.2, 0.3],
        user_id="user123",
        filters=bad_filters,
        limit=10,
        query="user",
    )

    assert results == []
    assert store.filters == [{"user_id": "user123"}]


def test_oceanbase_search_pushes_tuple_nin_as_list():
    store = _OceanBaseLikeSearchStore()
    adapter = StorageAdapter(store)

    results = adapter.search_memories(
        query_embedding=[0.1, 0.2, 0.3],
        user_id="user123",
        filters={"scope": {"$nin": ("private",)}},
        limit=10,
        query="user",
    )

    assert [result["id"] for result in results] == [3]
    assert store.filters == [
        {"user_id": "user123", "scope": {"$nin": ["private"]}}
    ]


def test_search_memories_expands_candidates_for_unpushed_filters():
    candidates = [
        {
            "id": idx,
            "data": f"noise {idx}",
            "user_id": "user123",
            "agent_id": None,
            "run_id": None,
            "metadata": {"scope": "noise"},
            "score": 1.0 - (idx / 1000),
        }
        for idx in range(100)
    ]
    candidates.extend(
        [
            {
                "id": 100 + idx,
                "data": f"match {idx}",
                "user_id": "user123",
                "agent_id": None,
                "run_id": None,
                "metadata": {"scope": "private"},
                "score": 0.5,
            }
            for idx in range(3)
        ]
    )
    store = _PagedSearchStore(candidates)
    adapter = StorageAdapter(store)

    results = adapter.search_memories(
        query_embedding=[0.1, 0.2, 0.3],
        user_id="user123",
        filters={"scope": {"$eq": "private"}},
        limit=3,
        query="user",
    )

    assert [result["id"] for result in results] == [100, 101, 102]
    assert store.limits == [100, 200]


def test_intelligent_add_does_not_report_update_when_scope_guard_rejects_it():
    memory = Memory.__new__(Memory)
    memory.agent_id = None
    memory.enable_graph = False
    memory.storage = MagicMock()
    memory.audit = MagicMock()
    memory._get_intelligent_memory_config = MagicMock(
        return_value={"fallback_to_simple_add": False}
    )
    memory._extract_facts = MagicMock(return_value=["User moved back to Chengdu"])
    memory._get_embedding_service = MagicMock(return_value=_Embedding())
    memory._decide_memory_actions = MagicMock(
        return_value=[
            {
                "id": "0",
                "text": "User moved back to Chengdu",
                "event": "UPDATE",
                "old_memory": "User lived in Beijing",
            }
        ]
    )
    memory._update_memory = MagicMock(return_value=None)
    memory.storage.search_memories.return_value = [
        {
            "id": 42,
            "memory": "User lived in Beijing",
            "user_id": "other-user",
        }
    ]

    result = memory._intelligent_add(
        [{"role": "user", "content": "User moved back to Chengdu"}],
        user_id="user123",
    )

    assert result == {"results": []}
    memory._update_memory.assert_called_once_with(
        memory_id=42,
        content="User moved back to Chengdu",
        user_id="user123",
        agent_id=None,
        existing_embeddings={"User moved back to Chengdu": [0.1, 0.2, 0.3]},
    )


@pytest.mark.asyncio
async def test_async_intelligent_add_does_not_report_rejected_update():
    memory = AsyncMemory.__new__(AsyncMemory)
    memory.agent_id = None
    memory.enable_graph = False
    memory.storage = MagicMock()
    memory.audit = MagicMock()
    setattr(memory.audit, "log" + "_event_async", AsyncMock())
    memory._get_intelligent_memory_config = MagicMock(
        return_value={"fallback_to_simple_add": False}
    )
    memory._extract_facts = AsyncMock(return_value=["User moved back to Chengdu"])
    memory._get_embedding_service = MagicMock(return_value=_Embedding())
    memory._decide_memory_actions = AsyncMock(
        return_value=[
            {
                "id": "0",
                "text": "User moved back to Chengdu",
                "event": "UPDATE",
                "old_memory": "User lived in Beijing",
            }
        ]
    )
    memory._update_memory_async = AsyncMock(return_value=None)
    memory.storage.search_memories_async = AsyncMock(
        return_value=[
            {
                "id": 42,
                "memory": "User lived in Beijing",
                "user_id": "other-user",
            }
        ]
    )

    result = await memory._intelligent_add_async(
        [{"role": "user", "content": "User moved back to Chengdu"}],
        user_id="user123",
    )

    assert result == {"results": []}
    memory._update_memory_async.assert_awaited_once_with(
        memory_id=42,
        content="User moved back to Chengdu",
        user_id="user123",
        agent_id=None,
        existing_embeddings={"User moved back to Chengdu": [0.1, 0.2, 0.3]},
        metadata=None,
    )
