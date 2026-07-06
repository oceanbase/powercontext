"""
Regression coverage for issue #1093: metadata vs top-level payload filter key collisions.

SQLite: end-to-end count/list/search. OceanBase: adapter translation plus real
_generate_where_clause() column-first behavior (mock table, no live DB).
PGVector: _build_db_filters via lightweight backend stubs.
"""

import pytest

from powermem.agent.filters import matches_memory_filters
from powermem.storage.adapter import StorageAdapter
from powermem.storage.sqlite.sqlite_vector_store import SQLiteVectorStore

COLLISION_CONTENT = "alpha collision target"
DECOY_CONTENT = "decoy memory beta"

PAYLOAD_CATEGORY = "work"
METADATA_CATEGORY = "personal"
DECOY_CATEGORY = "archived"

PAYLOAD_CREATED_AT = "2024-06-01T00:00:00Z"
PAYLOAD_UPDATED_AT = "2024-06-02T00:00:00Z"
METADATA_CREATED_AT = "2020-01-01T00:00:00Z"
METADATA_UPDATED_AT = "2020-01-02T00:00:00Z"
DECOY_CREATED_AT = "1999-01-01T00:00:00Z"
DECOY_UPDATED_AT = "1999-01-02T00:00:00Z"

PAYLOAD_HASH = "payload-hash"
METADATA_HASH = "metadata-hash"
DECOY_PAYLOAD_HASH = "decoy-payload-hash"
DECOY_METADATA_HASH = "decoy-metadata-hash"

METADATA_DATA = "metadata-data"
DECOY_METADATA_DATA = "decoy-metadata-data"

PAYLOAD_ACTOR_ID = "payload-actor"
METADATA_ACTOR_ID = "metadata-actor"

PAYLOAD_ROLE = "payload-role"
METADATA_ROLE = "metadata-role"

PAYLOAD_TYPE = "payload-type"
METADATA_TYPE = "metadata-type"

USER_ID = "u01"


@pytest.fixture
def sqlite_adapter():
    """In-memory SQLite StorageAdapter for collision integration tests."""
    store = SQLiteVectorStore(database_path=":memory:")
    yield StorageAdapter(store)
    store.close()


def _oceanbase_adapter() -> StorageAdapter:
    store = type(
        "_OBStub",
        (),
        {"collection_name": "memories", "__module__": "powermem.storage.oceanbase.oceanbase"},
    )()
    return StorageAdapter(store)


def _pgvector_adapter() -> StorageAdapter:
    store = type(
        "_PGStub",
        (),
        {"collection_name": "memories", "__module__": "powermem.storage.pgvector.pgvector"},
    )()
    return StorageAdapter(store)


def _collision_memory_data(content: str = COLLISION_CONTENT) -> dict:
    return {
        "content": content,
        "user_id": USER_ID,
        "category": PAYLOAD_CATEGORY,
        "created_at": PAYLOAD_CREATED_AT,
        "updated_at": PAYLOAD_UPDATED_AT,
        "hash": PAYLOAD_HASH,
        "actor_id": PAYLOAD_ACTOR_ID,
        "role": PAYLOAD_ROLE,
        "type": PAYLOAD_TYPE,
        "metadata": {
            "category": METADATA_CATEGORY,
            "created_at": METADATA_CREATED_AT,
            "updated_at": METADATA_UPDATED_AT,
            "hash": METADATA_HASH,
            "data": METADATA_DATA,
            "actor_id": METADATA_ACTOR_ID,
            "role": METADATA_ROLE,
            "type": METADATA_TYPE,
        },
    }


def _decoy_memory_data() -> dict:
    return {
        "content": DECOY_CONTENT,
        "user_id": USER_ID,
        "category": DECOY_CATEGORY,
        "created_at": DECOY_CREATED_AT,
        "updated_at": DECOY_UPDATED_AT,
        "hash": DECOY_PAYLOAD_HASH,
        "actor_id": "decoy-actor",
        "role": "decoy-role",
        "type": "decoy-type",
        "metadata": {
            "category": DECOY_CATEGORY,
            "created_at": DECOY_CREATED_AT,
            "updated_at": DECOY_UPDATED_AT,
            "hash": DECOY_METADATA_HASH,
            "data": DECOY_METADATA_DATA,
            "actor_id": "decoy-actor",
            "role": "decoy-role",
            "type": "decoy-type",
        },
    }


