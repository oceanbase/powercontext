from typing import Any, Callable, Dict, Optional

from powermem.integrations.llm.config.base import BaseLLMConfig


class AzureOpenAIConfig(BaseLLMConfig):
    """
    Configuration class for Azure OpenAI-specific parameters.
    Inherits from BaseLLMConfig and adds Azure OpenAI-specific settings.
    """

    def __init__(
            self,
            # Base parameters
            model: Optional[str] = None,
            temperature: float = 0.1,
            api_key: Optional[str] = None,
            max_tokens: int = 2000,
            top_p: float = 0.1,
            top_k: int = 1,
            enable_vision: bool = False,
            vision_details: Optional[str] = "auto",
            http_client_proxies: Optional[dict] = None,
            # Azure OpenAI-specific parameters
            azure_endpoint: Optional[str] = None,
            api_version: Optional[str] = "2025-01-01-preview",
            azure_ad_token_provider: Optional[Callable[[], str]] = None,
            deployment_name: Optional[str] = None,
    ):
        """
        Initialize Azure OpenAI configuration.

        Args:
            model: Azure OpenAI deployment name to use, defaults to None
            temperature: Controls randomness, defaults to 0.1
            api_key: Azure OpenAI API key, defaults to None
            max_tokens: Maximum tokens to generate, defaults to 2000
            top_p: Nucleus sampling parameter, defaults to 0.1
            top_k: Top-k sampling parameter, defaults to 1
            enable_vision: Enable vision capabilities, defaults to False
            vision_details: Vision detail level, defaults to "auto"
            http_client_proxies: HTTP client proxy settings, defaults to None
            azure_endpoint: Azure OpenAI endpoint URL, defaults to None
            api_version: Azure OpenAI API version, defaults to "2025-01-01-preview"
            azure_ad_token_provider: Callable that returns an Azure AD token, defaults to None
            deployment_name: Azure OpenAI deployment name (alias for model), defaults to None
        """
        # Initialize base parameters
        super().__init__(
            model=model,
            temperature=temperature,
            api_key=api_key,
            max_tokens=max_tokens,
            top_p=top_p,
            top_k=top_k,
            enable_vision=enable_vision,
            vision_details=vision_details,
            http_client_proxies=http_client_proxies,
        )

        # Azure OpenAI-specific parameters
        self.azure_endpoint = azure_endpoint
        self.api_version = api_version
        self.azure_ad_token_provider = azure_ad_token_provider
        # Use deployment_name if provided, otherwise use model
        if deployment_name:
            self.model = deployment_name
