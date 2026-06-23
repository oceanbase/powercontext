import asyncio
from unittest.mock import AsyncMock, MagicMock

from powermem.core.async_memory import AsyncMemory
from powermem.core.memory import Memory
from powermem.storage.adapter import StorageAdapter
from powermem.storage.sqlite.sqlite_vector_store import SQLiteVectorStore
from server.services.memory_service import MemoryService


def _add_memory(adapter, content, scope):
    return adapter.add_memory(
        {
            "content": content,
            "user_id": "u01",
            "agent_id": "a01",
            "metadata": {"scope": scope},
        }
    )


def _add_categorized_memory(adapter, content, category, priority):
    return adapter.add_memory(
        {
            "content": content,
            "user_id": "u01",
            "agent_id": "a01",
            "category": category,
            "metadata": {"priority": priority},
        }
    )


def test_storage_adapter_filters_metadata_before_pagination():
    store = SQLiteVectorStore(database_path=":memory:")
    adapter = StorageAdapter(store)

    _add_memory(adapter, "personal-1", "personal")
    _add_memory(adapter, "group-1", "group")
    _add_memory(adapter, "personal-2", "personal")
    _add_memory(adapter, "personal-3", "personal")

    total = adapter.count_all_memories(
        user_id="u01",
        agent_id="a01",
        filters={"scope": "personal"},
    )
    page = adapter.get_all_memories(
        user_id="u01",
        agent_id="a01",
        limit=2,
        offset=1,
        sort_by="id",
        order="asc",
        filters={"scope": "personal"},
    )

    assert total == 3
    assert [memory["memory"] for memory in page] == [
        "personal-2",
        "personal-3",
    ]
    assert all(memory["metadata"]["scope"] == "personal" for memory in page)


def test_storage_adapter_pushes_sqlite_metadata_filter_to_db():
    store = SQLiteVectorStore(database_path=":memory:")
    adapter = StorageAdapter(store)

    assert adapter._build_db_filters(
        user_id="u01",
        agent_id="a01",
        filters={"scope": "personal"},
    ) == {
        "user_id": "u01",
        "agent_id": "a01",
        "metadata.scope": "personal",
    }


def test_storage_adapter_preserves_sqlite_payload_and_dotted_filter_keys():
    store = SQLiteVectorStore(database_path=":memory:")
    adapter = StorageAdapter(store)

    assert adapter._build_db_filters(
        filters={
            "category": "preference",
            "metadata.priority": "high",
            "scope": "coding_agent",
        },
    ) == {
        "category": "preference",
        "metadata.priority": "high",
        "metadata.scope": "coding_agent",
    }


def test_storage_adapter_sqlite_filters_payload_and_metadata_keys():
    store = SQLiteVectorStore(database_path=":memory:")
    adapter = StorageAdapter(store)

    _add_categorized_memory(adapter, "python", "preference", "high")
    _add_categorized_memory(adapter, "email", "communication", "low")

    assert adapter.count_all_memories(filters={"category": "preference"}) == 1
    assert adapter.count_all_memories(filters={"priority": "high"}) == 1
    assert adapter.count_all_memories(filters={"metadata.priority": "high"}) == 1

    listed_results = adapter.get_all_memories(filters={"metadata.priority": "high"})
    category_results = adapter.search_memories(
        query_embedding=[0.1],
        filters={"category": "preference"},
    )
    priority_results = adapter.search_memories(
        query_embedding=[0.1],
        filters={"metadata.priority": "high"},
    )

    assert [memory["memory"] for memory in listed_results] == ["python"]
    assert [memory["memory"] for memory in category_results] == ["python"]
    assert [memory["memory"] for memory in priority_results] == ["python"]


def test_storage_adapter_keeps_oceanbase_metadata_filter_key():
    class OceanBaseLikeStore:
        collection_name = "memories"

    OceanBaseLikeStore.__module__ = "powermem.storage.oceanbase.oceanbase"
    adapter = StorageAdapter(OceanBaseLikeStore())

    assert adapter._build_db_filters(
        user_id="u01",
        agent_id="a01",
        filters={"scope": "personal"},
    ) == {
        "user_id": "u01",
        "agent_id": "a01",
        "scope": "personal",
    }