def _add_collision_memory(adapter: StorageAdapter, content: str = COLLISION_CONTENT) -> int:
    memory_id = adapter.add_memory(_collision_memory_data(content))
    assert memory_id > 0
    return memory_id


def _add_decoy_memory(adapter: StorageAdapter) -> int:
    memory_id = adapter.add_memory(_decoy_memory_data())
    assert memory_id > 0
    return memory_id


def _seed_collision_dataset(adapter: StorageAdapter) -> None:
    _add_collision_memory(adapter)
    _add_decoy_memory(adapter)


def _memory_texts(memories) -> list[str]:
    return [memory["memory"] for memory in memories]


def _agent_collision_memory() -> dict:
    return {
        "memory": COLLISION_CONTENT,
        "data": COLLISION_CONTENT,
        "document": COLLISION_CONTENT,
        "category": PAYLOAD_CATEGORY,
        "hash": PAYLOAD_HASH,
        "actor_id": PAYLOAD_ACTOR_ID,
        "role": PAYLOAD_ROLE,
        "type": PAYLOAD_TYPE,
        "metadata": {
            "category": METADATA_CATEGORY,
            "hash": METADATA_HASH,
            "data": METADATA_DATA,
            "actor_id": METADATA_ACTOR_ID,
            "role": METADATA_ROLE,
            "type": METADATA_TYPE,
        },
    }


# ------------------------------------------------------------------ #
# SQLite / PGVector / OceanBase: _build_db_filters translation layer
# ------------------------------------------------------------------ #


def test_sqlite_collision_keys_created_at_updated_at_category_default_to_payload(sqlite_adapter):
    # payload.created_at / payload.category are intentionally dropped when bare
    # created_at / category appear first: _build_db_filters keeps first-write-wins.
    assert sqlite_adapter._build_db_filters(
        filters={
            "created_at": PAYLOAD_CREATED_AT,
            "updated_at": PAYLOAD_UPDATED_AT,
            "category": PAYLOAD_CATEGORY,
            "metadata.created_at": METADATA_CREATED_AT,
            "metadata.updated_at": METADATA_UPDATED_AT,
            "metadata.category": METADATA_CATEGORY,
            "payload.created_at": METADATA_CREATED_AT,
            "payload.category": METADATA_CATEGORY,
        },
    ) == {
        "created_at": PAYLOAD_CREATED_AT,
        "updated_at": PAYLOAD_UPDATED_AT,
        "category": PAYLOAD_CATEGORY,
        "metadata.created_at": METADATA_CREATED_AT,
        "metadata.updated_at": METADATA_UPDATED_AT,
        "metadata.category": METADATA_CATEGORY,
    }


def test_sqlite_collision_keys_role_and_type_default_to_payload(sqlite_adapter):
    assert sqlite_adapter._build_db_filters(
        filters={
            "role": PAYLOAD_ROLE,
            "type": PAYLOAD_TYPE,
            "metadata.role": METADATA_ROLE,
            "metadata.type": METADATA_TYPE,
        },
    ) == {
        "role": PAYLOAD_ROLE,
        "type": PAYLOAD_TYPE,
        "metadata.role": METADATA_ROLE,
        "metadata.type": METADATA_TYPE,
    }


def test_sqlite_collision_keys_actor_id_and_sparse_embedding_default_to_payload(sqlite_adapter):
    assert sqlite_adapter._build_db_filters(
        filters={
            "actor_id": PAYLOAD_ACTOR_ID,
            "sparse_embedding": "payload-sparse",
            "metadata.actor_id": METADATA_ACTOR_ID,
            "metadata.sparse_embedding": "meta-sparse",
            "payload.actor_id": "explicit-payload-actor",
        },
    ) == {
        "actor_id": PAYLOAD_ACTOR_ID,
        "sparse_embedding": "payload-sparse",
        "metadata.actor_id": METADATA_ACTOR_ID,
        "metadata.sparse_embedding": "meta-sparse",
    }


