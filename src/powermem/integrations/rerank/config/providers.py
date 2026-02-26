"""
Provider-specific rerank configurations
"""
from typing import Optional

from pydantic import AliasChoices, Field

from powermem.integrations.rerank.config.base import BaseRerankConfig
from powermem.settings import settings_config


class QwenRerankConfig(BaseRerankConfig):
    """Configuration for Qwen (DashScope) rerank service"""
    
    _provider_name = "qwen"
    _class_path = "powermem.integrations.rerank.qwen.QwenRerank"
    
    model_config = settings_config("RERANKER_", extra="forbid", env_file=None)
    
    # Override base fields with Qwen-specific aliases
    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "api_key",  # Must include field name itself!
            "RERANKER_API_KEY",
            "QWEN_API_KEY",
            "DASHSCOPE_API_KEY",
        ),
        description="API key for Qwen rerank service"
    )
    
    model: Optional[str] = Field(
        default="qwen3-rerank",
        description="Qwen rerank model name"
    )
    
    api_base_url: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "api_base_url",
            "RERANKER_API_BASE_URL",
            "QWEN_RERANK_BASE_URL",
            "DASHSCOPE_BASE_URL",
        ),
        description="Base URL for Qwen/DashScope API"
    )


class JinaRerankConfig(BaseRerankConfig):
    """Configuration for Jina AI rerank service"""
    
    _provider_name = "jina"
    _class_path = "powermem.integrations.rerank.jina.JinaRerank"
    
    model_config = settings_config("RERANKER_", extra="forbid", env_file=None)
    
    # Override base fields with Jina-specific aliases
    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "api_key",
            "RERANKER_API_KEY",
            "JINA_API_KEY",
        ),
        description="API key for Jina AI"
    )
    
    model: Optional[str] = Field(
        default="jina-reranker-v3",
        description="Jina rerank model name"
    )
    
    api_base_url: Optional[str] = Field(
        default="https://api.jina.ai/v1/rerank",
        validation_alias=AliasChoices(
            "api_base_url",
            "RERANKER_API_BASE_URL",
            "JINA_API_BASE_URL",
        ),
        description="Base URL for Jina AI rerank API"
    )


class ZaiRerankConfig(BaseRerankConfig):
    """Configuration for Zhipu AI rerank service"""
    
    _provider_name = "zai"
    _class_path = "powermem.integrations.rerank.zai.ZaiRerank"
    
    model_config = settings_config("RERANKER_", extra="forbid", env_file=None)
    
    # Override base fields with Zhipu AI-specific aliases
    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "api_key",
            "RERANKER_API_KEY",
            "ZAI_API_KEY",
        ),
        description="API key for Zhipu AI"
    )
    
    model: Optional[str] = Field(
        default="rerank",
        description="Zhipu AI rerank model name"
    )
    
    api_base_url: Optional[str] = Field(
        default="https://open.bigmodel.cn/api/paas/v4/rerank",
        validation_alias=AliasChoices(
            "api_base_url",
            "RERANKER_API_BASE_URL",
            "ZAI_API_BASE_URL",
        ),
        description="Base URL for Zhipu AI rerank API"
    )


class GenericRerankConfig(BaseRerankConfig):
    """Configuration for generic rerank service"""
    
    _provider_name = "generic"
    _class_path = "powermem.integrations.rerank.generic.GenericRerank"
    
    model_config = settings_config("RERANKER_", extra="forbid", env_file=None)
    
    # Generic uses base class default configuration
