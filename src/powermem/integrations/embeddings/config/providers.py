from typing import Any, Dict, Optional, Union

import httpx
from pydantic import AliasChoices, Field

from powermem.integrations.embeddings.config.base import BaseEmbedderConfig
from powermem.settings import settings_config


class OpenAIEmbeddingConfig(BaseEmbedderConfig):
    _provider_name = "openai"
    _class_path = "powermem.integrations.embeddings.openai.OpenAIEmbedding"

    model_config = settings_config("EMBEDDING_", extra="forbid", env_file=None)

    model: Optional[str] = Field(default=None)
    openai_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPENAI_EMBEDDING_BASE_URL",
            "OPEN_EMBEDDING_BASE_URL",
        ),
    )


class QwenEmbeddingConfig(BaseEmbedderConfig):
    _provider_name = "qwen"
    _class_path = "powermem.integrations.embeddings.qwen.QwenEmbedding"

    model_config = settings_config("EMBEDDING_", extra="forbid", env_file=None)

    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "api_key",
            "QWEN_API_KEY",
            "DASHSCOPE_API_KEY",
            "EMBEDDING_API_KEY",
        ),
    )
    model: Optional[str] = Field(default=None)
    dashscope_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "dashscope_base_url",
            "QWEN_EMBEDDING_BASE_URL",
        ),
    )
    memory_add_embedding_type: Optional[str] = Field(default=None)
    memory_update_embedding_type: Optional[str] = Field(default=None)
    memory_search_embedding_type: Optional[str] = Field(default=None)


class SiliconFlowEmbeddingConfig(BaseEmbedderConfig):
    _provider_name = "siliconflow"
    _class_path = "powermem.integrations.embeddings.siliconflow.SiliconFlowEmbedding"

    model_config = settings_config("EMBEDDING_", extra="forbid", env_file=None)

    model: Optional[str] = Field(default=None)
    siliconflow_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("SILICONFLOW_EMBEDDING_BASE_URL"),
    )


class HuggingFaceEmbeddingConfig(BaseEmbedderConfig):
    _provider_name = "huggingface"
    _class_path = "powermem.integrations.embeddings.huggingface.HuggingFaceEmbedding"

    model_config = settings_config("EMBEDDING_", extra="forbid", env_file=None)

    model: Optional[str] = Field(default=None)
    huggingface_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("HUGGINFACE_EMBEDDING_BASE_URL"),
    )
    model_kwargs: Dict[str, Any] = Field(default_factory=dict)


class OllamaEmbeddingConfig(BaseEmbedderConfig):
    _provider_name = "ollama"
    _class_path = "powermem.integrations.embeddings.ollama.OllamaEmbedding"

    model_config = settings_config("EMBEDDING_", extra="forbid", env_file=None)

    model: Optional[str] = Field(default=None)
    ollama_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("OLLAMA_EMBEDDING_BASE_URL"),
    )


class LMStudioEmbeddingConfig(BaseEmbedderConfig):
    _provider_name = "lmstudio"
    _class_path = "powermem.integrations.embeddings.lmstudio.LMStudioEmbedding"

    model_config = settings_config("EMBEDDING_", extra="forbid", env_file=None)

    model: Optional[str] = Field(default=None)
    lmstudio_base_url: Optional[str] = Field(
        default="http://localhost:1234/v1",
        validation_alias=AliasChoices("LMSTUDIO_EMBEDDING_BASE_URL"),
    )


class AzureOpenAIEmbeddingConfig(BaseEmbedderConfig):
    _provider_name = "azure_openai"
    _class_path = "powermem.integrations.embeddings.azure_openai.AzureOpenAIEmbedding"

    model_config = settings_config("EMBEDDING_", extra="forbid", env_file=None)

    model: Optional[str] = Field(default=None)
    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "AZURE_OPENAI_API_KEY",
            "AZURE_API_KEY",
            "EMBEDDING_API_KEY",
        ),
    )
    azure_deployment: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AZURE_DEPLOYMENT"),
    )
    azure_endpoint: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AZURE_ENDPOINT", "AZURE_OPENAI_ENDPOINT"),
    )
    api_version: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("AZURE_API_VERSION"),
    )
    default_headers: Optional[Dict[str, str]] = Field(default=None)
    http_client_proxies: Optional[Union[Dict[str, Any], str]] = Field(default=None)
    http_client: Optional[httpx.Client] = Field(default=None, exclude=True)

    def model_post_init(self, __context: Any) -> None:
        if self.http_client_proxies:
            self.http_client = httpx.Client(proxies=self.http_client_proxies)


class GeminiEmbeddingConfig(BaseEmbedderConfig):
    _provider_name = "gemini"
    _class_path = "powermem.integrations.embeddings.gemini.GoogleGenAIEmbedding"

    model_config = settings_config("EMBEDDING_", extra="forbid", env_file=None)

    model: Optional[str] = Field(default=None)
    output_dimensionality: Optional[int] = Field(default=None)


class VertexAIEmbeddingConfig(BaseEmbedderConfig):
    _provider_name = "vertexai"
    _class_path = "powermem.integrations.embeddings.vertexai.VertexAIEmbedding"

    model_config = settings_config("EMBEDDING_", extra="forbid", env_file=None)

    model: Optional[str] = Field(default=None)
    vertex_credentials_json: Optional[str] = Field(default=None)
    memory_add_embedding_type: Optional[str] = Field(default=None)
    memory_update_embedding_type: Optional[str] = Field(default=None)
    memory_search_embedding_type: Optional[str] = Field(default=None)


class TogetherEmbeddingConfig(BaseEmbedderConfig):
    _provider_name = "together"
    _class_path = "powermem.integrations.embeddings.together.TogetherEmbedding"

    model_config = settings_config("EMBEDDING_", extra="forbid", env_file=None)

    model: Optional[str] = Field(default=None)


class AWSBedrockEmbeddingConfig(BaseEmbedderConfig):
    _provider_name = "aws_bedrock"
    _class_path = "powermem.integrations.embeddings.aws_bedrock.AWSBedrockEmbedding"

    model_config = settings_config("EMBEDDING_", extra="forbid", env_file=None)

    model: Optional[str] = Field(default=None)
    aws_access_key_id: Optional[str] = Field(default=None)
    aws_secret_access_key: Optional[str] = Field(default=None)
    aws_region: Optional[str] = Field(default=None)


class ZaiEmbeddingConfig(BaseEmbedderConfig):
    _provider_name = "zai"
    _class_path = "powermem.integrations.embeddings.zai.ZaiEmbedding"

    model_config = settings_config("EMBEDDING_", extra="forbid", env_file=None)

    model: Optional[str] = Field(default=None)
    zai_base_url: Optional[str] = Field(default=None)


class LangchainEmbeddingConfig(BaseEmbedderConfig):
    _provider_name = "langchain"
    _class_path = "powermem.integrations.embeddings.langchain.LangchainEmbedding"

    model_config = settings_config("EMBEDDING_", extra="forbid", env_file=None)

    model: Optional[Any] = Field(default=None)


class MockEmbeddingConfig(BaseEmbedderConfig):
    _provider_name = "mock"
    _class_path = "powermem.integrations.embeddings.mock.MockEmbeddings"

    model_config = settings_config("EMBEDDING_", extra="allow", env_file=None)


class CustomEmbeddingConfig(BaseEmbedderConfig):
    model_config = settings_config("EMBEDDING_", extra="allow", env_file=None)
