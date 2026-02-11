from typing import Any, Callable, Optional

from pydantic import AliasChoices, Field

from powermem.integrations.llm.config.base import BaseLLMConfig
from powermem.settings import settings_config


class AzureOpenAIConfig(BaseLLMConfig):
    """
    Configuration class for Azure OpenAI-specific parameters.
    Inherits from BaseLLMConfig and adds Azure OpenAI-specific settings.
    """

    _provider_name = "azure"
    _class_path = "powermem.integrations.llm.azure.AzureLLM"

    model_config = settings_config("LLM_", extra="forbid", env_file=None)

    # Override base fields with Azure-specific validation_alias
    api_key: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "api_key",
            "LLM_API_KEY",
            "AZURE_OPENAI_API_KEY",
            "AZURE_API_KEY",
        ),
        description="Azure OpenAI API key"
    )

    # Azure OpenAI-specific fields
    azure_endpoint: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "azure_endpoint",
            "AZURE_ENDPOINT",
            "AZURE_OPENAI_ENDPOINT",
        ),
        description="Azure OpenAI endpoint URL"
    )
    
    api_version: Optional[str] = Field(
        default="2025-01-01-preview",
        validation_alias=AliasChoices(
            "api_version",
            "AZURE_API_VERSION",
        ),
        description="Azure OpenAI API version"
    )
    
    azure_ad_token_provider: Optional[Callable[[], str]] = Field(
        default=None,
        exclude=True,
        description="Callable that returns an Azure AD token"
    )
    
    deployment_name: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices(
            "deployment_name",
            "AZURE_DEPLOYMENT",
        ),
        description="Azure OpenAI deployment name (alias for model)"
    )

    def model_post_init(self, __context: Any) -> None:
        """Initialize fields after model creation."""
        super().model_post_init(__context)
        # Use deployment_name if provided, otherwise use model
        if self.deployment_name:
            self.model = self.deployment_name
