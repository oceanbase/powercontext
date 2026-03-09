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
