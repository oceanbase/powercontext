from typing import Any, ClassVar, Dict, Optional

from pydantic import Field
from pydantic_settings import BaseSettings

from powermem.settings import settings_config


class BaseEmbedderConfig(BaseSettings):
    """Common embedding configuration shared by all providers."""

    model_config = settings_config("EMBEDDING_", extra="allow", env_file=None)

    _provider_name: ClassVar[Optional[str]] = None
    _class_path: ClassVar[Optional[str]] = None
    _registry: ClassVar[dict[str, type["BaseEmbedderConfig"]]] = {}
    _class_paths: ClassVar[dict[str, str]] = {}

    @classmethod
    def _register_provider(cls) -> None:
        provider = getattr(cls, "_provider_name", None)
        class_path = getattr(cls, "_class_path", None)
        if provider:
            BaseEmbedderConfig._registry[provider] = cls
            if class_path:
                BaseEmbedderConfig._class_paths[provider] = class_path

    def __init_subclass__(cls, **kwargs) -> None:
        super().__init_subclass__(**kwargs)
        cls._register_provider()

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        cls._register_provider()

    @classmethod
    def get_provider_config_cls(cls, provider: str) -> Optional[type["BaseEmbedderConfig"]]:
        return cls._registry.get(provider)

    @classmethod
    def get_provider_class_path(cls, provider: str) -> Optional[str]:
        return cls._class_paths.get(provider)

    @classmethod
    def has_provider(cls, provider: str) -> bool:
        return provider in cls._registry

    model: Optional[Any] = Field(
        default=None,
        description="Embedding model name or provider-specific model object.",
    )
    api_key: Optional[str] = Field(
        default=None,
        description="API key used for provider authentication.",
    )
    embedding_dims: Optional[int] = Field(
        default=None,
        description="Embedding vector dimensions, when configurable by provider.",
    )

    def to_component_dict(self) -> Dict[str, Any]:
        return {
            "provider": self._provider_name,
            "config": self.model_dump(exclude_none=True)
        }