def test_sqlite_unqualified_hash_and_data_map_to_metadata_not_payload(sqlite_adapter):
    assert sqlite_adapter._build_db_filters(
        filters={
            "hash": METADATA_HASH,
            "data": METADATA_DATA,
            "metadata.data": METADATA_DATA,
            "payload.hash": PAYLOAD_HASH,
            "payload.data": COLLISION_CONTENT,
        },
    ) == {
        "metadata.hash": METADATA_HASH,
        "metadata.data": METADATA_DATA,
        "hash": PAYLOAD_HASH,
        "data": COLLISION_CONTENT,
    }


def test_sqlite_system_scope_keys_coexist_with_metadata_filters(sqlite_adapter):
    assert sqlite_adapter._build_db_filters(
        user_id="scope-user",
        agent_id="scope-agent",
        run_id="scope-run",
        filters={
            "metadata.user_id": "meta-user",
            "metadata.agent_id": "meta-agent",
            "metadata.run_id": "meta-run",
        },
    ) == {
        "user_id": "scope-user",
        "agent_id": "scope-agent",
        "run_id": "scope-run",
        "metadata.user_id": "meta-user",
        "metadata.agent_id": "meta-agent",
        "metadata.run_id": "meta-run",
    }


def test_oceanbase_collision_keys_keep_plain_and_metadata_filters_distinct():
    adapter = _oceanbase_adapter()

    assert adapter._build_db_filters(
        filters={
            "hash": PAYLOAD_HASH,
            "data": COLLISION_CONTENT,
            "category": PAYLOAD_CATEGORY,
            "created_at": PAYLOAD_CREATED_AT,
            "updated_at": PAYLOAD_UPDATED_AT,
            "metadata.hash": METADATA_HASH,
            "metadata.category": METADATA_CATEGORY,
        },
    ) == {
        "hash": PAYLOAD_HASH,
        "document": COLLISION_CONTENT,
        "category": PAYLOAD_CATEGORY,
        "created_at": PAYLOAD_CREATED_AT,
        "updated_at": PAYLOAD_UPDATED_AT,
        "metadata.hash": METADATA_HASH,
        "metadata.category": METADATA_CATEGORY,
    }


def test_oceanbase_payload_prefix_strips_to_top_level_key():
    adapter = _oceanbase_adapter()

    assert adapter._build_db_filters(
        filters={
            "payload.hash": PAYLOAD_HASH,
            "payload.data": COLLISION_CONTENT,
            "payload.category": PAYLOAD_CATEGORY,
        },
    ) == {
        "hash": PAYLOAD_HASH,
        "document": COLLISION_CONTENT,
        "category": PAYLOAD_CATEGORY,
    }


def test_oceanbase_build_db_filters_keeps_metadata_and_plain_collision_keys():
    adapter = _oceanbase_adapter()

    assert adapter._build_db_filters(
        filters={"category": PAYLOAD_CATEGORY, "metadata.category": METADATA_CATEGORY},
    ) == {
        "category": PAYLOAD_CATEGORY,
        "metadata.category": METADATA_CATEGORY,
    }


def test_pgvector_adapter_translates_collision_keys_like_sqlite():
    adapter = _pgvector_adapter()

    assert adapter._build_db_filters(
        filters={
            "category": PAYLOAD_CATEGORY,
            "metadata.category": METADATA_CATEGORY,
            "hash": METADATA_HASH,
            "payload.hash": PAYLOAD_HASH,
        },
    ) == {
        "category": PAYLOAD_CATEGORY,
        "metadata.category": METADATA_CATEGORY,
        "metadata.hash": METADATA_HASH,
        "hash": PAYLOAD_HASH,
    }


