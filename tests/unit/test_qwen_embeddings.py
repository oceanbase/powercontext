from unittest.mock import Mock, patch

import pytest

from powermem.integrations.embeddings.config.base import BaseEmbedderConfig
from powermem.integrations.embeddings.qwen import QwenEmbedding


@pytest.fixture
def mock_dashscope():
    """Mock the dashscope module and TextEmbedding.call method"""
    with patch("powermem.integrations.embeddings.qwen.TextEmbedding") as mock_text_embedding:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.output = {
            'embeddings': [{'embedding': [0.1, 0.2, 0.3]}]
        }
        mock_text_embedding.call.return_value = mock_response
        yield mock_text_embedding


def test_embed_default_model(mock_dashscope):
    config = BaseEmbedderConfig(api_key="test_key")
    embedder = QwenEmbedding(config)
    
    # Update mock response for this test
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.output = {
        'embeddings': [{'embedding': [0.1, 0.2, 0.3]}]
    }
    mock_dashscope.call.return_value = mock_response

    result = embedder.embed("Hello world")

    mock_dashscope.call.assert_called_once_with(
        api_key="test_key",
        model="text-embedding-v4",
        input="Hello world",
        dimension=1536,
        text_type="document",
    )
    assert result == [0.1, 0.2, 0.3]


def test_embed_custom_model(mock_dashscope):
    config = BaseEmbedderConfig(model="custom-model", embedding_dims=1024, api_key="test_key")
    embedder = QwenEmbedding(config)
    
    # Update mock response for this test
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.output = {
        'embeddings': [{'embedding': [0.4, 0.5, 0.6]}]
    }
    mock_dashscope.call.return_value = mock_response

    result = embedder.embed("Test embedding")

    mock_dashscope.call.assert_called_once_with(
        api_key="test_key",
        model="custom-model",
        input="Test embedding",
        dimension=1024,
        text_type="document",
    )
    assert result == [0.4, 0.5, 0.6]


def test_embed_removes_newlines(mock_dashscope):
    config = BaseEmbedderConfig(api_key="test_key")
    embedder = QwenEmbedding(config)
    
    # Update mock response for this test
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.output = {
        'embeddings': [{'embedding': [0.7, 0.8, 0.9]}]
    }
    mock_dashscope.call.return_value = mock_response

    result = embedder.embed("Hello\nworld")

    mock_dashscope.call.assert_called_once_with(
        api_key="test_key",
        model="text-embedding-v4",
        input="Hello world",
        dimension=1536,
        text_type="document",
    )
    assert result == [0.7, 0.8, 0.9]


def test_embed_with_api_key_in_config(mock_dashscope):
    config = BaseEmbedderConfig(api_key="test_api_key")
    embedder = QwenEmbedding(config)
    
    # Update mock response for this test
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.output = {
        'embeddings': [{'embedding': [1.0, 1.1, 1.2]}]
    }
    mock_dashscope.call.return_value = mock_response

    result = embedder.embed("Testing API key")

    mock_dashscope.call.assert_called_once_with(
        api_key="test_api_key",
        model="text-embedding-v4",
        input="Testing API key",
        dimension=1536,
        text_type="document",
    )
    assert result == [1.0, 1.1, 1.2]


def test_embed_uses_environment_api_key(mock_dashscope, monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "env_key")
    config = BaseEmbedderConfig()
    embedder = QwenEmbedding(config)
    
    # Update mock response for this test
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.output = {
        'embeddings': [{'embedding': [1.3, 1.4, 1.5]}]
    }
    mock_dashscope.call.return_value = mock_response

    result = embedder.embed("Environment key test")

    mock_dashscope.call.assert_called_once_with(
        api_key="env_key",
        model="text-embedding-v4",
        input="Environment key test",
        dimension=1536,
        text_type="document",
    )
    assert result == [1.3, 1.4, 1.5]


def test_embed_with_memory_action(mock_dashscope):
    config = BaseEmbedderConfig(
        api_key="test_key",
        memory_add_embedding_type="query",
        memory_search_embedding_type="document"
    )
    embedder = QwenEmbedding(config)
    
    # Update mock response for this test
    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.output = {
        'embeddings': [{'embedding': [2.0, 2.1, 2.2]}]
    }
    mock_dashscope.call.return_value = mock_response

    result = embedder.embed("Test with memory action", memory_action="add")

    mock_dashscope.call.assert_called_once_with(
        api_key="test_key",
        model="text-embedding-v4",
        input="Test with memory action",
        dimension=1536,
        text_type="query",
    )
    assert result == [2.0, 2.1, 2.2]


def test_embed_api_error(mock_dashscope):
    config = BaseEmbedderConfig(api_key="test_key")
    embedder = QwenEmbedding(config)
    
    # Mock API error response
    mock_response = Mock()
    mock_response.status_code = 400
    mock_response.message = "Bad Request"
    mock_dashscope.call.return_value = mock_response

    with pytest.raises(Exception, match="API request failed with status 400"):
        embedder.embed("Test error")


def test_embed_no_api_key():
    config = BaseEmbedderConfig()
    with pytest.raises(ValueError, match="API key is required"):
        QwenEmbedding(config)


# --- VL (multimodal) model tests ---


