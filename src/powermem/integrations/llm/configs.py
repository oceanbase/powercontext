from typing import Optional

from pydantic import BaseModel, Field, field_validator

from powermem.integrations.llm.factory import LLMFactory


class LLMConfig(BaseModel):
    provider: str = Field(description="Provider of the LLM (e.g., 'ollama', 'openai')", default="openai")
    config: Optional[dict] = Field(description="Configuration for the specific LLM", default={})

    @field_validator("config")
    def validate_config(cls, v, info):
        provider = info.data.get("provider")
        initialized_providers = (
            "openai",
            "ollama",
            "anthropic",
            "openai_structured",
            "azure",
            "gemini",
            "deepseek",
            "vllm",
            "langchain",
            "qwen",
        )
        if provider in initialized_providers or provider in LLMFactory.provider_to_class:
            return v
        else:
            raise ValueError(f"Unsupported LLM provider: {provider}")