def test_pgvector_system_user_id_coexists_with_metadata_user_id():
    adapter = _pgvector_adapter()

    assert adapter._build_db_filters(
        user_id="scope-user",
        filters={"metadata.user_id": "meta-user"},
    ) == {
        "user_id": "scope-user",
        "metadata.user_id": "meta-user",
    }


# ------------------------------------------------------------------ #
# Agent filters: OR semantics (top-level OR metadata)
# ------------------------------------------------------------------ #


def test_agent_filters_match_top_level_or_metadata_for_colliding_keys():
    memory = _agent_collision_memory()

    assert matches_memory_filters(memory, {"category": PAYLOAD_CATEGORY}) is True
    assert matches_memory_filters(memory, {"category": METADATA_CATEGORY}) is True
    assert matches_memory_filters(memory, {"hash": PAYLOAD_HASH}) is True
    assert matches_memory_filters(memory, {"hash": METADATA_HASH}) is True
    assert matches_memory_filters(memory, {"data": METADATA_DATA}) is True
    assert matches_memory_filters(memory, {"data": COLLISION_CONTENT}) is True
    assert matches_memory_filters(memory, {"metadata.data": METADATA_DATA}) is True
    assert matches_memory_filters(memory, {"actor_id": PAYLOAD_ACTOR_ID}) is True
    assert matches_memory_filters(memory, {"actor_id": METADATA_ACTOR_ID}) is True
    assert matches_memory_filters(memory, {"role": PAYLOAD_ROLE}) is True
    assert matches_memory_filters(memory, {"role": METADATA_ROLE}) is True
    assert matches_memory_filters(memory, {"type": PAYLOAD_TYPE}) is True
    assert matches_memory_filters(memory, {"type": METADATA_TYPE}) is True


def test_agent_filters_reject_values_absent_from_both_top_level_and_metadata():
    memory = _agent_collision_memory()

    assert matches_memory_filters(memory, {"category": DECOY_CATEGORY}) is False
    assert matches_memory_filters(memory, {"hash": DECOY_PAYLOAD_HASH}) is False
    assert matches_memory_filters(memory, {"metadata.data": DECOY_METADATA_DATA}) is False


def test_memory_matches_filter_routes_bare_hash_by_store_type(sqlite_adapter):
    memory = _agent_collision_memory()

    assert sqlite_adapter._memory_matches_filter(
        memory,
        "hash",
        METADATA_HASH,
        target_store=sqlite_adapter.vector_store,
    ) is True
    assert sqlite_adapter._memory_matches_filter(
        memory,
        "hash",
        PAYLOAD_HASH,
        target_store=sqlite_adapter.vector_store,
    ) is False

    oceanbase = _oceanbase_adapter()
    assert oceanbase._memory_matches_filter(
        memory,
        "hash",
        PAYLOAD_HASH,
        target_store=oceanbase.vector_store,
    ) is True
    assert oceanbase._memory_matches_filter(
        memory,
        "hash",
        METADATA_HASH,
        target_store=oceanbase.vector_store,
    ) is False


def test_memory_matches_filter_routes_bare_data_by_store_type(sqlite_adapter):
    memory = _agent_collision_memory()

    assert sqlite_adapter._memory_matches_filter(
        memory,
        "data",
        METADATA_DATA,
        target_store=sqlite_adapter.vector_store,
    ) is True
    assert sqlite_adapter._memory_matches_filter(
        memory,
        "data",
        COLLISION_CONTENT,
        target_store=sqlite_adapter.vector_store,
    ) is False

    oceanbase = _oceanbase_adapter()
    assert oceanbase._memory_matches_filter(
        memory,
        "data",
        COLLISION_CONTENT,
        target_store=oceanbase.vector_store,
    ) is True
    assert oceanbase._memory_matches_filter(
        memory,
        "data",
        METADATA_DATA,
        target_store=oceanbase.vector_store,
    ) is False


