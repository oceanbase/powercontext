"""
Base configuration for rerank models
"""
from typing import Any, ClassVar, Dict, Optional, Union

try:
    import httpx
except ImportError:
    httpx = None

from pydantic import Field
from pydantic_settings import BaseSettings

from powermem.settings import settings_config


class BaseRerankConfig(BaseSettings):
    """Base configuration for rerank models
    
    This class uses pydantic-settings to support automatic loading from environment variables.
    All rerank provider configurations should inherit from this base class.
    
    Environment Variables:
        RERANK_ENABLED: Whether to enable reranker (default: False)
        RERANK_MODEL: The rerank model to use
        RERANK_API_KEY: API key for the rerank service
        RERANK_API_BASE_URL: Base URL for the rerank API endpoint
        RERANK_TOP_N: Default number of top results to return
    """

    model_config = settings_config("RERANK_", extra="allow", env_file=None)

    # Class variables for provider registration
    _provider_name: ClassVar[Optional[str]] = None
    _class_path: ClassVar[Optional[str]] = None
    _registry: ClassVar[dict[str, type["BaseRerankConfig"]]] = {}
    _class_paths: ClassVar[dict[str, str]] = {}

    # Configuration fields
    enabled: bool = Field(
        default=False,
        description="Whether to enable reranker"
    )
    model: Optional[str] = Field(
        default=None,
        description="The rerank model identifier to use"
    )
    api_key: Optional[str] = Field(
        default=None,
        description="API key for the rerank provider"
    )
    api_base_url: Optional[str] = Field(
        default=None,
        description="Base URL for the rerank API endpoint"
    )
    top_n: Optional[int] = Field(
        default=None,
        description="Default number of top results to return (can be overridden at runtime)"
    )
    http_client_proxies: Optional[Union[Dict, str]] = Field(
        default=None,
        description="Proxy settings for HTTP client"
    )
    http_client: Optional[Any] = Field(  # httpx.Client type
        default=None,
        exclude=True,
        description="HTTP client instance"
    )

    @classmethod
    def _register_provider(cls) -> None:
        """Register provider in the global registry."""
        provider = getattr(cls, "_provider_name", None)
        class_path = getattr(cls, "_class_path", None)
        if provider:
            BaseRerankConfig._registry[provider] = cls
            if class_path:
                BaseRerankConfig._class_paths[provider] = class_path

    def __init_subclass__(cls, **kwargs) -> None:
        """Called when a class inherits from BaseRerankConfig."""
        super().__init_subclass__(**kwargs)
        cls._register_provider()

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs) -> None:
        """Called by Pydantic when a class inherits from BaseRerankConfig."""
        super().__pydantic_init_subclass__(**kwargs)
        cls._register_provider()

    @classmethod
    def get_provider_config_cls(cls, provider: str) -> Optional[type["BaseRerankConfig"]]:
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
        if self.http_client_proxies and not self.http_client and httpx:
            self.http_client = httpx.Client(proxies=self.http_client_proxies)

    def to_component_dict(self) -> Dict[str, Any]:
        """Convert config to component dict format matching RerankConfig structure.
        
        Returns:
            Dict matching RerankConfig schema with 'enabled', 'provider', 'config' fields
        """
        return {
            "enabled": self.enabled,
            "provider": self._provider_name,
            "config": self.model_dump(exclude_none=True)
        }

