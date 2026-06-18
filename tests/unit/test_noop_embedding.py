"""Unit tests for the NoopEmbedding provider (EMBEDDING_PROVIDER=none).

Covers:
- NoopEmbedding.is_noop flag and return values
- EmbedderFactory routing for provider="none"
- BaseEmbedderConfig provider registry
- config_loader env-var path (EMBEDDING_PROVIDER=none)
- Memory._is_embedding_disabled / _embed helpers (including exception handling)
- StorageAdapter skips embed call when embedding is noop
- StorageAdapter search returns [] for empty query_embedding
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import powermem.config_loader as config_loader
import powermem.settings as settings
from powermem.integrations.embeddings.config.base import BaseEmbedderConfig
from powermem.integrations.embeddings.factory import EmbedderFactory
from powermem.integrations.embeddings.noop import NoopEmbedding


# ---------------------------------------------------------------------------
# NoopEmbedding basics
# ---------------------------------------------------------------------------


def test_noop_embedding_has_is_noop_flag():
    e = NoopEmbedding()
    assert e.is_noop is True


def test_noop_embedding_embed_returns_empty_list():
    e = NoopEmbedding()
    assert e.embed("hello world") == []


def test_noop_embedding_embed_batch_returns_empty_lists():
    e = NoopEmbedding()
    assert e.embed_batch(["a", "b", "c"]) == [[], [], []]


def test_noop_embedding_embed_batch_empty_input():
    e = NoopEmbedding()
    assert e.embed_batch([]) == []


# ---------------------------------------------------------------------------
# Factory and registry
# ---------------------------------------------------------------------------


def test_factory_returns_noop_embedding_for_none_provider():
    e = EmbedderFactory.create("none", None, None)
    assert isinstance(e, NoopEmbedding)
    assert e.is_noop is True


def test_noop_provider_is_registered():
    import powermem.integrations.embeddings.config.providers  # noqa: F401

    assert BaseEmbedderConfig.has_provider("none")
    assert (
        BaseEmbedderConfig.get_provider_class_path("none")
        == "powermem.integrations.embeddings.noop.NoopEmbedding"
    )


# ---------------------------------------------------------------------------
# Config loader env path
# ---------------------------------------------------------------------------


def test_load_config_from_env_supports_none_embedding(monkeypatch):
    monkeypatch.setattr(config_loader, "_DEFAULT_ENV_FILE", None, raising=False)
    monkeypatch.setattr(settings, "_DEFAULT_ENV_FILE", None, raising=False)
    new_config = dict(config_loader.EmbeddingSettings.model_config)
    new_config["env_file"] = None
    monkeypatch.setattr(config_loader.EmbeddingSettings, "model_config", new_config)
    monkeypatch.setenv("EMBEDDING_PROVIDER", "none")

    cfg = config_loader.load_config_from_env()

    assert cfg["embedder"]["provider"] == "none"


# ---------------------------------------------------------------------------
# Memory._is_embedding_disabled / _embed helpers
# ---------------------------------------------------------------------------


class _StubMemory:
    """Minimal stand-in for Memory that exercises the new helpers."""

    def __init__(self, embedding, embedding_provider="none"):
        self.embedding = embedding
        self.embedding_provider = embedding_provider

    # Bind helpers directly
    from powermem.core.memory import Memory
    _is_embedding_disabled = Memory._is_embedding_disabled
    _embed = Memory._embed


def test_is_embedding_disabled_true_for_none_provider():
    stub = _StubMemory(embedding=MagicMock(), embedding_provider="none")
    assert stub._is_embedding_disabled() is True


def test_is_embedding_disabled_true_for_noop_instance():
    noop = NoopEmbedding()
    stub = _StubMemory(embedding=noop, embedding_provider="other")
    assert stub._is_embedding_disabled() is True


def test_is_embedding_disabled_false_for_real_provider():
    stub = _StubMemory(embedding=MagicMock(), embedding_provider="openai")
    assert stub._is_embedding_disabled() is False


def test_embed_returns_none_when_disabled():
    stub = _StubMemory(embedding=MagicMock(), embedding_provider="none")
    result = stub._embed("some text")
    stub.embedding.embed.assert_not_called()
    assert result is None


def test_embed_calls_embedding_when_enabled():
    mock_embedding = MagicMock()
    mock_embedding.embed.return_value = [0.1] * 384
    stub = _StubMemory(embedding=mock_embedding, embedding_provider="default")
    stub.embedding.is_noop = False

    result = stub._embed("hello")

    mock_embedding.embed.assert_called_once_with("hello")
    assert result == [0.1] * 384


def test_embed_returns_none_on_embedding_exception():
    """_embed() must swallow exceptions from embed() and return None."""
    mock_embedding = MagicMock()
    mock_embedding.is_noop = False
    mock_embedding.embed.side_effect = RuntimeError("model unavailable")
    stub = _StubMemory(embedding=mock_embedding, embedding_provider="default")

    result = stub._embed("hello")

    assert result is None
    mock_embedding.embed.assert_called_once()


# ---------------------------------------------------------------------------
# StorageAdapter skips embed when noop
# ---------------------------------------------------------------------------


def test_adapter_skips_embed_call_when_noop(tmp_path):
    """StorageAdapter must not call embed() when embedding_service.is_noop is True."""
    from powermem.storage.adapter import StorageAdapter

    noop_embedder = NoopEmbedding()
    # Wrap to detect if embed() is ever called
    noop_embedder.embed = MagicMock(side_effect=AssertionError("embed must not be called"))

    mock_store = MagicMock()
    mock_store.collection_name = "test"
    mock_store.upsert.return_value = [1]

    adapter = StorageAdapter(mock_store, embedding_service=noop_embedder)
    adapter.add_memory({
        "content": "test memory",
        "user_id": "u1",
        "agent_id": "",
        "run_id": "",
    })

    noop_embedder.embed.assert_not_called()


def test_adapter_falls_back_to_mock_vector_when_precomputed_embedding_is_empty():
    """When memory_data carries embedding=[] (returned by NoopEmbedding), adapter
    must fall back to mock vector instead of passing vector_size=0 to the store."""
    from powermem.storage.adapter import StorageAdapter

    mock_store = MagicMock()
    mock_store.collection_name = "test"
    mock_store.insert.return_value = [1]

    adapter = StorageAdapter(mock_store, embedding_service=None)
    adapter.add_memory({
        "content": "test memory",
        "embedding": [],  # empty vector from NoopEmbedding
        "user_id": "u1",
        "agent_id": "",
        "run_id": "",
    })

    # insert([vector], [payload]) must have been called with non-empty vector
    mock_store.insert.assert_called_once()
    stored_vector = mock_store.insert.call_args[0][0][0]
    assert len(stored_vector) > 0, "adapter must not store a zero-length vector"


def test_adapter_search_returns_empty_for_empty_query_embedding():
    """search_memories with an empty query_embedding (returned by NoopEmbedding)
    must return [] instead of attempting a vector search."""
    from powermem.storage.adapter import StorageAdapter

    mock_store = MagicMock()
    mock_store.collection_name = "test"

    adapter = StorageAdapter(mock_store, embedding_service=None)
    result = adapter.search_memories(
        query_embedding=[],  # empty from NoopEmbedding
        user_id="u1",
        query="anything",
    )

    assert result == []
    mock_store.search.assert_not_called()


# ---------------------------------------------------------------------------
# MemoryConfig with NoopEmbeddingConfig requires no API key
# ---------------------------------------------------------------------------


def test_memory_config_none_embedding_requires_no_api_key(monkeypatch):
    """MemoryConfig with EMBEDDING_PROVIDER=none must not require an API key."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)

    from powermem.configs import MemoryConfig
    from powermem.integrations.embeddings.config.providers import NoopEmbeddingConfig

    cfg = MemoryConfig(embedder=NoopEmbeddingConfig())

    assert isinstance(cfg.embedder, NoopEmbeddingConfig)
    assert cfg.embedder.provider == "none"
    assert cfg.embedder.api_key is None