def test_memory_matches_filter_metadata_hash_prefers_nested_on_oceanbase():
    adapter = _oceanbase_adapter()
    memory = _agent_collision_memory()

    assert adapter._memory_matches_filter(
        memory,
        "metadata.hash",
        METADATA_HASH,
        target_store=adapter.vector_store,
    ) is True
    assert adapter._memory_matches_filter(
        memory,
        "metadata.hash",
        PAYLOAD_HASH,
        target_store=adapter.vector_store,
    ) is False


def test_memory_matches_filter_bare_unknown_key_falls_back_to_metadata_on_oceanbase():
    adapter = _oceanbase_adapter()
    memory = {
        "memory": COLLISION_CONTENT,
        "hash": PAYLOAD_HASH,
        "metadata": {"scope": "personal", "hash": METADATA_HASH},
    }

    assert adapter._memory_matches_filter(
        memory,
        "scope",
        "personal",
        target_store=adapter.vector_store,
    ) is True
    assert adapter._memory_matches_filter(
        memory,
        "scope",
        "work",
        target_store=adapter.vector_store,
    ) is False


def test_memory_matches_filter_treats_missing_data_as_absent_not_fallback():
    adapter = _oceanbase_adapter()
    memory = {
        "memory": COLLISION_CONTENT,
        "metadata": {"data": METADATA_DATA},
    }

    assert adapter._memory_matches_filter(
        memory,
        "payload.data",
        COLLISION_CONTENT,
        target_store=adapter.vector_store,
    ) is False


# ------------------------------------------------------------------ #
# SQLite integration: count / list / search honor the documented semantics
# ------------------------------------------------------------------ #


def test_sqlite_count_distinguishes_payload_and_metadata_collision_keys(sqlite_adapter):
    _seed_collision_dataset(sqlite_adapter)

    assert sqlite_adapter.count_all_memories(filters={"category": PAYLOAD_CATEGORY}) == 1
    assert sqlite_adapter.count_all_memories(filters={"metadata.category": METADATA_CATEGORY}) == 1
    assert sqlite_adapter.count_all_memories(filters={"created_at": PAYLOAD_CREATED_AT}) == 1
    assert sqlite_adapter.count_all_memories(filters={"metadata.created_at": METADATA_CREATED_AT}) == 1
    assert sqlite_adapter.count_all_memories(filters={"updated_at": PAYLOAD_UPDATED_AT}) == 1
    assert sqlite_adapter.count_all_memories(filters={"metadata.updated_at": METADATA_UPDATED_AT}) == 1
    assert sqlite_adapter.count_all_memories(filters={"hash": METADATA_HASH}) == 1
    assert sqlite_adapter.count_all_memories(filters={"payload.hash": PAYLOAD_HASH}) == 1
    assert sqlite_adapter.count_all_memories(filters={"data": METADATA_DATA}) == 1
    assert sqlite_adapter.count_all_memories(filters={"metadata.data": METADATA_DATA}) == 1
    assert sqlite_adapter.count_all_memories(filters={"payload.data": COLLISION_CONTENT}) == 1
    assert sqlite_adapter.count_all_memories(filters={"payload.data": DECOY_CONTENT}) == 1
    assert sqlite_adapter.count_all_memories(filters={"actor_id": PAYLOAD_ACTOR_ID}) == 1
    assert sqlite_adapter.count_all_memories(filters={"metadata.actor_id": METADATA_ACTOR_ID}) == 1
    assert sqlite_adapter.count_all_memories(filters={"role": PAYLOAD_ROLE}) == 1
    assert sqlite_adapter.count_all_memories(filters={"metadata.role": METADATA_ROLE}) == 1
    assert sqlite_adapter.count_all_memories(filters={"type": PAYLOAD_TYPE}) == 1
    assert sqlite_adapter.count_all_memories(filters={"metadata.type": METADATA_TYPE}) == 1
    assert sqlite_adapter.count_all_memories(filters={"category": "nonexistent"}) == 0


