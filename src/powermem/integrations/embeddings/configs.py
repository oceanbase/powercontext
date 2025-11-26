from typing import Optional

from pydantic import BaseModel, Field, field_validator

from powermem.integrations.embeddings.factory import EmbedderFactory


class EmbedderConfig(BaseModel):
    provider: str = Field(
        description="Provider of the embedding model (e.g., 'ollama', 'openai')",
        default="openai",
    )
    config: Optional[dict] = Field(description="Configuration for the specific embedding model", default={})

    @field_validator("config")
    def validate_config(cls, v, values):
        provider = values.data.get("provider")
        initialized_providers = [
            "openai",
            "ollama",
            "huggingface",
            "azure_openai",
            "gemini",
            "vertexai",
            "together",
            "lmstudio",
            "langchain",
            "aws_bedrock",
            "qwen",
        ]
        if provider in initialized_providers or provider in EmbedderFactory.provider_to_class or provider == "mock":
            return v
        else:
            raise ValueError(f"Unsupported embedding provider: {provider}")
