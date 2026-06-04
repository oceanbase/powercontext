"""Tests for the built-in zero-config default embedder (issues #940/#941).

The embedder wraps pyseekdb's ``DefaultEmbeddingFunction``. We mock that out so
the test never has to download an ONNX model — what we want to verify is that
PowerMem wires the default correctly and that ``MemoryConfig()`` no longer
requires an OPENAI key to be constructible.
"""

from __future__ import annotations

import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# Import the submodule explicitly so ``pyseekdb_default`` is a resolved
# attribute of the package before any test patches it. ``unittest.mock`` on
# Python 3.11 does not auto-import the final submodule when resolving a dotted
# patch target, so patch.object(<module>, ...) is used instead of a string.
from powermem.integrations.embeddings import pyseekdb_default

# ---------------------------------------------------------------------------
# Embedder behaviour
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_default_fn(monkeypatch):
    """Mock pyseekdb.DefaultEmbeddingFunction so no model is downloaded.

    We also stub out ``_load_sentence_transformer_with_fallback``: the
    embedder pre-warms a ``sentence_transformers`` model in ``__init__``
    before creating the DefaultEmbeddingFunction. Without this stub the test
    would import ``sentence_transformers`` (an optional extra) and hit the
    network on a cache miss, defeating the point of mocking the embedder.
    """
    pyseekdb_module = ModuleType("pyseekdb")
    client_module = ModuleType("pyseekdb.client")
    embedding_module = ModuleType("pyseekdb.client.embedding_function")

    mock_cls = MagicMock(name="DefaultEmbeddingFunction")
    embedding_module.DefaultEmbeddingFunction = mock_cls
    client_module.embedding_function = embedding_module
    pyseekdb_module.client = client_module

    monkeypatch.setitem(sys.modules, "pyseekdb", pyseekdb_module)
    monkeypatch.setitem(sys.modules, "pyseekdb.client", client_module)
    monkeypatch.setitem(
        sys.modules,
        "pyseekdb.client.embedding_function",
        embedding_module,
    )

    with patch.object(pyseekdb_default, "_load_sentence_transformer_with_fallback"):
        instance = MagicMock()
        # Return one 384-dim vector per input document, matching all-MiniLM-L6-v2.
        instance.side_effect = lambda docs: [[0.1] * 384 for _ in docs]
        mock_cls.return_value = instance
        yield mock_cls


def test_embed_returns_384_dim_vector(mock_default_fn):
    from powermem.integrations.embeddings.pyseekdb_default import (
        PyseekdbDefaultEmbedding,
    )

    embedder = PyseekdbDefaultEmbedding()
    vec = embedder.embed("hello world")

    assert isinstance(vec, list)
    assert len(vec) == 384
    mock_default_fn.assert_called_once()


def test_embed_batch_returns_per_input_vectors(mock_default_fn):
    from powermem.integrations.embeddings.pyseekdb_default import (
        PyseekdbDefaultEmbedding,
    )

    embedder = PyseekdbDefaultEmbedding()
    vectors = embedder.embed_batch(["a", "b", "c"])

    assert len(vectors) == 3
    assert all(len(v) == 384 for v in vectors)


def test_embed_batch_empty_input_short_circuits(mock_default_fn):
    from powermem.integrations.embeddings.pyseekdb_default import (
        PyseekdbDefaultEmbedding,
    )

    embedder = PyseekdbDefaultEmbedding()

    assert embedder.embed_batch([]) == []


def test_embed_rejects_none(mock_default_fn):
    from powermem.integrations.embeddings.pyseekdb_default import (
        PyseekdbDefaultEmbedding,
    )

    embedder = PyseekdbDefaultEmbedding()

    with pytest.raises(ValueError):
        embedder.embed(None)


def test_config_defaults_match_pyseekdb(mock_default_fn):
    from powermem.integrations.embeddings.pyseekdb_default import (
        PyseekdbDefaultEmbedding,
    )

    embedder = PyseekdbDefaultEmbedding()

    assert embedder.config.model == "all-MiniLM-L6-v2"
    assert embedder.config.embedding_dims == 384


# ---------------------------------------------------------------------------
# Provider registry / factory wiring
# ---------------------------------------------------------------------------


def test_default_provider_is_registered():
    # Importing providers populates the registry via __pydantic_init_subclass__.
    import powermem.integrations.embeddings.config.providers  # noqa: F401
    from powermem.integrations.embeddings.config.base import BaseEmbedderConfig

    assert BaseEmbedderConfig.has_provider("default")
    assert (
        BaseEmbedderConfig.get_provider_class_path("default")
        == "powermem.integrations.embeddings.pyseekdb_default.PyseekdbDefaultEmbedding"
    )


def test_factory_resolves_default_provider(mock_default_fn):
    from powermem.integrations.embeddings.config.providers import (
        PyseekdbDefaultEmbeddingConfig,
    )
    from powermem.integrations.embeddings.factory import EmbedderFactory

    embedder = EmbedderFactory.create(
        "default", PyseekdbDefaultEmbeddingConfig(), vector_config=None
    )

    assert embedder.embed("hi")  # round-trip via factory


# ---------------------------------------------------------------------------
# Zero-config MemoryConfig (the main #941 acceptance criterion)
# ---------------------------------------------------------------------------


def test_memory_config_default_embedder_requires_no_api_key(monkeypatch):
    """MemoryConfig() with no .env should pick the local default embedder."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)

    from powermem.configs import MemoryConfig
    from powermem.integrations.embeddings.config.providers import (
        PyseekdbDefaultEmbeddingConfig,
    )

    cfg = MemoryConfig()

    assert isinstance(cfg.embedder, PyseekdbDefaultEmbeddingConfig)
    assert cfg.embedder.provider == "default"
    assert cfg.embedder.api_key is None
    assert cfg.embedder.embedding_dims == 384