def test_sqlite_negative_payload_prefixed_keys_do_not_match_metadata(sqlite_adapter):
    _seed_collision_dataset(sqlite_adapter)

    assert sqlite_adapter.count_all_memories(filters={"payload.category": METADATA_CATEGORY}) == 0
    assert sqlite_adapter.count_all_memories(filters={"payload.created_at": METADATA_CREATED_AT}) == 0


def test_sqlite_negative_bare_data_does_not_match_payload_content(sqlite_adapter):
    _seed_collision_dataset(sqlite_adapter)

    assert sqlite_adapter.count_all_memories(filters={"data": COLLISION_CONTENT}) == 0


def test_sqlite_list_distinguishes_payload_and_metadata_collision_keys(sqlite_adapter):
    _seed_collision_dataset(sqlite_adapter)

    list_memories = sqlite_adapter.get_all_memories

    assert _memory_texts(list_memories(filters={"category": PAYLOAD_CATEGORY})) == [COLLISION_CONTENT]
    assert _memory_texts(list_memories(filters={"metadata.category": METADATA_CATEGORY})) == [COLLISION_CONTENT]
    assert _memory_texts(list_memories(filters={"created_at": PAYLOAD_CREATED_AT})) == [COLLISION_CONTENT]
    assert _memory_texts(list_memories(filters={"metadata.created_at": METADATA_CREATED_AT})) == [COLLISION_CONTENT]
    assert _memory_texts(list_memories(filters={"updated_at": PAYLOAD_UPDATED_AT})) == [COLLISION_CONTENT]
    assert _memory_texts(list_memories(filters={"metadata.updated_at": METADATA_UPDATED_AT})) == [COLLISION_CONTENT]
    assert _memory_texts(list_memories(filters={"hash": METADATA_HASH})) == [COLLISION_CONTENT]
    assert _memory_texts(list_memories(filters={"payload.hash": PAYLOAD_HASH})) == [COLLISION_CONTENT]
    assert _memory_texts(list_memories(filters={"data": METADATA_DATA})) == [COLLISION_CONTENT]
    assert _memory_texts(list_memories(filters={"metadata.data": METADATA_DATA})) == [COLLISION_CONTENT]
    assert _memory_texts(list_memories(filters={"payload.data": COLLISION_CONTENT})) == [COLLISION_CONTENT]
    assert _memory_texts(list_memories(filters={"actor_id": PAYLOAD_ACTOR_ID})) == [COLLISION_CONTENT]
    assert _memory_texts(list_memories(filters={"metadata.actor_id": METADATA_ACTOR_ID})) == [COLLISION_CONTENT]
    assert _memory_texts(list_memories(filters={"role": PAYLOAD_ROLE})) == [COLLISION_CONTENT]
    assert _memory_texts(list_memories(filters={"metadata.role": METADATA_ROLE})) == [COLLISION_CONTENT]
    assert _memory_texts(list_memories(filters={"type": PAYLOAD_TYPE})) == [COLLISION_CONTENT]
    assert _memory_texts(list_memories(filters={"metadata.type": METADATA_TYPE})) == [COLLISION_CONTENT]
    assert _memory_texts(list_memories(filters={"category": "nonexistent"})) == []