@pytest.fixture
def mock_multimodal():
    """Mock dashscope MultiModalEmbedding and MultiModalEmbeddingItemText."""
    with patch("powermem.integrations.embeddings.qwen.MultiModalEmbedding") as mock_mm, \
         patch("powermem.integrations.embeddings.qwen.MultiModalEmbeddingItemText") as mock_item_cls:
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.output = {
            'embeddings': [{'embedding': [0.4, 0.5, 0.6]}]
        }
        mock_mm.call.return_value = mock_response
        yield mock_mm, mock_item_cls


def test_vl_model_detection():
    """_is_vl_model detects VL models by name substring (case-insensitive)."""
    vl_config = BaseEmbedderConfig(model="qwen3-vl-embedding", api_key="test_key")
    vl_embedder = QwenEmbedding(vl_config)
    assert vl_embedder._is_vl_model() is True

    upper_config = BaseEmbedderConfig(model="Qwen3-VL-Embedding", api_key="test_key")
    upper_embedder = QwenEmbedding(upper_config)
    assert upper_embedder._is_vl_model() is True

    text_config = BaseEmbedderConfig(model="text-embedding-v4", api_key="test_key")
    text_embedder = QwenEmbedding(text_config)
    assert text_embedder._is_vl_model() is False


def test_embed_vl_uses_multimodal_api(mock_dashscope, mock_multimodal):
    """VL model should route to MultiModalEmbedding, not TextEmbedding."""
    mock_mm, mock_item_cls = mock_multimodal

    config = BaseEmbedderConfig(model="qwen3-vl-embedding", embedding_dims=1536, api_key="test_key")
    embedder = QwenEmbedding(config)

    result = embedder.embed("Test VL embedding")

    # MultiModalEmbeddingItemText created with correct args
    mock_item_cls.assert_called_once_with(text="Test VL embedding", factor=1.0)

    # MultiModalEmbedding.call used (not TextEmbedding.call)
    mock_mm.call.assert_called_once_with(
        api_key="test_key",
        model="qwen3-vl-embedding",
        input=[mock_item_cls.return_value],
        dimension=1536,
    )
    # TextEmbedding.call should NOT have been called
    mock_dashscope.call.assert_not_called()

    assert result == [0.4, 0.5, 0.6]


def test_embed_vl_ignores_memory_action(mock_dashscope, mock_multimodal):
    """VL model should not pass text_type even when memory_action is set."""
    mock_mm, mock_item_cls = mock_multimodal

    config = BaseEmbedderConfig(model="qwen3-vl-embedding", api_key="test_key")
    embedder = QwenEmbedding(config)

    embedder.embed("Search query", memory_action="search")

    call_kwargs = mock_mm.call.call_args[1]
    assert "text_type" not in call_kwargs


def test_embed_vl_api_error(mock_dashscope, mock_multimodal):
    """VL model should handle API errors properly."""
    mock_mm, _ = mock_multimodal
    mock_response = Mock()
    mock_response.status_code = 500
    mock_response.message = "Internal Server Error"
    mock_mm.call.return_value = mock_response

    config = BaseEmbedderConfig(model="qwen3-vl-embedding", api_key="test_key")
    embedder = QwenEmbedding(config)

    with pytest.raises(Exception, match="API request failed with status 500"):
        embedder.embed("Test error")


def test_multimodal_config_override_true(mock_dashscope, mock_multimodal):
    """multimodal=True forces VL path regardless of model name."""
    mock_mm, mock_item_cls = mock_multimodal

    # Model name has no "vl", but multimodal=True overrides
    config = BaseEmbedderConfig(model="my-custom-model", api_key="test_key", multimodal=True)
    embedder = QwenEmbedding(config)

    assert embedder._is_vl_model() is True
    result = embedder.embed("Test override")

    # Should use MultiModalEmbedding, not TextEmbedding
    mock_mm.call.assert_called_once()
    mock_dashscope.call.assert_not_called()


def test_multimodal_config_override_false(mock_dashscope):
    """multimodal=False forces text path even if model name contains 'vl'."""
    config = BaseEmbedderConfig(model="qwen3-vl-embedding", api_key="test_key", multimodal=False)
    embedder = QwenEmbedding(config)

    assert embedder._is_vl_model() is False

    mock_response = Mock()
    mock_response.status_code = 200
    mock_response.output = {'embeddings': [{'embedding': [0.1, 0.2]}]}
    mock_dashscope.call.return_value = mock_response

    result = embedder.embed("Force text path")
    # Should use TextEmbedding
    mock_dashscope.call.assert_called_once()


def test_multimodal_config_none_auto_detect():
    """multimodal=None (default) falls back to model name auto-detection."""
    # VL name -> True
    config_vl = BaseEmbedderConfig(model="qwen3-vl-embedding", api_key="test_key", multimodal=None)
    embedder_vl = QwenEmbedding(config_vl)
    assert embedder_vl._is_vl_model() is True

    # Text name -> False
    config_text = BaseEmbedderConfig(model="text-embedding-v4", api_key="test_key", multimodal=None)
    embedder_text = QwenEmbedding(config_text)
    assert embedder_text._is_vl_model() is False

    # No multimodal field at all -> also auto-detect
    config_no_field = BaseEmbedderConfig(model="qwen3-vl-embedding", api_key="test_key")
    embedder_no_field = QwenEmbedding(config_no_field)
    assert embedder_no_field._is_vl_model() is True
