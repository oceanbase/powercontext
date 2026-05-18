import json
from unittest.mock import patch, MagicMock

import pytest

from powermem.integrations.embeddings.config.base import BaseEmbedderConfig
from powermem.integrations.embeddings.config.providers import OBMassEmbeddingConfig
from powermem.integrations.embeddings.ob_mass import OBMassEmbedding


# -- Config registration tests --

def test_ob_mass_registered_in_provider_registry():
    """OBMassEmbeddingConfig should be discoverable via the base registry."""
    assert BaseEmbedderConfig.has_provider("ob_mass")
    assert BaseEmbedderConfig.get_provider_config_cls("ob_mass") is OBMassEmbeddingConfig
    assert BaseEmbedderConfig.get_provider_class_path("ob_mass") == (
        "powermem.integrations.embeddings.ob_mass.OBMassEmbedding"
    )


# -- Config validation tests --

def test_config_fields():
    cfg = OBMassEmbeddingConfig(
        api_key="test-key",
        model="qwen3-vl-embedding",
        embedding_dims=1536,
        openai_base_url="https://ob.example.com/v1",
        project_id="proj-123",
        request_id="req-abc",
    )
    assert cfg.api_key == "test-key"
    assert cfg.model == "qwen3-vl-embedding"
    assert cfg.embedding_dims == 1536
    assert cfg.openai_base_url == "https://ob.example.com/v1"
    assert cfg.project_id == "proj-123"
    assert cfg.request_id == "req-abc"


# -- Constructor validation tests --

def test_missing_api_key_raises():
    cfg = OBMassEmbeddingConfig(
        openai_base_url="https://ob.example.com/v1",
        project_id="proj-123",
    )
    with pytest.raises(ValueError, match="API key is required"):
        OBMassEmbedding(cfg)


def test_missing_base_url_raises():
    cfg = OBMassEmbeddingConfig(
        api_key="test-key",
        project_id="proj-123",
    )
    with pytest.raises(ValueError, match="Base URL is required"):
        OBMassEmbedding(cfg)


def test_missing_project_id_is_allowed():
    cfg = OBMassEmbeddingConfig(
        api_key="test-key",
        openai_base_url="https://ob.example.com/v1",
    )
    embedder = OBMassEmbedding(cfg)
    assert embedder.project_id is None


# -- Helpers --

def _make_embedder(**overrides):
    defaults = dict(
        api_key="test-key",
        model="qwen3-vl-embedding",
        embedding_dims=1536,
        openai_base_url="https://ob.example.com/v1",
        project_id="proj-123",
    )
    defaults.update(overrides)
    cfg = OBMassEmbeddingConfig(**defaults)
    return OBMassEmbedding(cfg)


def _mock_response(status_code=200, body=None):
    resp = MagicMock()
    resp.status_code = status_code
    if body is None:
        body = {"data": [{"embedding": [0.1, 0.2, 0.3]}]}
    resp.json.return_value = body
    resp.text = json.dumps(body)
    return resp


# -- embed() tests --