def test_storage_adapter_strips_oceanbase_dotted_metadata_prefix():
    class OceanBaseLikeStore:
        collection_name = "memories"

    OceanBaseLikeStore.__module__ = "powermem.storage.oceanbase.oceanbase"
    adapter = StorageAdapter(OceanBaseLikeStore())

    assert adapter._build_db_filters(
        filters={"metadata.scope": "personal", "metadata.priority": "high"},
    ) == {
        "scope": "personal",
        "priority": "high",
    }


def test_storage_adapter_list_matches_oceanbase_dotted_metadata_filter():
    class Result:
        def __init__(self, memory_id, payload):
            self.id = memory_id
            self.payload = payload

    class OceanBaseLikeStore:
        collection_name = "memories"

        def __init__(self):
            self.list_kwargs = None

        def list(self, **kwargs):
            self.list_kwargs = kwargs
            return [
                [
                    Result(
                        "mem-1",
                        {
                            "data": "personal",
                            "metadata": {"scope": "personal"},
                            "user_id": "u01",
                        },
                    ),
                    Result(
                        "mem-2",
                        {
                            "data": "group",
                            "metadata": {"scope": "group"},
                            "user_id": "u01",
                        },
                    ),
                ]
            ]

    OceanBaseLikeStore.__module__ = "powermem.storage.oceanbase.oceanbase"
    store = OceanBaseLikeStore()
    adapter = StorageAdapter(store)

    memories = adapter.get_all_memories(filters={"metadata.scope": "personal"})

    assert store.list_kwargs["filters"] == {"scope": "personal"}
    assert [memory["memory"] for memory in memories] == ["personal"]


def test_storage_adapter_search_pushes_sqlite_metadata_filter_to_db():
    class SQLiteLikeSearchStore:
        collection_name = "memories"

        def __init__(self):
            self.search_kwargs = None

        def search(self, query, vectors, limit, filters=None):
            self.search_kwargs = {
                "query": query,
                "vectors": vectors,
                "limit": limit,
                "filters": filters,
            }
            return []

    SQLiteLikeSearchStore.__module__ = "powermem.storage.sqlite.sqlite_vector_store"
    store = SQLiteLikeSearchStore()
    adapter = StorageAdapter(store)

    adapter.search_memories(
        query_embedding=[0.1],
        user_id="u01",
        agent_id="a01",
        filters={"scope": "coding_agent", "observation_id": "obs-1"},
        query="pytest",
    )

    assert store.search_kwargs["filters"] == {
        "user_id": "u01",
        "agent_id": "a01",
        "metadata.scope": "coding_agent",
        "metadata.observation_id": "obs-1",
    }


def test_storage_adapter_search_keeps_oceanbase_metadata_filter_key():
    class OceanBaseLikeSearchStore:
        collection_name = "memories"

        def __init__(self):
            self.search_kwargs = None

        def search(self, query, vectors, limit, filters=None):
            self.search_kwargs = {
                "query": query,
                "vectors": vectors,
                "limit": limit,
                "filters": filters,
            }
            return []

    OceanBaseLikeSearchStore.__module__ = "powermem.storage.oceanbase.oceanbase"
    store = OceanBaseLikeSearchStore()
    adapter = StorageAdapter(store)

    adapter.search_memories(
        query_embedding=[0.1],
        user_id="u01",
        agent_id="a01",
        filters={"scope": "coding_agent", "observation_id": "obs-1"},
        query="pytest",
    )

    assert store.search_kwargs["filters"] == {
        "user_id": "u01",
        "agent_id": "a01",
        "scope": "coding_agent",
        "observation_id": "obs-1",
    }


def test_storage_adapter_search_strips_oceanbase_dotted_metadata_filter_key():
    class OceanBaseLikeSearchStore:
        collection_name = "memories"

        def __init__(self):
            self.search_kwargs = None

        def search(self, query, vectors, limit, filters=None):
            self.search_kwargs = {
                "query": query,
                "vectors": vectors,
                "limit": limit,
                "filters": filters,
            }
            return []

    OceanBaseLikeSearchStore.__module__ = "powermem.storage.oceanbase.oceanbase"
    store = OceanBaseLikeSearchStore()
    adapter = StorageAdapter(store)

    adapter.search_memories(
        query_embedding=[0.1],
        filters={"metadata.scope": "coding_agent"},
        query="pytest",
    )

    assert store.search_kwargs["filters"] == {"scope": "coding_agent"}


