from unittest.mock import MagicMock

import pytest

from powermem import Memory
from powermem.config_loader import create_config
from powermem.configs import MemoryConfig
from powermem.core.async_memory import AsyncMemory


def _issue_1118_config(tmp_path):
    with pytest.warns(DeprecationWarning):
        config = create_config(
            database_provider="sqlite",
            database_config={
                "database_path": str(tmp_path / "issue_1118.sqlite"),
                "collection_name": "issue_1118_memories",
            },
            llm_provider="noop",
            llm_model="noop",
            embedding_provider="mock",
            embedding_dims=16,
        )
    config["intelligence"] = {"enabled": False}
    config["graph_store"] = {"enabled": False}
    return config


def test_memory_dict_config_graph_store_disabled_does_not_create_graph(tmp_path, monkeypatch):
    graph_create = MagicMock()
    monkeypatch.setattr("powermem.core.memory.GraphStoreFactory.create", graph_create)

    memory = Memory(config=_issue_1118_config(tmp_path))

    assert memory.enable_graph is False
    assert memory.graph_store is None
    graph_create.assert_not_called()


def test_memory_config_normalizes_disabled_graph_store_to_none(tmp_path):
    memory_config = MemoryConfig(**_issue_1118_config(tmp_path))

    assert memory_config.graph_store is None


def test_memory_config_path_graph_store_disabled_does_not_create_graph(
    tmp_path,
    monkeypatch,
):
    graph_create = MagicMock()
    monkeypatch.setattr("powermem.core.memory.GraphStoreFactory.create", graph_create)

    memory_config = MemoryConfig(**_issue_1118_config(tmp_path))
    memory = Memory(config=memory_config)

    assert memory.enable_graph is False
    assert memory.graph_store is None
    graph_create.assert_not_called()


@pytest.mark.asyncio
async def test_async_memory_graph_store_disabled_matches_sync_paths(
    tmp_path,
    monkeypatch,
):
    async_graph_create = MagicMock()
    monkeypatch.setattr(
        "powermem.core.async_memory.GraphStoreFactory.create",
        async_graph_create,
    )

    dict_memory = AsyncMemory(config=_issue_1118_config(tmp_path))
    memory_config = MemoryConfig(**_issue_1118_config(tmp_path))
    config_memory = AsyncMemory(config=memory_config)

    assert dict_memory.enable_graph is False
    assert dict_memory.graph_store is None
    assert config_memory.enable_graph is False
    assert config_memory.graph_store is None
    async_graph_create.assert_not_called()


def test_memory_with_disabled_graph_store_can_add_and_search(tmp_path):
    memory = Memory(config=_issue_1118_config(tmp_path))

    add_result = memory.add("User likes black coffee", user_id="issue_1118")
    search_result = memory.search("coffee", user_id="issue_1118")

    assert add_result["results"]
    assert search_result["results"]
    assert add_result.get("relations") in (None, [])
    assert search_result.get("relations") in (None, [])


def test_memory_config_enabled_graph_store_wrapper_uses_provider_config(tmp_path):
    config = _issue_1118_config(tmp_path)
    config["graph_store"] = {
        "enabled": True,
        "provider": "oceanbase",
        "config": {
            "host": "127.0.0.1",
            "embedding_model_dims": 16,
        },
        "custom_prompt": "Extract durable relations only.",
    }

    memory_config = MemoryConfig(**config)

    assert type(memory_config.graph_store).__name__ == "OceanBaseGraphConfig"
    assert memory_config.graph_store.host == "127.0.0.1"
    assert memory_config.graph_store.embedding_model_dims == 16
    assert memory_config.graph_store.custom_prompt == "Extract durable relations only."