def test_sqlite_search_distinguishes_payload_and_metadata_collision_keys(sqlite_adapter):
    _seed_collision_dataset(sqlite_adapter)

    search = sqlite_adapter.search_memories
    common = {"query_embedding": None, "query": "alpha", "retrieval_mode": "fts"}

    assert _memory_texts(search(**common, filters={"category": PAYLOAD_CATEGORY})) == [COLLISION_CONTENT]
    assert _memory_texts(search(**common, filters={"metadata.category": METADATA_CATEGORY})) == [COLLISION_CONTENT]
    assert _memory_texts(search(**common, filters={"created_at": PAYLOAD_CREATED_AT})) == [COLLISION_CONTENT]
    assert _memory_texts(search(**common, filters={"metadata.created_at": METADATA_CREATED_AT})) == [COLLISION_CONTENT]
    assert _memory_texts(search(**common, filters={"updated_at": PAYLOAD_UPDATED_AT})) == [COLLISION_CONTENT]
    assert _memory_texts(search(**common, filters={"metadata.updated_at": METADATA_UPDATED_AT})) == [COLLISION_CONTENT]
    assert _memory_texts(
        search(**common, filters={"hash": METADATA_HASH, "data": METADATA_DATA})
    ) == [COLLISION_CONTENT]
    assert _memory_texts(search(**common, filters={"metadata.data": METADATA_DATA})) == [COLLISION_CONTENT]
    assert _memory_texts(search(**common, filters={"payload.hash": PAYLOAD_HASH})) == [COLLISION_CONTENT]
    assert _memory_texts(search(**common, filters={"payload.data": COLLISION_CONTENT})) == [COLLISION_CONTENT]
    assert _memory_texts(search(**common, filters={"actor_id": PAYLOAD_ACTOR_ID})) == [COLLISION_CONTENT]
    assert _memory_texts(search(**common, filters={"metadata.actor_id": METADATA_ACTOR_ID})) == [COLLISION_CONTENT]
    assert _memory_texts(search(**common, filters={"role": PAYLOAD_ROLE})) == [COLLISION_CONTENT]
    assert _memory_texts(search(**common, filters={"metadata.role": METADATA_ROLE})) == [COLLISION_CONTENT]
    assert _memory_texts(search(**common, filters={"type": PAYLOAD_TYPE})) == [COLLISION_CONTENT]
    assert _memory_texts(search(**common, filters={"metadata.type": METADATA_TYPE})) == [COLLISION_CONTENT]
    assert _memory_texts(search(**common, filters={"category": "nonexistent"})) == []


# ------------------------------------------------------------------ #
# OceanBase: real _generate_where_clause column-first behavior
# ------------------------------------------------------------------ #


@pytest.fixture
def oceanbase_where_store():
    """OceanBaseVectorStore with real table schema; no live database connection."""
    pytest.importorskip("pyobvector")
    from powermem.storage.oceanbase.models import create_memory_model
    from powermem.storage.oceanbase.oceanbase import OceanBaseVectorStore

    store = OceanBaseVectorStore.__new__(OceanBaseVectorStore)
    store.metadata_field = "metadata"
    store.text_field = "document"
    model = create_memory_model("memories", 3, include_sparse=False)
    store.table = model.__table__
    return store


def _compiled_where_sql(store, filters):
    from sqlalchemy.dialects import mysql

    clause = store._generate_where_clause(filters)[0]
    return str(
        clause.compile(
            dialect=mysql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_oceanbase_where_clause_metadata_hash_targets_json_not_hash_column(
    oceanbase_where_store,
):
    metadata_sql = _compiled_where_sql(
        oceanbase_where_store,
        {"metadata.hash": METADATA_HASH},
    )
    column_sql = _compiled_where_sql(
        oceanbase_where_store,
        {"hash": PAYLOAD_HASH},
    )

    assert metadata_sql != column_sql
    assert "metadata" in metadata_sql
    assert "hash" in metadata_sql


def test_oceanbase_where_clause_document_column_for_text_content(
    oceanbase_where_store,
):
    document_sql = _compiled_where_sql(
        oceanbase_where_store,
        {"document": COLLISION_CONTENT},
    )
    metadata_data_sql = _compiled_where_sql(
        oceanbase_where_store,
        {"metadata.data": METADATA_DATA},
    )

    assert "document" in document_sql
    assert document_sql != metadata_data_sql


def test_oceanbase_adapter_db_filters_use_document_for_payload_data():
    adapter = _oceanbase_adapter()
    store = adapter.vector_store
    store.text_field = "document"

    db_filters = adapter._build_db_filters(
        filters={"payload.data": COLLISION_CONTENT, "metadata.hash": METADATA_HASH},
        target_store=store,
    )

    assert db_filters == {
        "document": COLLISION_CONTENT,
        "metadata.hash": METADATA_HASH,
    }