def test_storage_adapter_count_uses_db_filters_without_fetching_all():
    store = MagicMock()
    store.collection_name = "memories"
    store.count.return_value = 3
    adapter = StorageAdapter(store)
    adapter.get_all_memories = MagicMock()

    total = adapter.count_all_memories(
        user_id="u01",
        agent_id="a01",
        filters={"scope": "personal"},
    )

    assert total == 3
    store.count.assert_called_once_with(
        filters={
            "user_id": "u01",
            "agent_id": "a01",
            "scope": "personal",
        }
    )
    adapter.get_all_memories.assert_not_called()


def test_pgvector_count_supports_nested_metadata_filters():
    from powermem.storage.pgvector.pgvector import PGVectorStore

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, query, params):
            self.query = query
            self.params = params

        def fetchone(self):
            return (3,)

    cursor = Cursor()
    store = PGVectorStore.__new__(PGVectorStore)
    store.collection_name = "memories"
    store._get_cursor = MagicMock(return_value=cursor)

    assert store.count({"metadata.scope": "personal"}) == 3
    assert "payload #>> string_to_array(%s, '.')" in cursor.query
    assert cursor.params == ("metadata.scope", "personal")


def test_memory_count_all_passes_filters_to_storage():
    memory = Memory.__new__(Memory)
    memory.storage = MagicMock()
    memory.storage.count_all_memories.return_value = 2
    memory.audit = MagicMock()

    result = memory.count_all(
        user_id="u01",
        agent_id="a01",
        filters={"scope": "personal"},
    )

    assert result == 2
    memory.storage.count_all_memories.assert_called_once_with(
        "u01",
        "a01",
        None,
        filters={"scope": "personal"},
    )


def test_memory_service_list_and_count_pass_filters():
    service = MemoryService.__new__(MemoryService)
    service.memory = MagicMock()
    service.memory.get_all.return_value = {
        "results": [{"id": 1, "memory": "m"}]
    }
    service.memory.count_all.return_value = 1

    listed = service.list_memories(
        user_id="u01",
        agent_id="a01",
        filters={"scope": "group"},
    )
    counted = service.count_memories(
        user_id="u01",
        agent_id="a01",
        filters={"scope": "group"},
    )

    assert listed == [{"id": 1, "memory": "m"}]
    assert counted == 1
    service.memory.get_all.assert_called_once_with(
        user_id="u01",
        agent_id="a01",
        limit=100,
        offset=0,
        filters={"scope": "group"},
        sort_by=None,
        order="desc",
    )
    service.memory.count_all.assert_called_once_with(
        user_id="u01",
        agent_id="a01",
        filters={"scope": "group"},
    )


def test_memory_get_all_keeps_graph_relations_out_of_results():
    memory = Memory.__new__(Memory)
    memory.storage = MagicMock()
    memory.audit = MagicMock()
    memory.enable_graph = True
    memory.graph_store = MagicMock()
    memory._http_client = None
    memory.agent_id = None

    stored_memory = {
        "id": 1,
        "memory": "personal-1",
        "metadata": {"scope": "personal"},
    }
    relation = {"source": "u01", "relationship": "likes", "target": "topic"}
    memory.storage.get_all_memories.return_value = [stored_memory]
    memory.graph_store.get_all.return_value = [relation]

    result = memory.get_all(user_id="u01", agent_id="a01", limit=10, offset=0)

    assert result["results"] == [stored_memory]
    assert result["relations"] == [relation]
    memory.graph_store.get_all.assert_called_once_with(
        {"user_id": "u01", "agent_id": "a01", "run_id": None}, 10
    )


def test_async_memory_get_all_keeps_graph_relations_out_of_results():
    async def run_test():
        memory = AsyncMemory.__new__(AsyncMemory)
        memory.storage = MagicMock()
        memory.audit = MagicMock()
        memory.audit.log_event_async = AsyncMock()
        memory.enable_graph = True
        memory.graph_store = MagicMock()

        stored_memory = {
            "id": 1,
            "memory": "personal-1",
            "metadata": {"scope": "personal"},
        }
        relation = {
            "source": "u01",
            "relationship": "likes",
            "target": "topic",
        }
        memory.storage.get_all_memories_async = AsyncMock(
            return_value=[stored_memory]
        )
        memory.graph_store.get_all.return_value = [relation]

        result = await memory.get_all(
            user_id="u01",
            agent_id="a01",
            limit=10,
            offset=0,
        )

        assert result["results"] == [stored_memory]
        assert result["relations"] == [relation]
        memory.graph_store.get_all.assert_called_once_with(
            {"user_id": "u01", "agent_id": "a01", "run_id": None}, 10
        )

    asyncio.run(run_test())
