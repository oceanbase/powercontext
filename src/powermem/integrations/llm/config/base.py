from typing import Any, ClassVar, Dict, Optional, Union

import httpx
from pydantic import Field
from pydantic_settings import BaseSettings

from powermem.settings import settings_config


class BaseLLMConfig(BaseSettings):
    """
    Base configuration for LLMs with only common parameters.
    Provider-specific configurations should be handled by separate config classes.

    This class contains only the parameters that are common across all LLM providers.
    For provider-specific parameters, use the appropriate provider config class.
    
    Now uses pydantic-settings for automatic environment variable loading.
    """

    model_config = settings_config("LLM_", extra="allow", env_file=None)

    # Registry for provider configurations
    _provider_name: ClassVar[Optional[str]] = None
    _class_path: ClassVar[Optional[str]] = None
    _registry: ClassVar[dict[str, type["BaseLLMConfig"]]] = {}
    _class_paths: ClassVar[dict[str, str]] = {}

    # Field definitions
    model: Optional[Union[str, Dict]] = Field(
        default=None,
        description="The model identifier to use (e.g., 'gpt-4o-mini', 'claude-3-5-sonnet-20240620'). "
                    "Defaults to None (will be set by provider-specific configs)"
    )
    
    temperature: float = Field(
        default=0.1,
        description="Controls the randomness of the model's output. "
                    "Higher values (closer to 1) make output more random, lower values make it more deterministic. "
                    "Range: 0.0 to 2.0"
    )
    
    api_key: Optional[str] = Field(
        default=None,
        description="API key for the LLM provider. If None, will try to get from environment variables"
    )
    
    max_tokens: int = Field(
        default=2000,
        description="Maximum number of tokens to generate in the response. "
                    "Range: 1 to 4096 (varies by model)"
    )
    
    top_p: float = Field(
        default=0.1,
        description="Nucleus sampling parameter. Controls diversity via nucleus sampling. "
                    "Higher values (closer to 1) make word selection more diverse. "
                    "Range: 0.0 to 1.0"
    )
    
    top_k: int = Field(
        default=1,
        description="Top-k sampling parameter. Limits the number of tokens considered for each step. "
                    "Higher values make word selection more diverse. "
                    "Range: 1 to 40"
    )
    
    enable_vision: bool = Field(
        default=False,
        description="Whether to enable vision capabilities for the model. "
                    "Only applicable to vision-enabled models"
    )
    
    vision_details: Optional[str] = Field(
        default="auto",
        description="Level of detail for vision processing. Options: 'low', 'high', 'auto'"
    )
    
    http_client_proxies: Optional[Union[Dict, str]] = Field(
        default=None,
        description="Proxy settings for HTTP client. Can be a dict or string"
    )
    
    http_client: Optional[httpx.Client] = Field(
        default=None,
        exclude=True,
        description="HTTP client instance (automatically initialized from http_client_proxies)"
    )

    @classmethod
    def _register_provider(cls) -> None:
        """Register provider in the global registry."""
        provider = getattr(cls, "_provider_name", None)
        class_path = getattr(cls, "_class_path", None)
        if provider:
            BaseLLMConfig._registry[provider] = cls
            if class_path:
                BaseLLMConfig._class_paths[provider] = class_path

    def __init_subclass__(cls, **kwargs) -> None:
        """Called when a class inherits from BaseLLMConfig."""
        super().__init_subclass__(**kwargs)
        cls._register_provider()

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs) -> None:
        """Called by Pydantic when a class inherits from BaseLLMConfig."""
        super().__pydantic_init_subclass__(**kwargs)
        cls._register_provider()

    @classmethod
    def get_provider_config_cls(cls, provider: str) -> Optional[type["BaseLLMConfig"]]:
        """Get the config class for a specific provider."""
        return cls._registry.get(provider)

    @classmethod
    def get_provider_class_path(cls, provider: str) -> Optional[str]:
        """Get the class path for a specific provider."""
        return cls._class_paths.get(provider)

    @classmethod
    def has_provider(cls, provider: str) -> bool:
        """Check if a provider is registered."""
        return provider in cls._registry

    def model_post_init(self, __context: Any) -> None:
        """Initialize http_client after model creation."""
        if self.http_client_proxies and not self.http_client:
            self.http_client = httpx.Client(proxies=self.http_client_proxies)

    def to_component_dict(self) -> Dict[str, Any]:
        """
        Convert config to component dictionary format.
        
        This method is used by MemoryConfig.to_dict() to serialize
        LLM configuration in a consistent format.
        
        Returns:
            Dict with 'provider' and 'config' keys
        """
        return {
            "provider": self._provider_name,
            "config": self.model_dump(exclude_none=True)
        }
