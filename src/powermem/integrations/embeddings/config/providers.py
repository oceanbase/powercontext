from typing import Any, Dict, Optional, Union

import httpx
from pydantic import AliasChoices, Field

from powermem.integrations.embeddings.config.base import BaseEmbedderConfig
from powermem.settings import settings_config


class OpenAIEmbeddingConfig(BaseEmbedderConfig):
    model_config = settings_config("EMBEDDING_", extra="forbid")

    model: Optional[str] = Field(default=None)
    openai_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "OPENAI_EMBEDDING_BASE_URL",
            "OPEN_EMBEDDING_BASE_URL",
        ),
    )


class QwenEmbeddingConfig(BaseEmbedderConfig):
    model_config = settings_config("EMBEDDING_", extra="forbid")

    model: Optional[str] = Field(default=None)
    dashscope_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("QWEN_EMBEDDING_BASE_URL"),
    )
    memory_add_embedding_type: Optional[str] = Field(default=None)
    memory_update_embedding_type: Optional[str] = Field(default=None)
    memory_search_embedding_type: Optional[str] = Field(default=None)


class SiliconFlowEmbeddingConfig(BaseEmbedderConfig):
    model_config = settings_config("EMBEDDING_", extra="forbid")

    model: Optional[str] = Field(default=None)
    siliconflow_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("SILICONFLOW_EMBEDDING_BASE_URL"),
    )


class HuggingFaceEmbeddingConfig(BaseEmbedderConfig):
    model_config = settings_config("EMBEDDING_", extra="forbid")

    model: Optional[str] = Field(default=None)
    huggingface_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("HUGGINFACE_EMBEDDING_BASE_URL"),
    )
    model_kwargs: Dict[str, Any] = Field(default_factory=dict)


class OllamaEmbeddingConfig(BaseEmbedderConfig):
    model_config = settings_config("EMBEDDING_", extra="forbid")

    model: Optional[str] = Field(default=None)
    ollama_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("OLLAMA_EMBEDDING_BASE_URL"),
    )


class LMStudioEmbeddingConfig(BaseEmbedderConfig):
    model_config = settings_config("EMBEDDING_", extra="forbid")

    model: Optional[str] = Field(default=None)
    lmstudio_base_url: Optional[str] = Field(
        default="http://localhost:1234/v1",
        validation_alias=AliasChoices("LMSTUDIO_EMBEDDING_BASE_URL"),
    )


class AzureOpenAIEmbeddingConfig(BaseEmbedderConfig):
    model_config = settings_config("EMBEDDING_", extra="forbid")

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
    model_config = settings_config("EMBEDDING_", extra="forbid")

    model: Optional[str] = Field(default=None)
    output_dimensionality: Optional[int] = Field(default=None)


class VertexAIEmbeddingConfig(BaseEmbedderConfig):
    model_config = settings_config("EMBEDDING_", extra="forbid")

    model: Optional[str] = Field(default=None)
    vertex_credentials_json: Optional[str] = Field(default=None)
    memory_add_embedding_type: Optional[str] = Field(default=None)
    memory_update_embedding_type: Optional[str] = Field(default=None)
    memory_search_embedding_type: Optional[str] = Field(default=None)


class TogetherEmbeddingConfig(BaseEmbedderConfig):
    model_config = settings_config("EMBEDDING_", extra="forbid")

    model: Optional[str] = Field(default=None)


class AWSBedrockEmbeddingConfig(BaseEmbedderConfig):
    model_config = settings_config("EMBEDDING_", extra="forbid")

    model: Optional[str] = Field(default=None)
    aws_access_key_id: Optional[str] = Field(default=None)
    aws_secret_access_key: Optional[str] = Field(default=None)
    aws_region: Optional[str] = Field(default=None)


class ZaiEmbeddingConfig(BaseEmbedderConfig):
    model_config = settings_config("EMBEDDING_", extra="forbid")

    model: Optional[str] = Field(default=None)
    zai_base_url: Optional[str] = Field(default=None)


class LangchainEmbeddingConfig(BaseEmbedderConfig):
    model_config = settings_config("EMBEDDING_", extra="forbid")

    model: Optional[Any] = Field(default=None)


class CustomEmbeddingConfig(BaseEmbedderConfig):
    model_config = settings_config("EMBEDDING_", extra="allow")


PROVIDER_REGISTRY: dict[str, dict[str, object]] = {}
PROVIDER_TO_CONFIG: dict[str, type[BaseEmbedderConfig]] = {}
PROVIDER_TO_CLASS: dict[str, str] = {}


def register_provider(name: str, config_cls: type[BaseEmbedderConfig], class_path: str) -> None:
    PROVIDER_REGISTRY[name] = {
        "config": config_cls,
        "class_path": class_path,
    }
    PROVIDER_TO_CONFIG[name] = config_cls
    PROVIDER_TO_CLASS[name] = class_path


register_provider(
    "openai",
    OpenAIEmbeddingConfig,
    "powermem.integrations.embeddings.openai.OpenAIEmbedding",
)
register_provider(
    "siliconflow",
    SiliconFlowEmbeddingConfig,
    "powermem.integrations.embeddings.siliconflow.SiliconFlowEmbedding",
)
register_provider(
    "ollama",
    OllamaEmbeddingConfig,
    "powermem.integrations.embeddings.ollama.OllamaEmbedding",
)
register_provider(
    "huggingface",
    HuggingFaceEmbeddingConfig,
    "powermem.integrations.embeddings.huggingface.HuggingFaceEmbedding",
)
register_provider(
    "azure_openai",
    AzureOpenAIEmbeddingConfig,
    "powermem.integrations.embeddings.azure_openai.AzureOpenAIEmbedding",
)
register_provider(
    "gemini",
    GeminiEmbeddingConfig,
    "powermem.integrations.embeddings.gemini.GoogleGenAIEmbedding",
)
register_provider(
    "vertexai",
    VertexAIEmbeddingConfig,
    "powermem.integrations.embeddings.vertexai.VertexAIEmbedding",
)
register_provider(
    "together",
    TogetherEmbeddingConfig,
    "powermem.integrations.embeddings.together.TogetherEmbedding",
)
register_provider(
    "lmstudio",
    LMStudioEmbeddingConfig,
    "powermem.integrations.embeddings.lmstudio.LMStudioEmbedding",
)
register_provider(
    "langchain",
    LangchainEmbeddingConfig,
    "powermem.integrations.embeddings.langchain.LangchainEmbedding",
)
register_provider(
    "aws_bedrock",
    AWSBedrockEmbeddingConfig,
    "powermem.integrations.embeddings.aws_bedrock.AWSBedrockEmbedding",
)
register_provider(
    "qwen",
    QwenEmbeddingConfig,
    "powermem.integrations.embeddings.qwen.QwenEmbedding",
)
register_provider(
    "zai",
    ZaiEmbeddingConfig,
    "powermem.integrations.embeddings.zai.ZaiEmbedding",
)