@patch("powermem.integrations.embeddings.ob_mass.httpx.Client")
def test_embed_success(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.post.return_value = _mock_response()

    embedder = _make_embedder()
    result = embedder.embed("Hello world")

    assert result == [0.1, 0.2, 0.3]

    # Verify the request was made correctly
    call_args = mock_client.post.call_args
    url = call_args[0][0]
    assert url == "https://ob.example.com/v1/embeddings"

    kwargs = call_args[1]
    headers = kwargs["headers"]
    assert headers["Authorization"] == "Bearer test-key"
    assert headers["Content-Type"] == "application/json"
    assert headers["X-OB-Project-ID"] == "proj-123"
    assert "X-Request-ID" in headers

    payload = kwargs["json"]
    assert payload["model"] == "qwen3-vl-embedding"
    assert payload["input"] == {"contents": [{"text": "Hello world"}]}
    assert payload["dimensions"] == 1536


@patch("powermem.integrations.embeddings.ob_mass.httpx.Client")
def test_embed_strips_newlines(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.post.return_value = _mock_response()

    embedder = _make_embedder()
    embedder.embed("Hello\nworld\n")

    payload = mock_client.post.call_args[1]["json"]
    assert payload["input"] == {"contents": [{"text": "Hello world "}]}


@patch("powermem.integrations.embeddings.ob_mass.httpx.Client")
def test_embed_custom_request_id(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.post.return_value = _mock_response()

    embedder = _make_embedder(request_id="my-req-id")
    embedder.embed("test")

    headers = mock_client.post.call_args[1]["headers"]
    assert headers["X-Request-ID"] == "my-req-id"


@patch("powermem.integrations.embeddings.ob_mass.httpx.Client")
def test_embed_without_project_id_omits_header(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.post.return_value = _mock_response()

    embedder = _make_embedder(project_id=None)
    embedder.embed("test")

    headers = mock_client.post.call_args[1]["headers"]
    assert "X-OB-Project-ID" not in headers


@patch("powermem.integrations.embeddings.ob_mass.httpx.Client")
def test_embed_auto_generated_request_id(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.post.return_value = _mock_response()

    embedder = _make_embedder()
    embedder.embed("test")

    headers = mock_client.post.call_args[1]["headers"]
    # Should be a valid UUID
    request_id = headers["X-Request-ID"]
    assert len(request_id) == 36  # UUID format


@patch("powermem.integrations.embeddings.ob_mass.httpx.Client")
def test_embed_trailing_slash_stripped(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.post.return_value = _mock_response()

    embedder = _make_embedder(openai_base_url="https://ob.example.com/v1/")
    embedder.embed("test")

    url = mock_client.post.call_args[0][0]
    assert url == "https://ob.example.com/v1/embeddings"


# -- Error handling tests --

@patch("powermem.integrations.embeddings.ob_mass.httpx.Client")
def test_embed_http_error(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    error_body = {"error": {"message": "Invalid model", "code": "invalid_model"}}
    mock_client.post.return_value = _mock_response(status_code=400, body=error_body)

    embedder = _make_embedder()
    with pytest.raises(RuntimeError, match="status=400.*Invalid model"):
        embedder.embed("test")


@patch("powermem.integrations.embeddings.ob_mass.httpx.Client")
def test_embed_http_error_no_json(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    resp = MagicMock()
    resp.status_code = 500
    resp.json.side_effect = Exception("not json")
    resp.text = "Internal Server Error"
    mock_client.post.return_value = resp

    embedder = _make_embedder()
    with pytest.raises(RuntimeError, match="status=500.*Internal Server Error"):
        embedder.embed("test")


@patch("powermem.integrations.embeddings.ob_mass.httpx.Client")
def test_embed_unexpected_response_format(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.post.return_value = _mock_response(body={"result": "unexpected"})

    embedder = _make_embedder()
    with pytest.raises(RuntimeError, match="unexpected format"):
        embedder.embed("test")


@patch("powermem.integrations.embeddings.ob_mass.httpx.Client")
def test_error_message_does_not_contain_api_key(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    error_body = {"error": {"message": "Unauthorized"}}
    mock_client.post.return_value = _mock_response(status_code=401, body=error_body)

    embedder = _make_embedder()
    with pytest.raises(RuntimeError) as exc_info:
        embedder.embed("test")
    assert "test-key" not in str(exc_info.value)


# -- Default value tests --

@patch("powermem.integrations.embeddings.ob_mass.httpx.Client")
def test_default_model_and_dims(mock_client_cls):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.post.return_value = _mock_response()

    cfg = OBMassEmbeddingConfig(
        api_key="test-key",
        openai_base_url="https://ob.example.com/v1",
        project_id="proj-123",
    )
    embedder = OBMassEmbedding(cfg)
    embedder.embed("test")

    payload = mock_client.post.call_args[1]["json"]
    assert payload["model"] == "qwen3-vl-embedding"
    assert payload["dimensions"] == 1536


# -- Factory integration test --

@patch("powermem.integrations.embeddings.ob_mass.httpx.Client")
def test_factory_creates_ob_mass(mock_client_cls):
    """EmbedderFactory.create('ob_mass', ...) should produce an OBMassEmbedding."""
    mock_client_cls.return_value = MagicMock()

    from powermem.integrations.embeddings.factory import EmbedderFactory

    config = {
        "api_key": "test-key",
        "model": "qwen3-vl-embedding",
        "embedding_dims": 1536,
        "openai_base_url": "https://ob.example.com/v1",
        "project_id": "proj-123",
    }
    embedder = EmbedderFactory.create("ob_mass", config, None)
    assert isinstance(embedder, OBMassEmbedding)


# -- Environment variable fallback tests --

@patch("powermem.integrations.embeddings.ob_mass.httpx.Client")
def test_env_var_fallback(mock_client_cls, monkeypatch):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.post.return_value = _mock_response()

    monkeypatch.setenv("OB_MASS_API_KEY", "env-key")
    monkeypatch.setenv("OB_MASS_BASE_URL", "https://env.example.com/v1")
    monkeypatch.setenv("OB_MASS_PROJECT_ID", "env-proj")

    cfg = OBMassEmbeddingConfig()
    embedder = OBMassEmbedding(cfg)
    embedder.embed("test")

    headers = mock_client.post.call_args[1]["headers"]
    assert headers["Authorization"] == "Bearer env-key"
    assert headers["X-OB-Project-ID"] == "env-proj"
    url = mock_client.post.call_args[0][0]
    assert url == "https://env.example.com/v1/embeddings"
